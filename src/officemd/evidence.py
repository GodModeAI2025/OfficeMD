"""Belege eines Graphen gegen den aktuellen Dokumenttext prüfen.

``verify_evidence.py`` ordnet Graph-Quelle und Extraktion nur über identisches
``content_sha256`` (Byte-Hash) zu. Nach jedem Speichern in Office und nach dem Einbetten
stimmt der nicht mehr. Wir binden deshalb in temporären Kopien genau die eine Quelle, die für
diese Datei steht (``source_id``), an die frische Extraktion und lassen dann das unveränderte
Distiller-Skript prüfen. Belege anderer Quellen bleiben unangetastet und damit
``unverifiable``. Im Bericht steht immer, dass so zugeordnet wurde.
"""
from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from . import distiller


def verify_against(graph: Dict[str, Any], source_id: str, normalized: Dict[str, Any]) -> Dict[str, Any]:
    current_digest = normalized["source"]["content_sha256"]
    graph_copy = copy.deepcopy(graph)
    original_digest: Optional[str] = None
    for source in graph_copy.get("metadata", {}).get("sources", []):
        if isinstance(source, dict) and source.get("id") == source_id:
            original_digest = source.get("content_sha256")
            source["content_sha256"] = current_digest
            break
    else:
        raise ValueError(f"Quelle {source_id!r} kommt im Graphen nicht vor")

    with tempfile.TemporaryDirectory(prefix="omd-verify-") as tmp:
        graph_path = Path(tmp) / "graph.knowledge.json"
        norm_path = Path(tmp) / "normalized.json"
        graph_path.write_text(json.dumps(graph_copy, ensure_ascii=False), encoding="utf-8")
        norm_path.write_text(json.dumps(normalized, ensure_ascii=False), encoding="utf-8")
        report = distiller.verify(graph_path, [norm_path])

    evidence_source = {
        e.get("id"): e.get("source") for e in graph.get("evidence", []) or [] if isinstance(e, dict)
    }
    own = [r for r in report["results"] if evidence_source.get(r["evidence"]) == source_id]
    counts = {name: sum(1 for r in own if r["status"] == name) for name in ("verified", "not_found", "unverifiable")}
    return {
        "source_id": source_id,
        "total": len(own),
        "counts": counts,
        "missing": [r for r in own if r["status"] != "verified"],
        "other_sources": len(report["results"]) - len(own),
        "binding": {
            "method": "source_id" if original_digest != current_digest else "content_sha256",
            "graph_content_sha256": original_digest,
            "current_content_sha256": current_digest,
            "note": "Quelle über source_id an die aktuelle Extraktion gebunden; ein gefundener Beleg "
                    "zeigt nur, dass die Stelle existiert, nicht dass sie die Aussage stützt.",
        },
    }


def pick_source_id(graph: Dict[str, Any], normalized: Dict[str, Any], file_name: str,
                   requested: Optional[str] = None) -> str:
    sources = [s for s in graph.get("metadata", {}).get("sources", []) if isinstance(s, dict)]
    ids = [s.get("id") for s in sources]
    if requested:
        if requested not in ids:
            raise ValueError(f"--source-id {requested!r} kommt im Graphen nicht vor (vorhanden: {', '.join(map(str, ids))})")
        return requested
    digest = normalized["source"]["content_sha256"]
    by_digest = [s["id"] for s in sources if str(s.get("content_sha256", "")).removeprefix("sha256:").lower() == digest]
    if len(by_digest) == 1:
        return by_digest[0]
    by_name = [s["id"] for s in sources if Path(str(s.get("file", ""))).name == file_name]
    if len(by_name) == 1:
        return by_name[0]
    if len(sources) == 1:
        return sources[0]["id"]
    raise ValueError(
        "Welche Graph-Quelle steht für diese Datei? Bitte --source-id angeben "
        f"(vorhanden: {', '.join(map(str, ids))})"
    )
