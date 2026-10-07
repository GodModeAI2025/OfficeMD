"""XLSX- und PPTX-Adapter im Ausgabeformat von ``extract_source.py``.

Der Distiller liest DOCX, aber weder XLSX noch PPTX. Diese Adapter schließen die Lücke und
verwenden dafür die Hilfsfunktionen des Distillers selbst: dieselbe pfadsichere Eingabe
(kein Symlink, ``--input-root``), dieselben ZIP-Grenzen, dieselbe DTD-Sperre und dieselbe
Segment-Identität. Formeln werden nie ausgewertet; übernommen wird nur der von Excel
gespeicherte Wert.

Selektoren:

* XLSX: ``CsvSelector {sheet, cell_range}`` pro Zeile, wie der CSV-Adapter des Distillers.
* PPTX: ``FragmentSelector`` ``ppt/slides/slideN.xml#paragraph=K`` wie beim DOCX-Adapter,
  dazu ``PageSelector {page}`` mit der Foliennummer. ``verify_evidence.py`` kann
  Fragment-Selektoren ohne Spec-Erweiterung auflösen.
"""
from __future__ import annotations

import importlib.util
import io
import posixpath
import re
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

from ..distiller import distiller_root

ADAPTER_VERSION = "officemd-ooxml-1.0"

NS_REL_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_SML = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_PML = "http://schemas.openxmlformats.org/presentationml/2006/main"
NS_DML = "http://schemas.openxmlformats.org/drawingml/2006/main"
REL_OFFICE_DOCUMENT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
REL_SHARED_STRINGS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings"
REL_NOTES_SLIDE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide"

CELL_REF = re.compile(r"^([A-Z]{1,3})([1-9][0-9]*)$")

_KD = None


def _kd():
    """``extract_source.py`` des Distillers als Modul laden (einmalig)."""
    global _KD
    if _KD is None:
        scripts = distiller_root() / "scripts"
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        spec = importlib.util.spec_from_file_location("kd_extract_source", scripts / "extract_source.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _KD = module
    return _KD


class ExtractionError(Exception):
    pass


MIME = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def extract(path: Path) -> Dict[str, Any]:
    kd = _kd()
    path = Path(path)
    kind = path.suffix.lower().lstrip(".")
    if kind not in MIME:
        raise ExtractionError(f"Adapter unterstützt nur XLSX und PPTX, nicht {path.suffix}")
    max_bytes = kd.DEFAULT_MAX_INPUT_BYTES
    max_segments = kd.DEFAULT_MAX_SEGMENTS
    max_chars = kd.DEFAULT_MAX_SEGMENT_CHARS
    try:
        data, logical_name = kd._read_bounded_input(path.name, path.parent, max_bytes)
        archive = _open_archive(kd, data, max_bytes)
        with archive:
            if kind == "xlsx":
                drafts = _xlsx_drafts(kd, archive, max_bytes, max_segments, max_chars)
            else:
                drafts = _pptx_drafts(kd, archive, max_bytes, max_segments, max_chars)
        source_hash = kd._sha256(data)
        segments = kd._finalize_segments(
            drafts, source_hash, max_segments=max_segments, max_segment_chars=max_chars
        )
        source_doc_hash = kd.normalized_sha256(segments) if hasattr(kd, "normalized_sha256") else None
        document = {
            "adapter_version": ADAPTER_VERSION,
            "source": {
                "id": "source-" + source_hash[:24],
                "file": logical_name,
                "logical_filename": logical_name,
                "type": kind,
                "mime_type": MIME[kind],
                "content_sha256": source_hash,
                **({"normalized_sha256": source_doc_hash} if source_doc_hash else {}),
                "size_bytes": len(data),
            },
            "segment_count": len(segments),
            "segments": segments,
        }
        kd._check_json_output_size(document, kd.DEFAULT_MAX_OUTPUT_BYTES)
        return document
    except kd.ExtractionError as exc:
        raise ExtractionError(f"error [{exc.code}]: {exc}") from exc


def _open_archive(kd, data: bytes, max_bytes: int) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data), "r")
    except (zipfile.BadZipFile, OSError) as exc:
        raise kd.MalformedSourceError("not a valid OOXML ZIP package (encrypted or damaged)") from exc
    names = kd._safe_zip_names(archive, max_bytes)
    lowered = {n.lower() for n in names}
    if any(n.endswith("vbaproject.bin") for n in lowered):
        raise kd.UnsupportedSourceError("macro-bearing OOXML packages are not supported")
    if "[Content_Types].xml" not in names:
        raise kd.MalformedSourceError("OOXML package is missing [Content_Types].xml")
    content_types = kd._read_zip_member(archive, "[Content_Types].xml", max_bytes)
    if b"macroenabled" in content_types.lower():
        raise kd.UnsupportedSourceError("macro-enabled OOXML content types are not supported")
    archive._omd_names = names  # type: ignore[attr-defined]
    return archive


