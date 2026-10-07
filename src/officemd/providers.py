"""Modellanbieter: Anthropic und OpenAI über ihre offiziellen Python-SDKs.

Beide liefern über ``generate(system, user, schema)`` den JSON-Text der Antwort und ein paar
Metadaten (Modell, Token, Request-ID). Die SDKs sind optional (``pip install 'officemd[ai]'``,
Python 3.10 oder neuer); ohne sie funktioniert alles außer der KI-Kompilierung.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from . import config

TIMEOUT_SECONDS = 900.0
MAX_OUTPUT_TOKENS = 64_000
SCHEMA_NAME = "officemd_knowledge"


class ProviderError(Exception):
    pass


class Provider:
    name = ""
    model = ""

    def generate(self, system: str, user: str, schema: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        raise NotImplementedError


# -- Anthropic ----------------------------------------------------------------------

class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str, effort: str = "high", fallbacks: bool = True) -> None:
        try:
            import anthropic
        except ImportError as exc:
            raise ProviderError(_missing_sdk("anthropic")) from exc
        self._sdk = anthropic
        self.client = anthropic.Anthropic(api_key=api_key, timeout=TIMEOUT_SECONDS)
        self.model = model
        self.effort = effort
        self.fallbacks = fallbacks

    def request_kwargs(self, system: str, user: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": MAX_OUTPUT_TOKENS,
            # Regeln und Profil sind über alle Dokumente gleich: Cache spart Kosten.
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "output_config": {"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
            "messages": [{"role": "user", "content": user}],
        }
        if self.fallbacks:
            # Lehnt das Modell aus Sicherheitsgründen ab, übernimmt serverseitig ein anderes.
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
        return kwargs

    def generate(self, system: str, user: str, schema: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        sdk = self._sdk
        kwargs = self.request_kwargs(system, user, schema)
        messages = self.client.beta.messages if "betas" in kwargs else self.client.messages
        try:
            with messages.stream(**kwargs) as stream:
                message = stream.get_final_message()
        except sdk.AuthenticationError as exc:
            raise ProviderError("Anthropic lehnt den API-Key ab. Key prüfen: officemd config set-key anthropic") from exc
        except sdk.PermissionDeniedError as exc:
            raise ProviderError(f"Anthropic verweigert den Zugriff: {exc.message}") from exc
        except sdk.NotFoundError as exc:
            raise ProviderError(f"Modell {self.model!r} bei Anthropic nicht gefunden: {exc.message}") from exc
        except sdk.RateLimitError as exc:
            raise ProviderError("Anthropic: Rate-Limit erreicht, später erneut versuchen.") from exc
        except sdk.APIStatusError as exc:
            raise ProviderError(f"Anthropic-Fehler {exc.status_code}: {exc.message}") from exc
        except sdk.APIConnectionError as exc:
            raise ProviderError("Keine Verbindung zu Anthropic.") from exc
        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise ProviderError(f"Anthropic hat die Anfrage abgelehnt (Kategorie: {category}).")
        if message.stop_reason == "max_tokens":
            raise ProviderError("Antwort wurde am Token-Limit abgeschnitten. Dokument ist für einen Lauf zu groß.")
        text = next((block.text for block in message.content if block.type == "text"), None)
        if text is None:
            raise ProviderError("Anthropic hat keinen Text geliefert.")
        usage = getattr(message, "usage", None)
        return text, {
            "provider": self.name,
            "model": getattr(message, "model", self.model),
            "request_id": getattr(message, "_request_id", None),
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
        }


# -- OpenAI ---------------------------------------------------------------------------

class OpenAIProvider(Provider):
    name = "openai"

    def __init__(self, api_key: str, model: str, effort: str = "high") -> None:
        try:
            import openai
        except ImportError as exc:
            raise ProviderError(_missing_sdk("openai")) from exc
        self._sdk = openai
        self.client = openai.OpenAI(api_key=api_key, timeout=TIMEOUT_SECONDS)
        self.model = model
        self.effort = effort

    def request_kwargs(self, system: str, user: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "model": self.model,
            "instructions": system,
            "input": user,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "reasoning": {"effort": self.effort},
            "text": {"format": {"type": "json_schema", "name": SCHEMA_NAME, "strict": True, "schema": schema}},
        }

    def generate(self, system: str, user: str, schema: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        sdk = self._sdk
        try:
            response = self.client.responses.create(**self.request_kwargs(system, user, schema))
        except sdk.AuthenticationError as exc:
            raise ProviderError("OpenAI lehnt den API-Key ab. Key prüfen: officemd config set-key openai") from exc
        except sdk.PermissionDeniedError as exc:
            raise ProviderError(f"OpenAI verweigert den Zugriff: {exc}") from exc
        except sdk.NotFoundError as exc:
            raise ProviderError(f"Modell {self.model!r} bei OpenAI nicht gefunden.") from exc
        except sdk.RateLimitError as exc:
            raise ProviderError("OpenAI: Rate-Limit erreicht, später erneut versuchen.") from exc
        except sdk.APIStatusError as exc:
            raise ProviderError(f"OpenAI-Fehler {exc.status_code}: {exc}") from exc
        except sdk.APIConnectionError as exc:
            raise ProviderError("Keine Verbindung zu OpenAI.") from exc
        if getattr(response, "status", None) == "incomplete":
            reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
            raise ProviderError(f"OpenAI-Antwort unvollständig ({reason}).")
        message = next((item for item in response.output if item.type == "message"), None)
        if message is None or not message.content:
            raise ProviderError("OpenAI hat keine Nachricht geliefert.")
        content = message.content[0]
        if content.type == "refusal":
            raise ProviderError(f"OpenAI hat die Anfrage abgelehnt: {content.refusal}")
        usage = getattr(response, "usage", None)
        return content.text, {
            "provider": self.name,
            "model": getattr(response, "model", self.model),
            "request_id": getattr(response, "_request_id", None),
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
        }


def _missing_sdk(package: str) -> str:
    return (f"Das Python-Paket {package!r} fehlt. Einmalig einrichten: ./officemd setup-ai "
            "(legt .venv mit Python 3.12 an und installiert die SDKs).")


def get_provider(cfg: Dict[str, Any], name: Optional[str] = None) -> Provider:
    """Baut den konfigurierten Anbieter. Tests ersetzen diese Funktion."""
    name = name or cfg.get("provider")
    if not name:
        raise ProviderError("Kein KI-Anbieter eingestellt: officemd config set provider anthropic "
                            "(oder openai), dann officemd config set-key <anbieter>.")
    key, _source = config.get_key(name)
    if not key:
        raise ProviderError(f"Kein API-Key für {name}. Hinterlegen mit: officemd config set-key {name}")
    settings = cfg.get(name, {})
    if name == "anthropic":
        return AnthropicProvider(key, settings.get("model", "claude-opus-5-5"),
                                 settings.get("effort", "high"), bool(settings.get("fallbacks", True)))
    if name == "openai":
        return OpenAIProvider(key, settings.get("model", "gpt-6-astra"), settings.get("effort", "high"))
    raise ProviderError(f"Unbekannter Anbieter {name!r}")
