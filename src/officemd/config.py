"""Konfiguration und API-Keys.

Konfiguration: ``~/.config/officemd/config.json`` (oder ``$OFFICEMD_CONFIG_DIR``).

API-Keys, in dieser Reihenfolge:

1. Umgebungsvariable ``OFFICEMD_<ANBIETER>_API_KEY``, dann ``ANTHROPIC_API_KEY`` bzw.
   ``OPENAI_API_KEY``;
2. macOS-Schlüsselbund (Dienst ``officemd``, Konto = Anbieter);
3. sonst eine Datei ``credentials.json`` mit Rechten 0600 im Konfigurationsordner.

Ein Key steht nie in Argumenten, Logs oder JSON-Ausgaben. Beim Speichern im Schlüsselbund
geht er per stdin an ``security -i``, damit er nicht in der Prozessliste auftaucht.
"""
from __future__ import annotations

import copy
import json
import os
import platform
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

PROVIDERS = ("anthropic", "openai")
ENV_KEYS = {
    "anthropic": ("OFFICEMD_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
    "openai": ("OFFICEMD_OPENAI_API_KEY", "OPENAI_API_KEY"),
}
KEYCHAIN_SERVICE = "officemd"

DEFAULTS: Dict[str, Any] = {
    "provider": None,
    "mode": "graph",
    "depth": "standard",
    "max_input_chars": 2_000_000,
    "section_chars": 120_000,
    "repair_rounds": 2,
    "anthropic": {"model": "claude-opus-5-5", "effort": "high", "fallbacks": True},
    "openai": {"model": "gpt-6-astra", "effort": "high"},
}

CHOICES = {
    "provider": (None, "anthropic", "openai"),
    "mode": ("graph", "raw"),
    "depth": ("quick", "standard", "deep"),
    "anthropic.effort": ("low", "medium", "high", "xhigh", "max"),
    "openai.effort": ("low", "medium", "high", "xhigh", "max"),
}


class ConfigError(Exception):
    pass


def config_dir() -> Path:
    override = os.environ.get("OFFICEMD_CONFIG_DIR")
    return Path(override) if override else Path.home() / ".config" / "officemd"


def _merge(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load() -> Dict[str, Any]:
    path = config_dir() / "config.json"
    if not path.is_file():
        return copy.deepcopy(DEFAULTS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"Konfiguration nicht lesbar ({path}): {exc}") from exc
    return _merge(DEFAULTS, data if isinstance(data, dict) else {})


def save(cfg: Dict[str, Any]) -> Path:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "config.json"
    tmp = directory / ".config.json.tmp"
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def set_value(cfg: Dict[str, Any], dotted: str, raw: str) -> Dict[str, Any]:
    keys = dotted.split(".")
    if len(keys) > 2 or (keys[0] not in DEFAULTS):
        raise ConfigError(f"Unbekannter Schlüssel {dotted!r}. Erlaubt: {', '.join(settable_keys())}")
    if len(keys) == 2 and (keys[0] not in PROVIDERS or keys[1] not in DEFAULTS[keys[0]]):
        raise ConfigError(f"Unbekannter Schlüssel {dotted!r}. Erlaubt: {', '.join(settable_keys())}")
    default = DEFAULTS[keys[0]] if len(keys) == 1 else DEFAULTS[keys[0]][keys[1]]
    if isinstance(default, bool):
        if raw.lower() not in ("true", "false", "ja", "nein", "1", "0"):
            raise ConfigError(f"{dotted} erwartet true oder false")
        value: Any = raw.lower() in ("true", "ja", "1")
    elif isinstance(default, int):
        try:
            value = int(raw)
        except ValueError as exc:
            raise ConfigError(f"{dotted} erwartet eine Zahl") from exc
        if value < 0:
            raise ConfigError(f"{dotted} darf nicht negativ sein")
    else:
        value = None if raw.lower() in ("none", "null", "") and dotted == "provider" else raw
    allowed = CHOICES.get(dotted)
    if allowed and value not in allowed:
        raise ConfigError(f"{dotted} muss einer der Werte sein: {', '.join(str(a) for a in allowed if a)}")
    if len(keys) == 1:
        cfg[keys[0]] = value
    else:
        cfg.setdefault(keys[0], {})[keys[1]] = value
    return cfg


def settable_keys():
    for key, value in DEFAULTS.items():
        if isinstance(value, dict):
            for sub in value:
                yield f"{key}.{sub}"
        else:
            yield key


# -- API-Keys ---------------------------------------------------------------------

def _use_keychain() -> bool:
    if os.environ.get("OFFICEMD_KEYCHAIN", "1") == "0":
        return False
    return platform.system() == "Darwin" and shutil.which("security") is not None


def _check_provider(provider: str) -> None:
    if provider not in PROVIDERS:
        raise ConfigError(f"Unbekannter Anbieter {provider!r}. Erlaubt: {', '.join(PROVIDERS)}")


def _validate_key(key: str) -> str:
    key = key.strip()
    if not key:
        raise ConfigError("Leerer API-Key")
    if any(ch.isspace() or ch in "\"'\\" for ch in key) or len(key) > 512:
        raise ConfigError("API-Key enthält unerlaubte Zeichen")
    return key


def _credentials_file() -> Path:
    return config_dir() / "credentials.json"


def _read_file_keys() -> Dict[str, str]:
    path = _credentials_file()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_file_keys(keys: Dict[str, str]) -> None:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = _credentials_file()
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(keys, handle)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def get_key(provider: str) -> Tuple[Optional[str], str]:
    """(Key, Herkunft) mit Herkunft ``env``, ``keychain``, ``file`` oder ``none``."""
    _check_provider(provider)
    for name in ENV_KEYS[provider]:
        value = os.environ.get(name)
        if value:
            return value.strip(), "env"
    if _use_keychain():
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", provider, "-w"],
            capture_output=True, text=True)
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip(), "keychain"
        return None, "none"
    value = _read_file_keys().get(provider)
    return (value, "file") if value else (None, "none")


def set_key(provider: str, key: str) -> str:
    _check_provider(provider)
    key = _validate_key(key)
    if _use_keychain():
        command = f'add-generic-password -U -s {KEYCHAIN_SERVICE} -a {provider} -l "OfficeMD {provider}" -w "{key}"\n'
        proc = subprocess.run(["security", "-i"], input=command, capture_output=True, text=True)
        if proc.returncode != 0 or "error" in proc.stderr.lower():
            raise ConfigError("Schlüsselbund hat den Key nicht gespeichert: " + proc.stderr.strip())
        return "keychain"
    keys = _read_file_keys()
    keys[provider] = key
    _write_file_keys(keys)
    return "file"


def delete_key(provider: str) -> bool:
    _check_provider(provider)
    if _use_keychain():
        proc = subprocess.run(["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", provider],
                              capture_output=True, text=True)
        return proc.returncode == 0
    keys = _read_file_keys()
    removed = keys.pop(provider, None) is not None
    if removed:
        _write_file_keys(keys)
    return removed


def sdk_available(provider: str) -> bool:
    module = {"anthropic": "anthropic", "openai": "openai"}[provider]
    try:
        __import__(module)
        return True
    except ImportError:
        return False


def describe(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Zustand ohne Geheimnisse, auch für die App."""
    import sys

    return {
        "config_path": str(config_dir() / "config.json"),
        "provider": cfg.get("provider"),
        "mode": cfg.get("mode"),
        "depth": cfg.get("depth"),
        "max_input_chars": cfg.get("max_input_chars"),
        "section_chars": cfg.get("section_chars"),
        "repair_rounds": cfg.get("repair_rounds"),
        "providers": {
            p: {**cfg.get(p, {}), "key_source": get_key(p)[1], "sdk_available": sdk_available(p)}
            for p in PROVIDERS
        },
        "python": sys.version.split()[0],
        "key_store": "keychain" if _use_keychain() else "file",
    }