def _xml(kd, archive: zipfile.ZipFile, name: str, max_bytes: int) -> ET.Element:
    if name not in archive._omd_names:  # type: ignore[attr-defined]
        raise kd.MalformedSourceError(f"OOXML member {name!r} is missing")
    data = kd._read_zip_member(archive, name, max_bytes)
    kd._require_utf8_xml(name, data)
    if kd.XML_DECLARATION_ATTACK.search(data):
        raise kd.MalformedSourceError(f"OOXML member {name!r} contains a forbidden DTD/entity declaration")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise kd.MalformedSourceError(f"OOXML member {name!r} contains malformed XML: {exc}") from exc


def _rels(kd, archive, part: str, max_bytes: int) -> Dict[str, Tuple[str, str]]:
    """Relationship-ID -> (Typ, aufgelöster Zielpart)."""
    directory, base = posixpath.split(part)
    rels_name = posixpath.join(directory, "_rels", base + ".rels")
    if rels_name not in archive._omd_names:  # type: ignore[attr-defined]
        return {}
    result = {}
    for rel in _xml(kd, archive, rels_name, max_bytes).iter(f"{{{NS_REL_PKG}}}Relationship"):
        if rel.get("TargetMode") == "External":
            continue
        target = rel.get("Target", "")
        resolved = target.lstrip("/") if target.startswith("/") else posixpath.normpath(
            posixpath.join(directory, target))
        result[rel.get("Id", "")] = (rel.get("Type", ""), resolved)
    return result


def _main_part(kd, archive, max_bytes: int) -> str:
    for rel_type, target in _rels(kd, archive, "", max_bytes).values():
        if rel_type == REL_OFFICE_DOCUMENT:
            return target
    raise kd.MalformedSourceError("OOXML package has no officeDocument relationship")


# -- XLSX ----------------------------------------------------------------------

def _column_index(letters: str) -> int:
    value = 0
    for ch in letters:
        value = value * 26 + (ord(ch) - ord("A") + 1)
    return value - 1


def _rich_text(element: ET.Element) -> str:
    # Phonetische Hilfen (rPh) gehören nicht zum sichtbaren Text.
    parts = []
    for child in element:
        if child.tag == f"{{{NS_SML}}}t":
            parts.append(child.text or "")
        elif child.tag == f"{{{NS_SML}}}r":
            parts.extend(t.text or "" for t in child.iter(f"{{{NS_SML}}}t"))
    return "".join(parts)


def _cell_text(cell: ET.Element, shared: List[str]) -> str:
    cell_type = cell.get("t", "n")
    value = cell.find(f"{{{NS_SML}}}v")
    raw = value.text if value is not None and value.text is not None else ""
    if cell_type == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return ""
    if cell_type == "inlineStr":
        inline = cell.find(f"{{{NS_SML}}}is")
        return _rich_text(inline) if inline is not None else ""
    if cell_type == "b":
        return "TRUE" if raw == "1" else "FALSE" if raw == "0" else raw
    return raw


