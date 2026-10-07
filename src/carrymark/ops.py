"""Die Abläufe hinter den CLI-Befehlen: check, embed, sync, restore, render, export, strip."""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__, converter, ooxml

# Zustände
NEVER = "never"
CURRENT = "current"
STALE = "stale"
LOST = "lost"
UNREADABLE = "unreadable"

STATE_LABELS = {
    NEVER: "Nie vorhanden",
    CURRENT: "Aktuell",
    STALE: "Veraltet",
    LOST: "Verloren",
    UNREADABLE: "Nicht lesbar",
}

PART_KILLERS = (
    "Dokumentinspektor (Dokument prüfen > Alle entfernen)",
    "Öffnen und Speichern in Pages, Google Docs oder LibreOffice",
    "Export als PDF und zurück",
    "Konverter oder Upload-Dienste, die das Archiv neu aufbauen",
)
FOREIGN_APPS = ("pages", "google", "libreoffice", "openoffice", "onlyoffice", "wps")
SIDECAR_SUFFIX = ".carrymark"


class OpError(Exception):
    """Fehler, den das CLI als Meldung ohne Traceback ausgibt."""


def now() -> str:
    return converter.now()


def sidecar_dir(path: Path) -> Path:
    return path.with_name(path.name + SIDECAR_SUFFIX)


def _write_text(path: Path, text: str) -> None:
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _saving_app(pkg: ooxml.Package) -> Optional[str]:
    if not pkg.has("docProps/app.xml"):
        return None
    try:
        root = pkg.xml("docProps/app.xml")
    except ooxml.OoxmlError:
        return None
    for child in root:
        if child.tag.endswith("}Application"):
            return (child.text or "").strip() or None
    return None


def _convert(path: Path) -> converter.Conversion:
    try:
        return converter.convert(path)
    except converter.ConversionError as exc:
        raise OpError(str(exc)) from exc


# -- check ---------------------------------------------------------------------

def check(path: Path) -> Dict[str, Any]:
    path = Path(path)
    report: Dict[str, Any] = {"file": str(path), "state": None, "label": None, "locked": False,
                              "mode": None, "warnings": []}
    lock = ooxml.existing_lock(path)
    if lock is not None:
        report["locked"] = True
        report["lock_file"] = lock.name
        report["warnings"].append(f"Office hat die Datei geöffnet ({lock.name}). Schreiben ist gesperrt.")
    side = sidecar_dir(path)
    report["sidecar"] = {"path": str(side), "exists": side.is_dir(),
                         "restorable": (side / "embed.json").is_file()}

    try:
        pkg = ooxml.Package(path)
        part = pkg.find_knowledge()
        props = pkg.read_props()
    except ooxml.OoxmlError as exc:
        return _finish(report, UNREADABLE, str(exc))

    app = _saving_app(pkg)
    if app:
        report["saved_by"] = app
        if any(name in app.lower() for name in FOREIGN_APPS):
            report["warnings"].append(
                f"Zuletzt gespeichert mit {app}. Solche Programme entfernen Custom-XML-Parts oft.")

    try:
        conv = converter.convert(path)
    except converter.ConversionError as exc:
        return _finish(report, UNREADABLE, str(exc))
    report["fingerprint"] = {"current": conv.fingerprint, "embedded": None, "props": props.get("Fingerprint")}
    report["converter"] = conv.converter
    report["characters"] = len(conv.body)

    if part is None:
        if props.get("Fingerprint") or props.get("PartGuid"):
            hint = " Wiederherstellen mit: carrymark restore." if report["sidecar"]["restorable"] else ""
            report["possible_causes"] = list(PART_KILLERS)
            return _finish(report, LOST,
                           "Der Markdown-Part fehlt, der Fingerprint-Eintrag in docProps/custom.xml "
                           "ist aber noch da. Beim Speichern oder Bearbeiten wurde der Part entfernt." + hint)
        return _finish(report, NEVER, "Noch kein Markdown eingebettet.")

    payload = part.payload
    report["mode"] = "markdown"
    report["fingerprint"]["embedded"] = payload.fingerprint
    report["part"] = {
        "item": part.item_name,
        "item_props": part.props_name,
        "guid": part.guid,
        "registered": part.registered,
        "embedded_at": payload.embedded_at,
        "tool_version": payload.tool_version,
    }
    if not part.registered:
        report["warnings"].append("Part ist nicht vom Hauptpart aus verknüpft. Office könnte ihn verwerfen.")
    if not part.props_name or not part.guid:
        report["warnings"].append("ItemProps mit GUID fehlen. Der Part ist nicht regulär registriert.")
    if not props:
        report["warnings"].append("Fingerprint-Eintrag in docProps/custom.xml fehlt. "
                                  "Ein späterer Verlust des Parts wäre nicht erkennbar.")
    elif props.get("PartGuid") and part.guid and props["PartGuid"] != part.guid:
        report["warnings"].append("GUID in docProps/custom.xml passt nicht zum Part.")

    if payload.fingerprint == conv.fingerprint:
        return _finish(report, CURRENT, "Eingebettetes Markdown passt zum aktuellen Inhalt.")
    return _finish(report, STALE, "Der Inhalt hat sich seit dem Einbetten geändert. "
                                  "Neu einbetten mit carrymark sync.")


