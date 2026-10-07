"""Text-Fingerprint über die normalisierten Segmente.

``content_sha256`` des Distillers hasht die rohen Dateibytes. Die ändern sich bei jedem
Speichern in Office (rsids, Metadaten) und durch unseren eigenen Part. Der Fingerprint hier
hängt nur an dem, was der Extraktor als Inhalt sieht: Reihenfolge, Selektoren und Text jedes
Segments. Segment-IDs fließen nicht ein, weil der Distiller den Byte-Hash in sie einrechnet.
Seit Adapter 1.1 liefert der Distiller genau diesen Wert als ``normalized_sha256``; OfficeMD
übernimmt ihn und rechnet nur bei älteren Extraktionen selbst.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict

PREFIX = "omd-text-v1:"


def normalized_digest(normalized: Dict[str, Any]) -> str:
    """Dieselbe Rechnung wie ``normalized_sha256`` im Distiller (ab Adapter 1.1)."""
    items = [[segment.get("selectors"), segment.get("text_sha256")] for segment in normalized["segments"]]
    canonical = json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def text_fingerprint(normalized: Dict[str, Any]) -> str:
    digest = normalized["source"].get("normalized_sha256") or normalized_digest(normalized)
    return PREFIX + digest