def _xlsx_drafts(kd, archive, max_bytes, max_segments, max_chars) -> List[Dict[str, Any]]:
    workbook_part = _main_part(kd, archive, max_bytes)
    workbook = _xml(kd, archive, workbook_part, max_bytes)
    rels = _rels(kd, archive, workbook_part, max_bytes)
    shared: List[str] = []
    for rel_type, target in rels.values():
        if rel_type == REL_SHARED_STRINGS:
            sst = _xml(kd, archive, target, max_bytes)
            shared = [_rich_text(si) for si in sst.iter(f"{{{NS_SML}}}si")]
    drafts: List[Dict[str, Any]] = []
    sheets = workbook.find(f"{{{NS_SML}}}sheets")
    for sheet in (sheets if sheets is not None else []):
        name = sheet.get("name", "")
        rel = rels.get(sheet.get(f"{{{NS_R}}}id", ""))
        if rel is None or rel[1] not in archive._omd_names:  # Diagrammblätter u. Ä.
            continue
        root = _xml(kd, archive, rel[1], max_bytes)
        data = root.find(f"{{{NS_SML}}}sheetData")
        if data is None:
            continue
        next_row = 1
        for row in data.findall(f"{{{NS_SML}}}row"):
            row_number = int(row.get("r", next_row))
            next_row = row_number + 1
            cells: Dict[int, str] = {}
            next_col = 0
            for cell in row.findall(f"{{{NS_SML}}}c"):
                match = CELL_REF.match(cell.get("r", ""))
                col = _column_index(match.group(1)) if match else next_col
                next_col = col + 1
                cells[col] = _cell_text(cell, shared)
            filled = [c for c, v in cells.items() if v != ""]
            if not filled:
                continue
            last = max(filled)
            text = "\t".join(cells.get(c, "") for c in range(last + 1))
            cell_range = f"A{row_number}:{kd._spreadsheet_column(last)}{row_number}"
            kd._append_bounded_draft(
                drafts,
                {
                    "text": text,
                    "locator": f"{name}!{cell_range}",
                    "selectors": [{"type": "CsvSelector", "sheet": name, "cell_range": cell_range}],
                },
                max_segments=max_segments,
                max_segment_chars=max_chars,
            )
    return drafts


# -- PPTX ----------------------------------------------------------------------

def _dml_paragraph_text(paragraph: ET.Element) -> str:
    parts = []
    for item in paragraph.iter():
        if item.tag == f"{{{NS_DML}}}t":
            parts.append(item.text or "")
        elif item.tag == f"{{{NS_DML}}}br":
            parts.append("\n")
    return "".join(parts).replace("\r\n", "\n").replace("\r", "\n").strip()


def _slide_paragraph_drafts(kd, root, part, slide_number, drafts, max_segments, max_chars):
    number = 0
    for paragraph in root.iter(f"{{{NS_DML}}}p"):
        number += 1
        text = _dml_paragraph_text(paragraph)
        if not text:
            continue
        fragment = f"{part}#paragraph={number}"
        selectors = [
            {"type": "FragmentSelector", "fragment": fragment},
            {"type": "TextQuoteSelector", "exact": text},
            {"type": "PageSelector", "page": slide_number},
        ]
        kd._append_bounded_draft(
            drafts,
            {"text": text, "locator": fragment, "selectors": selectors},
            max_segments=max_segments,
            max_segment_chars=max_chars,
        )


def _pptx_drafts(kd, archive, max_bytes, max_segments, max_chars) -> List[Dict[str, Any]]:
    presentation_part = _main_part(kd, archive, max_bytes)
    presentation = _xml(kd, archive, presentation_part, max_bytes)
    rels = _rels(kd, archive, presentation_part, max_bytes)
    drafts: List[Dict[str, Any]] = []
    id_list = presentation.find(f"{{{NS_PML}}}sldIdLst")
    slide_number = 0
    for slide_id in (id_list if id_list is not None else []):
        rel = rels.get(slide_id.get(f"{{{NS_R}}}id", ""))
        if rel is None:
            continue
        slide_number += 1
        slide_part = rel[1]
        root = _xml(kd, archive, slide_part, max_bytes)
        _slide_paragraph_drafts(kd, root, slide_part, slide_number, drafts, max_segments, max_chars)
        for rel_type, target in _rels(kd, archive, slide_part, max_bytes).values():
            if rel_type == REL_NOTES_SLIDE and target in archive._omd_names:
                notes = _xml(kd, archive, target, max_bytes)
                _slide_paragraph_drafts(kd, notes, target, slide_number, drafts, max_segments, max_chars)
    return drafts
