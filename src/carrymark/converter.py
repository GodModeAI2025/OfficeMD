"""Markdown aus Office-Dateien mit microsoft/markitdown, verpackt als OKF-Dokument.

markitdown (https://github.com/microsoft/markitdown, MIT) wandelt DOCX, XLSX und PPTX in
Markdown um. Carrymark ergänzt eine Frontmatter nach dem Open Knowledge Format
(https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md, v0.2):

* Pflichtfeld ``type`` und die empfohlenen Felder ``title``, ``resource``, ``tags``;
* ``sources`` mit Titel, Autor und Änderungsdatum aus ``docProps/core.xml``;
* ``generated`` mit ``by: carrymark/<version>`` und Zeitpunkt;
* eigene Felder unter ``carrymark:`` (Fingerprint, Konverter), was OKF ausdrücklich erlaubt.

Der Fingerprint hängt nur am Markdown-Text, nicht an der Frontmatter. Sonst würde allein der
Zeitstempel in ``generated.at`` jede Datei als veraltet melden.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import __version__, ooxml

FINGERPRINT_PREFIX = "cm-md-v1:"
OKF_VERSION = "0.2"
OKF_TYPE = "Office Document"
NS_CORE = {
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
}


class ConversionError(Exception):
    pass


@dataclass
class Conversion:
    body: str            # reines Markdown von markitdown, normalisiert
    fingerprint: str
    converter: str       # z. B. "markitdown/0.1.8"
    metadata: Dict[str, Optional[str]]

    def document(self, file_name: str, generated_at: Optional[str] = None) -> str:
        """Vollständiges OKF-Dokument: Frontmatter plus Markdown."""
        return frontmatter(self, file_name, generated_at or now()) + self.body


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def markitdown_version() -> str:
    try:
        import markitdown
    except ImportError as exc:
        raise ConversionError(
            "markitdown ist nicht installiert. Einmalig einrichten: ./carrymark setup "
            "(oder pip install 'markitdown[docx,xlsx,pptx]')") from exc
    return getattr(markitdown, "__version__", "unbekannt")


def normalize(markdown: str) -> str:
    """Zeilenenden vereinheitlichen, Leerzeichen am Zeilenende und Leerzeilenblöcke kürzen."""
    text = markdown.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip("\n")
    return text + "\n" if text else ""


def fingerprint(body: str) -> str:
    return FINGERPRINT_PREFIX + hashlib.sha256(normalize(body).encode("utf-8")).hexdigest()


def core_metadata(pkg: ooxml.Package) -> Dict[str, Optional[str]]:
    """Titel, Autor und Änderungsdatum aus docProps/core.xml, sofern vorhanden."""
    root = pkg.optional_xml("docProps/core.xml")

    def text(path: str) -> Optional[str]:
        node = root.find(path, NS_CORE) if root is not None else None
        value = (node.text or "").strip() if node is not None else ""
        return value or None

    return {"title": text("dc:title"), "author": text("dc:creator"),
            "last_modified": text("dcterms:modified")}


_MARKITDOWN = None


def _markitdown():
    """Eine MarkItDown-Instanz für alle Umwandlungen eines Laufs."""
    global _MARKITDOWN
    if _MARKITDOWN is None:
        from markitdown import MarkItDown

        _MARKITDOWN = MarkItDown(enable_plugins=False)
    return _MARKITDOWN


def convert(path: Path, pkg: Optional[ooxml.Package] = None) -> Conversion:
    """Markdown für ``path``. ``pkg``: bereits geöffnetes Paket, spart das zweite Entpacken."""
    path = Path(path)
    if pkg is None:
        try:
            pkg = ooxml.Package(path)  # weist Makros, Verschlüsselung und kaputte Archive vorher ab
        except ooxml.OoxmlError as exc:
            raise ConversionError(str(exc)) from exc
    version = markitdown_version()
    try:
        result = _markitdown().convert_local(str(path))
    except Exception as exc:  # markitdown fasst Konverterfehler unterschiedlich zusammen
        raise ConversionError(f"markitdown konnte die Datei nicht umwandeln: {exc}") from exc
    body = normalize(result.text_content or "")
    return Conversion(body=body, fingerprint=fingerprint(body), converter=f"markitdown/{version}",
                      metadata=core_metadata(pkg))


# -- Frontmatter (YAML) ------------------------------------------------------------------

def _q(value: Any) -> str:
    """JSON-Strings sind gültige YAML-Skalare und sicher gequotet."""
    return json.dumps(value, ensure_ascii=False)


def frontmatter(conv: Conversion, file_name: str, generated_at: str) -> str:
    kind = Path(file_name).suffix.lower().lstrip(".")
    meta = conv.metadata
    title = meta.get("title") or Path(file_name).stem
    lines: List[str] = [
        "---",
        f"type: {_q(OKF_TYPE)}",
        f"title: {_q(title)}",
        f"resource: {_q(file_name)}",
        f"tags: [{_q(kind)}]",
        "sources:",
        f"  - id: {_q('document')}",
        f"    resource: {_q(file_name)}",
    ]
    for key in ("title", "author", "last_modified"):
        if meta.get(key):
            lines.append(f"    {key}: {_q(meta[key])}")
    lines += [
        f"generated: {{ by: {_q('carrymark/' + __version__)}, at: {_q(generated_at)} }}",
        "carrymark:",
        f"  fingerprint: {_q(conv.fingerprint)}",
        f"  converter: {_q(conv.converter)}",
        "---",
        "",
    ]
    return "\n".join(lines)


def split_frontmatter(document: str) -> Tuple[str, str]:
    """(Frontmatter inklusive Trennern, Markdown-Text)."""
    if document.startswith("---\n"):
        end = document.find("\n---\n", 4)
        if end != -1:
            return document[: end + 5], document[end + 5:].lstrip("\n")
    return "", document


def okf_index(entries: List[Tuple[str, str, str]], title: str = "Dokumente") -> str:
    """Bundle-Root ``index.md``: einzige erlaubte Frontmatter ist ``okf_version``.

    ``entries``: (Titel, relativer Link, Kurzbeschreibung).
    """
    lines = ["---", f"okf_version: {_q(OKF_VERSION)}", "---", "", f"# {title}", ""]
    for name, link, description in entries:
        label = name.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
        lines.append(f"* [{label}]({link}) - {description}" if description else f"* [{label}]({link})")
    return "\n".join(lines) + "\n"
