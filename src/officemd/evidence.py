"""Belege eines Graphen gegen den aktuellen Dokumenttext prüfen.

Die Datei, in der der Graph steckt, ist per Definition die Quelle ``source_id``. Deshalb wird
diese Quelle mit ``verify_evidence.py --bind SOURCE_ID=EXTRAKTION`` ausdrücklich an die
aktuelle Extraktion gebunden. So lässt sich auch nach Textänderungen zählen, welche Belege noch
auffindbar sind. Der Bericht nennt zusätzlich, ob der Inhalt seit dem Kompilieren gleich
geblieben ist (``normalized_sha256``) oder sogar die Bytes (``content_sha256``).
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from . import distiller


def _digest(value: Any) -> Optional[str]:
    return value.removeprefix("sha256:").lower() if isinstance(value, str) else None


def verify_against(graph: Dict[str, Any], source_id: str, normalized: Dict[str, Any]) -> Dict[str, Any]:
    source = next((s for s in graph.get("metadata", {}).get("sources", [])
                   if isinstance(s, dict) and s.get("id") == source_id), None)
    if source is None:
        raise ValueError(f"Quelle {source_id!r} kommt im Graphen nicht vor")

    with tempfile.TemporaryDirectory(prefix="omd-verify-") as tmp:
        graph_path = Path(tmp) / "graph.knowledge.json"
        norm_path = Path(tmp) / "normalized.json"
        graph_path.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
        norm_path.write_text(json.dumps(normalized, ensure_ascii=False), encoding="utf-8")
        report = distiller.verify(graph_path, [], bind={source_id: norm_path})

    evidence_source = {
        e.get("id"): e.get("source") for e in graph.get("evidence", []) or [] if isinstance(e, dict)
    }
    own = [r for r in report["results"] if evidence_source.get(r["evidence"]) == source_id]
    counts = {name: sum(1 for r in own if r["status"] == name) for name in ("verified", "not_found", "unverifiable")}
    current = normalized["source"]
    if _digest(source.get("content_sha256")) == _digest(current.get("content_sha256")):
        same = "content_sha256"
    elif source.get("normalized_sha256") and _digest(source.get("normalized_sha256")) == _digest(current.get("normalized_sha256")):
        same = "normalized_sha256"
    else:
        same = None
    return {
        "source_id": source_id,
        "total": len(own),
        "counts": counts,
        "missing": [r for r in own if r["status"] != "verified"],
        "other_sources": len(report["results"]) - len(own),
        "binding": {
            "method": "binding",
            "unchanged_since_compile": same,
            "graph_content_sha256": source.get("content_sha256"),
            "graph_normalized_sha256": source.get("normalized_sha256"),
            "current_content_sha256": current.get("content_sha256"),
            "current_normalized_sha256": current.get("normalized_sha256"),
            "note": "Quelle per verify_evidence.py --bind an die aktuelle Extraktion gebunden; ein "
                    "gefundener Beleg zeigt nur, dass die Stelle existiert, nicht dass sie die Aussage stützt.",
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
