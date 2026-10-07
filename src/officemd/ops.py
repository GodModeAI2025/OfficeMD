"""Die Abläufe hinter den CLI-Befehlen: check, embed, update, restore, render, strip."""
from __future__ import annotations

import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__, distiller, evidence, ooxml, rawmd
from .fingerprint import text_fingerprint

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


class OpError(Exception):
    """Fehler, den das CLI als Meldung ohne Traceback ausgibt."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sidecar_dir(path: Path) -> Path:
    return path.with_name(path.name + ".knowledge")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _write_text(path: Path, text: str) -> None:
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _extract(path: Path) -> Dict[str, Any]:
    try:
        return distiller.extract(path)
    except distiller.ExtractionFailed as exc:
        raise OpError(f"Nicht lesbar: {exc}") from exc


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


# -- check ---------------------------------------------------------------------

def check(path: Path, *, with_evidence: bool = True) -> Dict[str, Any]:
    path = Path(path)
    report: Dict[str, Any] = {
        "file": str(path),
        "state": None,
        "label": None,
        "locked": False,
        "mode": None,
        "warnings": [],
    }
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
        normalized = distiller.extract(path)
    except distiller.ExtractionFailed as exc:
        return _finish(report, UNREADABLE, str(exc))
    current_fp = text_fingerprint(normalized)
    report["fingerprint"] = {"current": current_fp, "embedded": None, "props": props.get("Fingerprint")}
    report["segments"] = normalized["segment_count"]

    if part is None:
        if props.get("Fingerprint") or props.get("PartGuid"):
            report["mode"] = props.get("Mode")
            hint = " Wiederherstellen mit: officemd restore." if report["sidecar"]["restorable"] else ""
            report["possible_causes"] = list(PART_KILLERS)
            return _finish(report, LOST,
                           "Der Wissens-Part fehlt, der Fingerprint-Eintrag in docProps/custom.xml "
                           "ist aber noch da. Beim Speichern oder Bearbeiten wurde der Part entfernt." + hint)
        return _finish(report, NEVER, "Kein eingebettetes Wissen.")

    payload = part.payload
    report["mode"] = payload.mode
    report["fingerprint"]["embedded"] = payload.fingerprint
    report["part"] = {
        "item": part.item_name,
        "item_props": part.props_name,
        "guid": part.guid,
        "registered": part.registered,
        "embedded_at": payload.embedded_at,
        "source_id": payload.source_id,
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

    stale = payload.fingerprint != current_fp
    if payload.mode == "graph" and payload.graph_json and with_evidence and payload.source_id:
        try:
            graph = json.loads(payload.graph_json)
            ev = evidence.verify_against(graph, payload.source_id, normalized)
            report["evidence"] = ev
        except (ValueError, distiller.DistillerError) as exc:
            report["warnings"].append(f"Belegprüfung nicht möglich: {exc}")

    if not stale:
        ev = report.get("evidence")
        if ev and ev["counts"]["not_found"]:
            report["warnings"].append("Text unverändert, trotzdem fehlen Belege. Der Graph passte schon "
                                      "beim Einbetten nicht vollständig.")
        return _finish(report, CURRENT, "Eingebettetes Wissen passt zum aktuellen Text.")

    ev = report.get("evidence")
    if ev:
        missing = ev["counts"]["not_found"]
        if missing:
            message = (f"Text hat sich geändert. {missing} von {ev['total']} Belegen sind im Dokument "
                       "nicht mehr auffindbar. Aktualisieren mit officemd sync (KI) oder einen neuen "
                       "Graphen per officemd update einspielen.")
        else:
            message = (f"Text hat sich geändert, alle {ev['total']} Belege sind weiterhin auffindbar. "
                       "Neue Inhalte sind im Graphen noch nicht erfasst.")
    else:
        message = "Text hat sich seit dem Einbetten geändert. Neu einbetten mit officemd embed."
    return _finish(report, STALE, message)


def _finish(report: Dict[str, Any], state: str, message: str) -> Dict[str, Any]:
    report["state"] = state
    report["label"] = STATE_LABELS[state]
    report["message"] = message
    return report


# -- embed ---------------------------------------------------------------------

def _prepare_graph(graph_path: Path, workdir: Path) -> Path:
    """Graph kopieren, abgeleitete Felder neu berechnen, validieren."""
    target = workdir / "graph.knowledge.json"
    shutil.copyfile(graph_path, target)
    try:
        distiller.build_graph(target)
    except distiller.DistillerError as exc:
        raise OpError(f"build_graph.py lehnt den Graphen ab: {exc.stderr.strip() or exc}") from exc
    report = distiller.validate(target)
    if not report.get("ok"):
        errors = "\n  ".join(report.get("errors", [])[:20])
        raise OpError(f"Graph ist nicht konform (validate_knowledge.py):\n  {errors}")
    return target


def _save_payload(path: Path, payload: ooxml.Payload) -> str:
    try:
        pkg = ooxml.Package(path)
        guid = pkg.embed(payload)
        pkg.save()
    except ooxml.OoxmlError as exc:
        raise OpError(str(exc)) from exc
    return guid


def _post_check(path: Path, fingerprint: str) -> None:
    after = text_fingerprint(_extract(path))
    if after != fingerprint:  # pragma: no cover - würde einen Fehler im Paketcode bedeuten
        raise OpError("Nach dem Einbetten liefert die Extraktion einen anderen Fingerprint.")


def embed(path: Path, graph_path: Optional[Path] = None, *, source_id: Optional[str] = None,
          allow_missing_evidence: bool = False, replace: bool = False,
          include_markdown: bool = True, compile_info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    path = Path(path)
    lock = ooxml.existing_lock(path)
    if lock is not None:
        raise OpError(f"Office hat die Datei geöffnet ({lock.name}); bitte zuerst schließen.")
    try:
        ooxml.Package(path)
    except ooxml.OoxmlError as exc:
        raise OpError(f"Nicht lesbar: {exc}") from exc
    normalized = _extract(path)
    fp = text_fingerprint(normalized)
    stamp = now()
    side = sidecar_dir(path)
    result: Dict[str, Any] = {"file": str(path), "fingerprint": fp, "sidecar": str(side)}

    if graph_path is None:
        markdown = rawmd.render(normalized, fp, stamp)
        payload = ooxml.Payload(mode="raw", fingerprint=fp, embedded_at=stamp,
                                markdown=markdown, tool_version=__version__)
        guid = _save_payload(path, payload)
        _post_check(path, fp)
        side.mkdir(exist_ok=True)
        _write_text(side / "knowledge.md", markdown)
        _write_meta(side, payload, guid, path)
        result.update(mode="raw", guid=guid)
        return result

    graph_path = Path(graph_path)
    if (side / "knowledge.json").is_file() and not replace:
        existing = (side / "knowledge.json").read_bytes()
        if existing != graph_path.read_bytes():
            raise OpError("Es gibt schon einen eingebetteten Graphen. Für Änderungen officemd update "
                          "verwenden (additiver Merge), oder --replace für bewusstes Ersetzen.")
    with tempfile.TemporaryDirectory(prefix="omd-embed-") as tmp:
        prepared = _prepare_graph(graph_path, Path(tmp))
        graph = _read_json(prepared)
        try:
            sid = evidence.pick_source_id(graph, normalized, path.name, source_id)
        except ValueError as exc:
            raise OpError(str(exc)) from exc
        ev = evidence.verify_against(graph, sid, normalized)
        if ev["counts"]["not_found"] and not allow_missing_evidence:
            lines = "\n  ".join(f"{m['evidence']}: {m['detail']}" for m in ev["missing"][:20])
            raise OpError(f"{ev['counts']['not_found']} von {ev['total']} Belegen sind im Dokument nicht "
                          f"auffindbar:\n  {lines}\n(--allow-missing-evidence bettet trotzdem ein)")
        md_path = Path(tmp) / "graph.knowledge.md"
        markdown = distiller.build_md(prepared, md_path) if include_markdown else None
        graph_text = prepared.read_text(encoding="utf-8")

        payload = ooxml.Payload(mode="graph", fingerprint=fp, embedded_at=stamp, markdown=markdown,
                                graph_json=graph_text, source_id=sid, tool_version=__version__)
        guid = _save_payload(path, payload)
        _post_check(path, fp)
        side.mkdir(exist_ok=True)
        if replace and (side / "knowledge.json").is_file():
            _archive_sidecar_graph(side, "replaced")
        _write_text(side / "knowledge.json", graph_text)
        if markdown is not None:
            _write_text(side / "knowledge.md", markdown)
        _write_meta(side, payload, guid, path, compile_info)
    result.update(mode="graph", guid=guid, source_id=sid, evidence=ev)
    return result


def _archive_sidecar_graph(side: Path, reason: str) -> Path:
    versions = side / "versions"
    versions.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = versions / f"knowledge.{stamp}.{reason}.json"
    shutil.copyfile(side / "knowledge.json", target)
    return target


def _write_meta(side: Path, payload: ooxml.Payload, guid: str, path: Path,
                compile_info: Optional[Dict[str, Any]] = None) -> None:
    meta = {
        "file": path.name,
        "mode": payload.mode,
        "fingerprint": payload.fingerprint,
        "source_id": payload.source_id,
        "guid": guid,
        "embedded_at": payload.embedded_at,
        "tool_version": payload.tool_version,
    }
    if compile_info:
        meta["compiled_by"] = compile_info
    _write_json(side / "embed.json", meta)


# -- update ----------------------------------------------------------------------

def update(path: Path, incoming: Path, *, allow_missing_evidence: bool = False) -> Dict[str, Any]:
    path = Path(path)
    try:
        part = ooxml.Package(path).find_knowledge()
    except ooxml.OoxmlError as exc:
        raise OpError(f"Nicht lesbar: {exc}") from exc
    side = sidecar_dir(path)
    if part is None or part.payload.mode != "graph" or not part.payload.graph_json:
        if (side / "knowledge.json").is_file():
            raise OpError("Kein eingebetteter Graph in der Datei. Erst officemd restore ausführen.")
        raise OpError("Kein eingebetteter Graph. Erst officemd embed --graph ausführen.")
    side.mkdir(exist_ok=True)
    base = side / "knowledge.json"
    embedded = part.payload.graph_json
    if base.is_file() and base.read_text(encoding="utf-8") != embedded:
        _archive_sidecar_graph(side, "sidecar-diverged")
    _write_text(base, embedded)

    source_id = part.payload.source_id
    with tempfile.TemporaryDirectory(prefix="omd-update-") as tmp:
        prepared = _prepare_graph(Path(incoming), Path(tmp))
        aligned = _align_source_digest(json.loads(embedded), prepared, source_id)
        try:
            merge = distiller.merge(base, prepared, base, side / "versions")
        except distiller.DistillerError as exc:
            raise OpError(f"merge_knowledge.py: {exc.stderr.strip() or exc}") from exc
    result = embed(path, base, source_id=source_id,
                   allow_missing_evidence=allow_missing_evidence, replace=False)
    result["merge"] = {
        "stdout": merge.stdout.strip(),
        "diff_json": str(side / "knowledge.diff.json"),
        "diff_md": str(side / "knowledge.diff.md"),
        "versions": str(side / "versions"),
        "aligned_source_digest": aligned,
    }
    return result


def _align_source_digest(base: Dict[str, Any], incoming_path: Path, source_id: Optional[str]) -> Optional[Dict[str, str]]:
    """Byte-Hash der Dateiquelle im eingehenden Graphen an die Basis angleichen.

    Ein neu kompilierter Graph trägt für dieselbe Quelle den Byte-Hash der geänderten Datei.
    ``merge_knowledge.py`` wertet das als widersprüchliche Nutzlast und bricht ab. Für die
    eingebettete Datei ist der Byte-Hash nach jedem Speichern ohnehin überholt; Aktualität
    prüft OfficeMD über Text-Fingerprint und Belege. Gibt die Änderung zurück, damit sie im
    Ergebnis sichtbar ist.
    """
    if not source_id:
        return None
    base_source = next((s for s in base.get("metadata", {}).get("sources", [])
                        if isinstance(s, dict) and s.get("id") == source_id), None) or {}
    incoming = _read_json(incoming_path)
    for source in incoming.get("metadata", {}).get("sources", []):
        if isinstance(source, dict) and source.get("id") == source_id:
            changes = {}
            for field in ("content_sha256", "normalized_sha256"):
                if source.get(field) == base_source.get(field):
                    continue
                changes[field] = {"incoming": source.get(field), "kept": base_source.get(field)}
                if base_source.get(field) is None:
                    source.pop(field, None)
                else:
                    source[field] = base_source[field]
            if not changes:
                return None
            _write_json(incoming_path, incoming)
            return {"source_id": source_id, **changes}
    return None


# -- restore, render, strip ---------------------------------------------------------

def restore(path: Path) -> Dict[str, Any]:
    path = Path(path)
    side = sidecar_dir(path)
    meta_path = side / "embed.json"
    if not meta_path.is_file():
        raise OpError(f"Kein Sidecar zum Wiederherstellen gefunden ({meta_path}).")
    meta = _read_json(meta_path)
    graph_text = None
    if meta.get("mode") == "graph":
        graph_file = side / "knowledge.json"
        if not graph_file.is_file():
            raise OpError("Sidecar ist unvollständig: knowledge.json fehlt.")
        graph_text = graph_file.read_text(encoding="utf-8")
        report = distiller.validate(graph_file)
        if not report.get("ok"):
            raise OpError("Sidecar-Graph ist nicht konform: " + "; ".join(report.get("errors", [])[:5]))
    md_file = side / "knowledge.md"
    payload = ooxml.Payload(
        mode=meta.get("mode", "raw"),
        fingerprint=meta["fingerprint"],
        embedded_at=meta.get("embedded_at", now()),
        markdown=md_file.read_text(encoding="utf-8") if md_file.is_file() else None,
        graph_json=graph_text,
        source_id=meta.get("source_id"),
        tool_version=meta.get("tool_version", __version__),
    )
    guid = _save_payload(path, payload)
    return {"file": str(path), "restored_from": str(side), "guid": guid, "mode": payload.mode}


def render(path: Path) -> str:
    try:
        part = ooxml.Package(Path(path)).find_knowledge()
    except ooxml.OoxmlError as exc:
        raise OpError(f"Nicht lesbar: {exc}") from exc
    if part is None:
        raise OpError("Kein eingebettetes Wissen.")
    if part.payload.mode == "graph" and part.payload.graph_json:
        with tempfile.TemporaryDirectory(prefix="omd-render-") as tmp:
            graph = Path(tmp) / "graph.knowledge.json"
            graph.write_text(part.payload.graph_json, encoding="utf-8")
            return distiller.build_md(graph, Path(tmp) / "graph.knowledge.md")
    return part.payload.markdown or ""


def strip(path: Path, *, keep_props: bool = False) -> bool:
    try:
        pkg = ooxml.Package(Path(path))
        removed = pkg.remove_knowledge(keep_props=keep_props)
        pkg.save()
    except ooxml.OoxmlError as exc:
        raise OpError(str(exc)) from exc
    return removed


# -- Verzeichnis-Scan ---------------------------------------------------------------

def office_files(paths: List[Path], recursive: bool = True) -> List[Path]:
    found: List[Path] = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            pattern = "**/*" if recursive else "*"
            for f in sorted(p.glob(pattern)):
                if (f.is_file() and f.suffix.lower() in ooxml.SUPPORTED_SUFFIXES
                        and not f.name.startswith(("~$", ".omd-"))
                        and not any(part.endswith(".knowledge") for part in f.parts)):
                    found.append(f)
        else:
            found.append(p)
    return found
