"""Die Abläufe hinter den CLI-Befehlen: check, embed, restore, render, export, strip."""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import __version__, container, converter, ooxml

# Zustände
NEVER = "never"
CURRENT = "current"
STALE = "stale"
LOST = "lost"
UNREADABLE = "unreadable"

STATE_LABELS = {
    NEVER: "Nicht eingebettet",
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


def sidecar_dir(path: Path) -> Path:
    return path.with_name(path.name + SIDECAR_SUFFIX)


def _write_text(path: Path, text: str) -> None:
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


# -- check ---------------------------------------------------------------------

@dataclass
class Inspection:
    """Ergebnis einer Prüfung samt Umwandlung und Part, damit Folgeschritte nichts wiederholen."""

    report: Dict[str, Any]
    conversion: Optional[converter.Conversion] = None
    part: Optional[ooxml.KnowledgePart] = None
    package: Optional[ooxml.Package] = None


def inspect(path: Path) -> Inspection:
    path = Path(path)
    report: Dict[str, Any] = {"file": str(path), "state": None, "label": None, "locked": False,
                              "embedded": False, "warnings": []}
    lock = ooxml.existing_lock(path)
    if lock is not None:
        report["locked"] = True
        report["lock_file"] = lock.name
        report["warnings"].append(f"Office hat die Datei geöffnet ({lock.name}). Schreiben ist gesperrt.")
    side = sidecar_dir(path)
    report["sidecar"] = {"path": str(side), "exists": side.is_dir(),
                         "restorable": (side / "embed.json").is_file()}

    try:
        pkg = container.open_package(path)
        part = pkg.find_knowledge()
        props = pkg.read_props()
    except ooxml.OoxmlError as exc:
        return Inspection(_finish(report, UNREADABLE, str(exc)))
    result = Inspection(report, part=part, package=pkg)

    app = pkg.saving_app()
    if app:
        report["saved_by"] = app
        if any(name in app.lower() for name in FOREIGN_APPS):
            report["warnings"].append(
                f"Zuletzt gespeichert mit {app}. Solche Programme entfernen Custom-XML-Parts oft.")

    # Verlust hängt nicht an der Umwandlung: erst Part und Einträge ansehen.
    if part is None and (props.get("Fingerprint") or props.get("PartGuid")):
        hint = " Wiederherstellen mit: carrymark restore." if report["sidecar"]["restorable"] else ""
        report["possible_causes"] = list(PART_KILLERS)
        _finish(report, LOST, "Der Markdown-Part fehlt, der Fingerprint-Eintrag in docProps/custom.xml "
                              "ist aber noch da. Beim Speichern oder Bearbeiten wurde der Part entfernt." + hint)
    try:
        conv = converter.convert(path, pkg)
    except converter.ConversionError as exc:
        if report["state"] is None:
            _finish(report, UNREADABLE, str(exc))
        return result
    result.conversion = conv
    report["fingerprint"] = {"current": conv.fingerprint, "props": props.get("Fingerprint"),
                             "embedded": part.payload.fingerprint if part else None}
    report["converter"] = conv.converter
    report["characters"] = len(conv.body)
    if report["state"] is not None:
        return result
    if part is None:
        _finish(report, NEVER, "Noch kein Markdown eingebettet.")
        return result

    report["embedded"] = bool(part.payload.markdown)
    report["part"] = {"item": part.item_name, "item_props": part.props_name, "guid": part.guid,
                      "registered": part.registered, "embedded_at": part.payload.embedded_at,
                      "tool_version": part.payload.tool_version}
    if not part.registered:
        report["warnings"].append("Part ist nicht vom Hauptpart aus verknüpft. Office könnte ihn verwerfen.")
    if not part.props_name or not part.guid:
        report["warnings"].append("ItemProps mit GUID fehlen. Der Part ist nicht regulär registriert.")
    if not props:
        report["warnings"].append("Fingerprint-Eintrag in docProps/custom.xml fehlt. "
                                  "Ein späterer Verlust des Parts wäre nicht erkennbar.")
    elif props.get("PartGuid") and part.guid and props["PartGuid"] != part.guid:
        report["warnings"].append("GUID in docProps/custom.xml passt nicht zum Part.")

    if part.payload.fingerprint == conv.fingerprint:
        _finish(report, CURRENT, "Eingebettetes Markdown passt zum aktuellen Inhalt.")
    else:
        _finish(report, STALE, "Der Inhalt hat sich seit dem Einbetten geändert. Neu einbetten mit carrymark sync.")
    return result


def check(path: Path) -> Dict[str, Any]:
    return inspect(path).report


def _finish(report: Dict[str, Any], state: str, message: str) -> Dict[str, Any]:
    report["state"] = state
    report["label"] = STATE_LABELS[state]
    report["message"] = message
    return report


# -- embed, restore, render, strip -------------------------------------------------

def _save_payload(path: Path, payload: ooxml.Payload, pkg: Optional[ooxml.Package] = None) -> str:
    try:
        pkg = pkg or container.open_package(path)
        guid = pkg.embed(payload)
        pkg.save()
    except ooxml.OoxmlError as exc:
        raise OpError(str(exc)) from exc
    return guid


def embed(path: Path, conv: Optional[converter.Conversion] = None,
          pkg: Optional[ooxml.Package] = None) -> Dict[str, Any]:
    """Markdown als OKF-Dokument einbetten und die Sicherung schreiben.

    ``conv``/``pkg`` aus einer vorherigen Prüfung derselben Datei sparen eine Umwandlung.
    """
    path = Path(path)
    lock = ooxml.existing_lock(path)
    if lock is not None:
        raise OpError(f"Office hat die Datei geöffnet ({lock.name}); bitte zuerst schließen.")
    if conv is None:
        try:
            pkg = pkg or container.open_package(path)
            conv = converter.convert(path, pkg)
        except (ooxml.OoxmlError, converter.ConversionError) as exc:
            raise OpError(str(exc)) from exc
    stamp = converter.now()
    document = conv.document(path.name, stamp)
    payload = ooxml.Payload(fingerprint=conv.fingerprint, embedded_at=stamp, markdown=document,
                            tool_version=f"carrymark/{__version__} {conv.converter}")
    guid = _save_payload(path, payload, pkg)
    side = sidecar_dir(path)
    side.mkdir(exist_ok=True)
    _write_text(side / "markdown.md", document)
    _write_text(side / "embed.json", json.dumps({
        "file": path.name, "fingerprint": conv.fingerprint, "guid": guid, "embedded_at": stamp,
        "converter": conv.converter, "tool_version": __version__,
    }, ensure_ascii=False, indent=2) + "\n")
    return {"file": str(path), "fingerprint": conv.fingerprint, "guid": guid,
            "sidecar": str(side), "converter": conv.converter, "characters": len(conv.body)}


def restore(path: Path) -> Dict[str, Any]:
    path = Path(path)
    side = sidecar_dir(path)
    meta_path, md_path = side / "embed.json", side / "markdown.md"
    if not meta_path.is_file() or not md_path.is_file():
        raise OpError(f"Keine vollständige Sicherung zum Wiederherstellen ({side}).")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    payload = ooxml.Payload(
        fingerprint=meta["fingerprint"], embedded_at=meta.get("embedded_at", converter.now()),
        markdown=md_path.read_text(encoding="utf-8"),
        tool_version=f"carrymark/{meta.get('tool_version', __version__)} {meta.get('converter', '')}".strip())
    guid = _save_payload(path, payload)
    return {"file": str(path), "restored_from": str(side), "guid": guid, "fingerprint": meta["fingerprint"]}


def render(path: Path) -> str:
    try:
        part = container.open_package(Path(path)).find_knowledge()
    except ooxml.OoxmlError as exc:
        raise OpError(f"Nicht lesbar: {exc}") from exc
    if part is None or not part.payload.markdown:
        raise OpError("Kein eingebettetes Markdown.")
    return part.payload.markdown


def strip(path: Path, *, keep_props: bool = False) -> bool:
    try:
        pkg = container.open_package(Path(path))
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
    found = inspect(path)
    if found.report["state"] == CURRENT and found.part and found.part.payload.markdown:
        return found.part.payload.markdown
    if found.conversion is None:
        raise OpError(found.report["message"])
    return found.conversion.document(Path(path).name)


def export(paths: List[Path], out_dir: Optional[Path] = None, *, okf: bool = False) -> List[Dict[str, Any]]:
    """Markdown neben die Dateien schreiben oder als Bundle (OKF) in einen Ordner."""
    if okf and out_dir is None:
        raise OpError("Für ein OKF-Bundle bitte einen Zielordner angeben (-o).")
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
    results: List[Dict[str, Any]] = []
    entries: List[Tuple[str, str, str]] = []
    used: set = set()
    for f in office_files(paths)[0]:
        try:
            text = document_for(f)
        except OpError as exc:
            results.append({"file": str(f), "error": str(exc)})
            continue
        # Zielname eindeutig halten, auch bei gleichnamigen Dateien aus verschiedenen Ordnern.
        stem = _slug(f"{f.stem}-{f.suffix.lstrip('.')}") if okf else f.name
        name, n = stem, 2
        while out_dir and name in used:
            name, n = f"{stem}-{n}", n + 1
        used.add(name)
        if okf:
            target = out_dir / f"{name}.md"
            entries.append((f.name, f"/{name}.md", f"{f.suffix.lstrip('.').upper()}-Dokument"))
        else:
            target = (out_dir / f"{name}.md") if out_dir else f.with_name(f.name + ".md")
        _write_text(target, text)
        results.append({"file": str(f), "markdown": str(target)})
    if okf:
        _write_text(out_dir / "index.md", converter.okf_index(entries))
    return results


# -- Dateiauswahl -------------------------------------------------------------------

def _is_candidate(f: Path) -> bool:
    return (f.suffix.lower() in container.SUPPORTED_SUFFIXES
            and not f.name.startswith(("~$", ".cm-"))
            and not any(part.endswith((SIDECAR_SUFFIX, ".officemd", ".knowledge")) for part in f.parts))


def office_files(paths: List[Path]) -> Tuple[List[Path], List[Path]]:
    """(verarbeitbare Office-Dateien, ausdrücklich genannte, aber nicht unterstützte Dateien)."""
    found: Dict[Path, Path] = {}  # aufgelöster Pfad -> wie angegeben; jede Datei nur einmal
    ignored: List[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            candidates = [f for f in sorted(p.rglob("*")) if f.is_file() and _is_candidate(f)]
        elif p.suffix.lower() in container.SUPPORTED_SUFFIXES:
            candidates = [p]
        else:
            ignored.append(p)
            continue
        for f in candidates:
            found.setdefault(f.resolve(), f)
    return list(found.values()), ignored