def _finish(report: Dict[str, Any], state: str, message: str) -> Dict[str, Any]:
    report["state"] = state
    report["label"] = STATE_LABELS[state]
    report["message"] = message
    return report


# -- embed, restore, render, strip -------------------------------------------------

def _save_payload(path: Path, payload: ooxml.Payload) -> str:
    try:
        pkg = ooxml.Package(path)
        guid = pkg.embed(payload)
        pkg.save()
    except ooxml.OoxmlError as exc:
        raise OpError(str(exc)) from exc
    return guid


def embed(path: Path) -> Dict[str, Any]:
    """Markdown mit markitdown erzeugen, als OKF-Dokument einbetten, Sidecar schreiben."""
    path = Path(path)
    lock = ooxml.existing_lock(path)
    if lock is not None:
        raise OpError(f"Office hat die Datei geöffnet ({lock.name}); bitte zuerst schließen.")
    conv = _convert(path)
    stamp = now()
    document = conv.document(path.name, stamp)
    payload = ooxml.Payload(mode="markdown", fingerprint=conv.fingerprint, embedded_at=stamp,
                            markdown=document, tool_version=f"carrymark/{__version__} {conv.converter}")
    guid = _save_payload(path, payload)
    after = _convert(path)
    if after.fingerprint != conv.fingerprint:  # pragma: no cover - würde einen Fehler im Paketcode bedeuten
        raise OpError("Nach dem Einbetten liefert markitdown einen anderen Inhalt.")
    side = sidecar_dir(path)
    side.mkdir(exist_ok=True)
    _write_text(side / "markdown.md", document)
    _write_text(side / "embed.json", json.dumps({
        "file": path.name,
        "fingerprint": conv.fingerprint,
        "guid": guid,
        "embedded_at": stamp,
        "converter": conv.converter,
        "tool_version": __version__,
    }, ensure_ascii=False, indent=2) + "\n")
    return {"file": str(path), "fingerprint": conv.fingerprint, "guid": guid,
            "sidecar": str(side), "converter": conv.converter, "characters": len(conv.body)}


def restore(path: Path) -> Dict[str, Any]:
    path = Path(path)
    side = sidecar_dir(path)
    meta_path, md_path = side / "embed.json", side / "markdown.md"
    if not meta_path.is_file() or not md_path.is_file():
        raise OpError(f"Kein vollständiger Sidecar zum Wiederherstellen ({side}).")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    payload = ooxml.Payload(
        mode="markdown", fingerprint=meta["fingerprint"],
        embedded_at=meta.get("embedded_at", now()), markdown=md_path.read_text(encoding="utf-8"),
        tool_version=f"carrymark/{meta.get('tool_version', __version__)} {meta.get('converter', '')}".strip())
    guid = _save_payload(path, payload)
    return {"file": str(path), "restored_from": str(side), "guid": guid}


