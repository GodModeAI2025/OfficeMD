"""Minimale Office-Dateien und Graphen für Tests und den Offline-Selbsttest, ohne Office."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence
from xml.sax.saxutils import escape

DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

APP_XML = (DECL + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
           "<Application>{app}</Application></Properties>")


def _write(path: Path, parts: Dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", parts.pop("[Content_Types].xml"))
        for name, data in parts.items():
            z.writestr(name, data)
    return path


def _root_rels(main: str) -> str:
    return (DECL + f'<Relationships xmlns="{REL_NS}">'
            f'<Relationship Id="rId1" Type="{R}/officeDocument" Target="{main}"/>'
            f'<Relationship Id="rId2" Type="{R}/extended-properties" Target="docProps/app.xml"/>'
            "</Relationships>")


def make_docx(path: Path, paragraphs: Sequence[str], app: str = "Microsoft Office Word") -> Path:
    body = "".join(f"<w:p><w:r><w:t xml:space=\"preserve\">{escape(p)}</w:t></w:r></w:p>" for p in paragraphs)
    return _write(Path(path), {
        "[Content_Types].xml": DECL + f'<Types xmlns="{CT_NS}">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        "</Types>",
        "_rels/.rels": _root_rels("word/document.xml"),
        "docProps/app.xml": APP_XML.format(app=escape(app)),
        "word/document.xml": DECL + '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>",
        "word/_rels/document.xml.rels": DECL + f'<Relationships xmlns="{REL_NS}"></Relationships>',
    })


def make_xlsx(path: Path, sheets: Dict[str, List[List[str]]]) -> Path:
    shared: List[str] = []
    sheet_parts = {}
    for i, (name, rows) in enumerate(sheets.items(), start=1):
        xml_rows = []
        for r, row in enumerate(rows, start=1):
            cells = []
            for c, value in enumerate(row):
                ref = f"{chr(ord('A') + c)}{r}"
                if value == "":
                    continue
                if value.replace(".", "", 1).isdigit():
                    cells.append(f'<c r="{ref}"><v>{value}</v></c>')
                else:
                    shared.append(value)
                    cells.append(f'<c r="{ref}" t="s"><v>{len(shared) - 1}</v></c>')
            xml_rows.append(f'<row r="{r}">{"".join(cells)}</row>')
        sheet_parts[f"xl/worksheets/sheet{i}.xml"] = (
            DECL + '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f"<sheetData>{''.join(xml_rows)}</sheetData></worksheet>")
    sml = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    sheet_list = "".join(f'<sheet name="{escape(n)}" sheetId="{i}" r:id="rId{i}"/>'
                         for i, n in enumerate(sheets, start=1))
    rels = "".join(f'<Relationship Id="rId{i}" Type="{R}/worksheet" Target="worksheets/sheet{i}.xml"/>'
                   for i in range(1, len(sheets) + 1))
    rels += f'<Relationship Id="rIdS" Type="{R}/sharedStrings" Target="sharedStrings.xml"/>'
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(1, len(sheets) + 1))
    parts = {
        "[Content_Types].xml": DECL + f'<Types xmlns="{CT_NS}">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        f"{overrides}</Types>",
        "_rels/.rels": _root_rels("xl/workbook.xml"),
        "docProps/app.xml": APP_XML.format(app="Microsoft Excel"),
        "xl/workbook.xml": DECL + f'<workbook xmlns="{sml}" xmlns:r="{R}"><sheets>{sheet_list}</sheets></workbook>',
        "xl/_rels/workbook.xml.rels": DECL + f'<Relationships xmlns="{REL_NS}">{rels}</Relationships>',
        "xl/sharedStrings.xml": DECL + f'<sst xmlns="{sml}">'
        + "".join(f"<si><t>{escape(s)}</t></si>" for s in shared) + "</sst>",
    }
    parts.update(sheet_parts)
    return _write(Path(path), parts)


def make_pptx(path: Path, slides: List[List[str]], notes: Optional[Dict[int, str]] = None) -> Path:
    notes = notes or {}
    pml = "http://schemas.openxmlformats.org/presentationml/2006/main"
    dml = "http://schemas.openxmlformats.org/drawingml/2006/main"
    parts: Dict[str, str] = {}
    ids = []
    rels = []
    overrides = []
    for i, paragraphs in enumerate(slides, start=1):
        paras = "".join(f"<a:p><a:r><a:t>{escape(t)}</a:t></a:r></a:p>" for t in paragraphs)
        parts[f"ppt/slides/slide{i}.xml"] = (
            DECL + f'<p:sld xmlns:p="{pml}" xmlns:a="{dml}"><p:cSld><p:spTree>'
            f"<p:sp><p:txBody>{paras}</p:txBody></p:sp></p:spTree></p:cSld></p:sld>")
        slide_rels = ""
        if i in notes:
            parts[f"ppt/notesSlides/notesSlide{i}.xml"] = (
                DECL + f'<p:notes xmlns:p="{pml}" xmlns:a="{dml}"><p:cSld><p:spTree><p:sp><p:txBody>'
                f"<a:p><a:r><a:t>{escape(notes[i])}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:notes>")
            slide_rels = f'<Relationship Id="rId1" Type="{R}/notesSlide" Target="../notesSlides/notesSlide{i}.xml"/>'
        parts[f"ppt/slides/_rels/slide{i}.xml.rels"] = DECL + f'<Relationships xmlns="{REL_NS}">{slide_rels}</Relationships>'
        ids.append(f'<p:sldId id="{255 + i}" r:id="rId{i}"/>')
        rels.append(f'<Relationship Id="rId{i}" Type="{R}/slide" Target="slides/slide{i}.xml"/>')
        overrides.append(f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>')
    parts.update({
        "[Content_Types].xml": DECL + f'<Types xmlns="{CT_NS}">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
        f"{''.join(overrides)}</Types>",
        "_rels/.rels": _root_rels("ppt/presentation.xml"),
        "docProps/app.xml": APP_XML.format(app="Microsoft Office PowerPoint"),
        "ppt/presentation.xml": DECL + f'<p:presentation xmlns:p="{pml}" xmlns:r="{R}">'
        f"<p:sldIdLst>{''.join(ids)}</p:sldIdLst></p:presentation>",
        "ppt/_rels/presentation.xml.rels": DECL + f'<Relationships xmlns="{REL_NS}">{"".join(rels)}</Relationships>',
    })
    return _write(Path(path), parts)


def make_graph(path: Path, *, file_name: str, file_type: str, content_sha256: str,
               quotes: Dict[str, str], source_id: str = "source-doc",
               human_added: Optional[str] = None) -> Path:
    """Kleiner Spec-1.1-Graph: je Zitat ein Knoten, ein Claim und ein Beleg."""
    evidence = []
    nodes = []
    claims = []
    for key, quote in quotes.items():
        evidence.append({
            "id": f"evidence-{key}",
            "source": source_id,
            "selector": {"type": "TextQuoteSelector", "exact": quote},
            "support": "supports",
            "attribution_basis": "source_explicit",
            "excerpt": quote,
            "review_status": "unreviewed",
        })
        nodes.append({
            "id": key,
            "label": key.replace("-", " ").title(),
            "cluster": "main",
            "confidence": "high",
            "definition": f"Begriff {key} aus dem Testdokument.",
            "relevance": "Testknoten.",
            "statements": [quote],
            "temporal": {"source_date": "2026-10", "valid_from": None, "valid_until": None,
                         "temporal_confidence": "unknown"},
            "sources": [source_id],
            "evidence": [f"evidence-{key}"],
            "claim_ids": [f"claim-{key}"],
        })
        claims.append({
            "id": f"claim-{key}",
            "node": key,
            "statement": quote,
            "confidence": "high",
            "origin": "source_stated",
            "evidence": [f"evidence-{key}"],
            "review_status": "unreviewed",
        })
    if human_added:
        evidence.append({
            "id": "evidence-human-note",
            "source": source_id,
            "selector": {"type": "TextQuoteSelector", "exact": human_added},
            "support": "contextualizes",
            "attribution_basis": "human_added",
            "review_status": "reviewed",
        })
    keys = list(quotes)
    edges = []
    if len(keys) > 1:
        edges.append({
            "id": f"{keys[0]}__uses__{keys[1]}",
            "source": keys[0], "target": keys[1], "type": "uses",
            "label": "uses", "weight": 1.0, "confidence": "medium",
            "evidence": [f"evidence-{keys[0]}"], "origin": "source_stated",
        })
    graph = {
        "metadata": {
            "title": f"Wissen zu {file_name}",
            "distiller_version": "4.0",
            "distiller_spec_version": "1.1",
            "sources": [{"id": source_id, "file": file_name, "type": file_type,
                         "content_sha256": content_sha256}],
            "distillation_date": "2026-10-06",
            "domain": "Test",
            "language": "de",
            "depth": "standard",
            "mode": "fresh",
        },
        "evidence": evidence,
        "clusters": [{"id": "main", "label": "Main", "description": "Testcluster", "concepts": keys}],
        "nodes": nodes,
        "claims": claims,
        "edges": edges,
    }
    Path(path).write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
    return Path(path)
