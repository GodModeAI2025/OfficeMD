"""Roh-Markdown: deterministischer Textabzug aus den normalisierten Segmenten, ohne KI.

Das ist kein Distiller-Format, sondern eine lesbare Fassung des Dokumenttexts. Struktur gibt
es nur so weit, wie die Segmente sie tragen: Absätze bei DOCX, Tabellen pro Blatt bei XLSX,
Folien bei PPTX.
"""
from __future__ import annotations

import json
from collections import OrderedDict
from typing import Any, Dict, List


def _yaml(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _front_matter(normalized: Dict[str, Any], fingerprint: str, generated_at: str) -> str:
    source = normalized["source"]
    lines = [
        "---",
        f"title: {_yaml(source.get('file', ''))}",
        "officemd_mode: \"raw\"",
        f"source_type: {_yaml(source.get('type', ''))}",
        f"source_content_sha256: {_yaml(source.get('content_sha256', ''))}",
        f"text_fingerprint: {_yaml(fingerprint)}",
        f"segment_count: {normalized.get('segment_count', len(normalized['segments']))}",
        f"generated_at: {_yaml(generated_at)}",
        "---",
        "",
    ]
    return "\n".join(lines)


def _escape_cell(text: str) -> str:
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def _xlsx(segments: List[Dict[str, Any]]) -> str:
    sheets: "OrderedDict[str, List[List[str]]]" = OrderedDict()
    for segment in segments:
        sheet = segment["selector"].get("sheet", "")
        sheets.setdefault(sheet, []).append(segment["text"].split("\t"))
    out = []
    for sheet, rows in sheets.items():
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        out.append(f"## {sheet}\n")
        out.append("| " + " | ".join(_escape_cell(c) for c in rows[0]) + " |")
        out.append("|" + "---|" * width)
        for row in rows[1:]:
            out.append("| " + " | ".join(_escape_cell(c) for c in row) + " |")
        out.append("")
    return "\n".join(out)


def _page(segment: Dict[str, Any]) -> int:
    for selector in segment.get("selectors", []):
        if selector.get("type") == "PageSelector":
            return int(selector["page"])
    return 0


def _pptx(segments: List[Dict[str, Any]]) -> str:
    out = []
    current = None
    for segment in segments:
        page = _page(segment)
        if page != current:
            out.append(f"\n## Folie {page}\n")
            current = page
        if "notesSlide" in segment.get("locator", ""):
            out.append("> " + segment["text"].replace("\n", "\n> ") + "\n")
        else:
            out.append(segment["text"] + "\n")
    return "\n".join(out).lstrip("\n")


def render(normalized: Dict[str, Any], fingerprint: str, generated_at: str) -> str:
    kind = normalized["source"].get("type")
    segments = normalized["segments"]
    if kind == "xlsx":
        body = _xlsx(segments)
    elif kind == "pptx":
        body = _pptx(segments)
    else:
        body = "\n\n".join(segment["text"] for segment in segments) + "\n"
    return _front_matter(normalized, fingerprint, generated_at) + body