def embedded_markdown(path: Path) -> Optional[str]:
    try:
        part = ooxml.Package(Path(path)).find_knowledge()
    except ooxml.OoxmlError as exc:
        raise OpError(f"Nicht lesbar: {exc}") from exc
    return part.payload.markdown if part else None


def render(path: Path) -> str:
    text = embedded_markdown(path)
    if text is None:
        raise OpError("Kein eingebettetes Markdown.")
    return text


def strip(path: Path, *, keep_props: bool = False) -> bool:
    try:
        pkg = ooxml.Package(Path(path))
        removed = pkg.remove_knowledge(keep_props=keep_props)
        pkg.save()
    except ooxml.OoxmlError as exc:
        raise OpError(str(exc)) from exc
    return removed


# -- export --------------------------------------------------------------------------

def _slug(value: str) -> str:
    table = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})
    text = unicodedata.normalize("NFKD", value.translate(table)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "dokument"


def document_for(path: Path) -> str:
    """Eingebettetes OKF-Dokument, wenn aktuell; sonst frisch umgewandelt (ohne einzubetten)."""
    report = check(path)
    if report["state"] == CURRENT:
        text = embedded_markdown(path)
        if text:
            return text
    if report["state"] == UNREADABLE:
        raise OpError(report["message"])
    return _convert(path).document(Path(path).name)


def export(paths: List[Path], out_dir: Optional[Path] = None, *, okf: bool = False) -> List[Dict[str, Any]]:
    """Markdown neben die Dateien schreiben oder als OKF-Bundle in einen Ordner."""
    files = office_files(paths)
    results: List[Dict[str, Any]] = []
    if okf:
        if out_dir is None:
            raise OpError("Für ein OKF-Bundle bitte einen Zielordner angeben (-o).")
        out_dir.mkdir(parents=True, exist_ok=True)
        entries = []
        used = set()
        for f in files:
            try:
                text = document_for(f)
            except OpError as exc:
                results.append({"file": str(f), "error": str(exc)})
                continue
            base = _slug(f.stem + "-" + f.suffix.lstrip("."))
            name, n = base, 2
            while name in used:
                name, n = f"{base}-{n}", n + 1
            used.add(name)
            target = out_dir / f"{name}.md"
            _write_text(target, text)
            entries.append((f.name, f"/{name}.md", f"{f.suffix.lstrip('.').upper()}-Dokument"))
            results.append({"file": str(f), "markdown": str(target)})
        _write_text(out_dir / "index.md", converter.okf_index(entries))
        return results
    for f in files:
        try:
            text = document_for(f)
        except OpError as exc:
            results.append({"file": str(f), "error": str(exc)})
            continue
        target = (out_dir / (f.name + ".md")) if out_dir else f.with_name(f.name + ".md")
        if out_dir:
            out_dir.mkdir(parents=True, exist_ok=True)
        _write_text(target, text)
        results.append({"file": str(f), "markdown": str(target)})
    return results


# -- Verzeichnis-Scan ---------------------------------------------------------------

def office_files(paths: List[Path], recursive: bool = True) -> List[Path]:
    found: List[Path] = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            pattern = "**/*" if recursive else "*"
            for f in sorted(p.glob(pattern)):
                if (f.is_file() and f.suffix.lower() in ooxml.SUPPORTED_SUFFIXES
                        and not f.name.startswith(("~$", ".cm-", ".zp-"))
                        and not any(part.endswith((SIDECAR_SUFFIX, ".knowledge")) for part in f.parts)):
                    found.append(f)
        else:
            found.append(p)
    return found
