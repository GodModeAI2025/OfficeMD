"""Lesen und Schreiben des OfficeMD-Parts in OOXML-Paketen (DOCX, XLSX, PPTX).

Der Part wird so registriert, wie Office selbst Custom-XML-Datastores anlegt:

* ``customXml/itemN.xml`` mit unserem Wurzelelement ``omd:knowledge``,
* ``customXml/itemPropsN.xml`` mit ``ds:datastoreItem`` und eigener GUID,
* ``customXml/_rels/itemN.xml.rels`` als Verweis vom Item auf die ItemProps,
* eine ``customXml``-Relationship vom Hauptpart (Dokument, Arbeitsmappe, Präsentation),
* ein Override in ``[Content_Types].xml`` für die ItemProps.

Zusätzlich landen Fingerprint, Version und Part-GUID als kleine Einträge in
``docProps/custom.xml``. Fehlt später der große Part, die Einträge aber nicht, ist der
Part beim Speichern verloren gegangen.

Das Modul fasst ausschließlich Paketstruktur an. Dokumentinhalt (``word/document.xml``
usw.) wird nie neu serialisiert, sondern byte-genau übernommen.
"""
from __future__ import annotations

import os
import posixpath
import re
import stat
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Tuple
from xml.sax.saxutils import quoteattr

NS_OMD = "urn:officemd:knowledge:1"
NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
NS_DS = "http://schemas.openxmlformats.org/officeDocument/2006/customXml"
NS_CUSTOM_PROPS = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
NS_VT = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"

_REL_BASE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
REL_OFFICE_DOCUMENT = _REL_BASE + "officeDocument"
REL_CUSTOM_XML = _REL_BASE + "customXml"
REL_CUSTOM_XML_PROPS = _REL_BASE + "customXmlProps"
REL_CUSTOM_PROPERTIES = _REL_BASE + "custom-properties"

CT_ITEM_PROPS = "application/vnd.openxmlformats-officedocument.customXmlProperties+xml"
CT_CUSTOM_PROPERTIES = "application/vnd.openxmlformats-officedocument.custom-properties+xml"
CT_XML = "application/xml"

FMTID_USER_DEFINED = "{D5CDD505-2E9C-101B-9397-08002B2CF9AE}"
PROP_PREFIX = "OfficeMD"
PROP_NAMES = ("Fingerprint", "Version", "PartGuid", "Mode", "EmbeddedAt")
PAYLOAD_VERSION = "1"

XML_DECLARATION = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
DOCTYPE_OR_ENTITY = re.compile(br"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
INVALID_XML_CHARS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
ITEM_NAME = re.compile(r"^customXml/item(\d+)\.xml$")

MAX_PACKAGE_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 10_000

SUPPORTED_SUFFIXES = {".docx": "docx", ".xlsx": "xlsx", ".pptx": "pptx"}
MACRO_SUFFIXES = {".docm", ".xlsm", ".pptm", ".dotm", ".xltm", ".potm", ".ppsm"}


class OoxmlError(Exception):
    """Basisklasse für alle Paketfehler."""


class UnreadableError(OoxmlError):
    """Datei ist kein lesbares OOXML-Paket (verschlüsselt, Makros, defekt, falsches Format)."""


class LockedError(OoxmlError):
    """Office hat die Datei geöffnet (Sperrdatei ``~$…`` vorhanden)."""


class ConcurrentModificationError(OoxmlError):
    """Die Datei hat sich zwischen Lesen und Schreiben verändert."""


@dataclass
class Payload:
    """Inhalt des OfficeMD-Parts."""

    mode: str  # "raw" oder "graph"
    fingerprint: str
    embedded_at: str
    markdown: Optional[str] = None
    graph_json: Optional[str] = None
    source_id: Optional[str] = None
    tool_version: str = ""


@dataclass
class KnowledgePart:
    item_name: str
    props_name: Optional[str]
    guid: Optional[str]
    payload: Payload
    registered: bool  # Relationship vom Hauptpart vorhanden


def office_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in MACRO_SUFFIXES:
        raise UnreadableError(f"Makrofähige Office-Datei ({suffix}) wird bewusst nicht unterstützt")
    if suffix not in SUPPORTED_SUFFIXES:
        raise UnreadableError(f"Kein unterstütztes Office-Format: {suffix or '(ohne Endung)'}")
    return SUPPORTED_SUFFIXES[suffix]


def lock_files(path: Path) -> List[Path]:
    """Mögliche Office-Sperrdateien zu ``path``.

    Word kürzt bei langen Namen die ersten Zeichen weg (``~$`` ersetzt sie), Excel und
    PowerPoint hängen ``~$`` vor den vollen Namen. Wir prüfen alle Varianten.
    """
    name = path.name
    candidates = {"~$" + name}
    if len(name) > 2:
        candidates.add("~$" + name[1:])
        candidates.add("~$" + name[2:])
    return [path.with_name(c) for c in sorted(candidates)]


def existing_lock(path: Path) -> Optional[Path]:
    for candidate in lock_files(path):
        if candidate.exists():
            return candidate
    return None


def _parse_xml(name: str, data: bytes) -> ET.Element:
    if DOCTYPE_OR_ENTITY.search(data):
        raise UnreadableError(f"{name}: DTD- oder Entity-Deklaration ist nicht erlaubt")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise UnreadableError(f"{name}: kein wohlgeformtes XML ({exc})") from exc


KNOWN_PREFIXES = {
    NS_VT: "vt",
    "http://www.w3.org/XML/1998/namespace": "xml",
    "http://schemas.openxmlformats.org/markup-compatibility/2006": "mc",
}


def _serialize(root: ET.Element, default_namespace: Optional[str] = None) -> bytes:
    """Serialisiert Paketstruktur-XML (Rels, Content Types, custom.xml).

    ``ET.tostring(default_namespace=…)`` scheitert an unqualifizierten Attributen, die in
    diesen Parts die Regel sind. Daher ein kleiner eigener Serializer mit festem Default-
    Namespace und stabilen Präfixen.
    """
    prefixes: Dict[str, str] = {}

    def name(qname: str) -> str:
        if not qname.startswith("{"):
            return qname
        ns, local = qname[1:].split("}", 1)
        if ns == default_namespace:
            return local
        if ns not in prefixes:
            prefixes[ns] = KNOWN_PREFIXES.get(ns, f"ns{len(prefixes)}")
        return f"{prefixes[ns]}:{local}"

    def esc(text: str, attr: bool = False) -> str:
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if attr:
            text = text.replace('"', "&quot;").replace("\n", "&#10;").replace("\r", "&#13;").replace("\t", "&#9;")
        return text

    # Erster Durchlauf sammelt die Präfixe, damit das Wurzelelement sie deklarieren kann.
    for element in root.iter():
        name(element.tag)
        for key in element.attrib:
            name(key)
    declarations = f' xmlns="{default_namespace}"' if default_namespace else ""
    declarations += "".join(f' xmlns:{p}="{esc(ns, True)}"' for ns, p in prefixes.items() if p != "xml")

    def render(element: ET.Element, extra: str = "") -> str:
        tag = name(element.tag)
        attrs = extra + "".join(f' {name(k)}="{esc(v, True)}"' for k, v in element.attrib.items())
        children = "".join(render(child) for child in element)
        text = esc(element.text) if element.text else ""
        tail = esc(element.tail) if element.tail else ""
        if not children and not text:
            return f"<{tag}{attrs}/>{tail}"
        return f"<{tag}{attrs}>{text}{children}</{tag}>{tail}"

    return XML_DECLARATION + render(root, declarations).encode("utf-8")


def _cdata(text: str) -> str:
    if INVALID_XML_CHARS.search(text):
        raise ValueError("Inhalt enthält Zeichen, die in XML nicht erlaubt sind")
    return "<![CDATA[" + text.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def rels_name_for(part: str) -> str:
    directory, base = posixpath.split(part)
    return posixpath.join(directory, "_rels", base + ".rels")


def resolve_target(source_part: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(source_part), target))


def relative_target(source_part: str, target_part: str) -> str:
    return posixpath.relpath(target_part, posixpath.dirname(source_part) or ".")


class Package:
    """Ein OOXML-Paket im Speicher, Einträge in Originalreihenfolge."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.kind = office_kind(self.path)
        try:
            st = self.path.stat()
        except OSError as exc:
            raise UnreadableError(f"Datei nicht lesbar: {exc}") from exc
        if not stat.S_ISREG(st.st_mode):
            raise UnreadableError("Keine reguläre Datei")
        if st.st_size > MAX_PACKAGE_BYTES:
            raise UnreadableError("Datei ist größer als 64 MiB")
        self._stat_key = (st.st_mtime_ns, st.st_size)
        self._mode = stat.S_IMODE(st.st_mode)
        self.entries: List[Tuple[zipfile.ZipInfo, bytes]] = []
        try:
            with zipfile.ZipFile(self.path) as archive:
                infos = archive.infolist()
                if len(infos) > MAX_ENTRIES:
                    raise UnreadableError("Zu viele Einträge im Archiv")
                total = 0
                seen = set()
                for info in infos:
                    name = info.filename
                    parts = PurePosixPath(name).parts
                    if (not name or name.startswith("/") or "\\" in name
                            or any(p in ("", ".", "..") for p in parts)):
                        raise UnreadableError(f"Unsicherer Archivname: {name!r}")
                    if name in seen:
                        raise UnreadableError(f"Doppelter Archiveintrag: {name!r}")
                    if info.flag_bits & 0x1:
                        raise UnreadableError("Verschlüsselte Archiveinträge werden nicht unterstützt")
                    seen.add(name)
                    total += info.file_size
                    if total > MAX_PACKAGE_BYTES:
                        raise UnreadableError("Archiv entpackt sich auf mehr als 64 MiB")
                    self.entries.append((info, archive.read(info)))
        except zipfile.BadZipFile as exc:
            # Mit Kennwort geschützte Office-Dateien sind CFB-Container, kein ZIP.
            raise UnreadableError("Kein OOXML-Paket (verschlüsselt oder beschädigt)") from exc
        if "[Content_Types].xml" not in self.names():
            raise UnreadableError("[Content_Types].xml fehlt")
        lowered = {n.lower() for n in self.names()}
        if any(n.endswith("vbaproject.bin") for n in lowered):
            raise UnreadableError("Paket enthält Makros (vbaProject.bin)")
        if b"macroenabled" in self.read("[Content_Types].xml").lower():
            raise UnreadableError("Paket ist als makrofähig deklariert")

    # -- Grundoperationen -------------------------------------------------

    def names(self) -> List[str]:
        return [info.filename for info, _ in self.entries]

    def has(self, name: str) -> bool:
        return any(info.filename == name for info, _ in self.entries)

    def read(self, name: str) -> bytes:
        for info, data in self.entries:
            if info.filename == name:
                return data
        raise KeyError(name)

    def write(self, name: str, data: bytes) -> None:
        for i, (info, _) in enumerate(self.entries):
            if info.filename == name:
                self.entries[i] = (info, data)
                return
        info = zipfile.ZipInfo(name, time.localtime()[:6])
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o600 << 16
        self.entries.append((info, data))

    def delete(self, name: str) -> None:
        self.entries = [(i, d) for i, d in self.entries if i.filename != name]

    def xml(self, name: str) -> ET.Element:
        return _parse_xml(name, self.read(name))

    # -- Relationships -----------------------------------------------------

    def relationships(self, source_part: str) -> List[ET.Element]:
        rels = rels_name_for(source_part) if source_part else "_rels/.rels"
        if not self.has(rels):
            return []
        return list(self.xml(rels).iter(f"{{{NS_PKG_REL}}}Relationship"))

    def main_part(self) -> str:
        for rel in self.relationships(""):
            if rel.get("Type") == REL_OFFICE_DOCUMENT and rel.get("TargetMode") != "External":
                target = resolve_target("", rel.get("Target", ""))
                if self.has(target):
                    return target
        raise UnreadableError("Hauptpart (officeDocument) nicht gefunden")

    def _edit_rels(self, source_part: str):
        name = rels_name_for(source_part) if source_part else "_rels/.rels"
        if self.has(name):
            root = self.xml(name)
        else:
            root = ET.Element(f"{{{NS_PKG_REL}}}Relationships")
        return name, root

    def add_relationship(self, source_part: str, rel_type: str, target_part: str) -> str:
        name, root = self._edit_rels(source_part)
        target = relative_target(source_part, target_part) if source_part else target_part
        for rel in root.iter(f"{{{NS_PKG_REL}}}Relationship"):
            if rel.get("Type") == rel_type and resolve_target(source_part, rel.get("Target", "")) == target_part:
                return rel.get("Id", "")
        used = {rel.get("Id") for rel in root.iter(f"{{{NS_PKG_REL}}}Relationship")}
        n = 1
        while f"rId{n}" in used:
            n += 1
        rel_id = f"rId{n}"
        ET.SubElement(root, f"{{{NS_PKG_REL}}}Relationship",
                      {"Id": rel_id, "Type": rel_type, "Target": target})
        self.write(name, _serialize(root, NS_PKG_REL))
        return rel_id

    def remove_relationships_to(self, source_part: str, target_part: str) -> None:
        name = rels_name_for(source_part) if source_part else "_rels/.rels"
        if not self.has(name):
            return
        root = self.xml(name)
        changed = False
        for rel in list(root):
            if resolve_target(source_part, rel.get("Target", "")) == target_part:
                root.remove(rel)
                changed = True
        if changed:
            self.write(name, _serialize(root, NS_PKG_REL))

    # -- Content Types -----------------------------------------------------

    def _content_types(self) -> ET.Element:
        return self.xml("[Content_Types].xml")

    def ensure_override(self, part: str, content_type: str) -> None:
        root = self._content_types()
        part_name = "/" + part
        for override in root.iter(f"{{{NS_CT}}}Override"):
            if override.get("PartName", "").lower() == part_name.lower():
                if override.get("ContentType") == content_type:
                    return
                override.set("ContentType", content_type)
                break
        else:
            ET.SubElement(root, f"{{{NS_CT}}}Override",
                          {"PartName": part_name, "ContentType": content_type})
        self.write("[Content_Types].xml", _serialize(root, NS_CT))

    def has_xml_default(self) -> bool:
        return any(d.get("Extension", "").lower() == "xml"
                   for d in self._content_types().iter(f"{{{NS_CT}}}Default"))

    def remove_override(self, part: str) -> None:
        root = self._content_types()
        part_name = "/" + part
        removed = False
        for override in list(root):
            if override.tag == f"{{{NS_CT}}}Override" and override.get("PartName", "").lower() == part_name.lower():
                root.remove(override)
                removed = True
        if removed:
            self.write("[Content_Types].xml", _serialize(root, NS_CT))

    # -- Custom Properties (docProps/custom.xml) ---------------------------

    def custom_props_part(self) -> Optional[str]:
        for rel in self.relationships(""):
            if rel.get("Type") == REL_CUSTOM_PROPERTIES:
                target = resolve_target("", rel.get("Target", ""))
                if self.has(target):
                    return target
        return None

    def read_props(self) -> Dict[str, str]:
        part = self.custom_props_part()
        if part is None:
            return {}
        result: Dict[str, str] = {}
        for prop in self.xml(part).iter(f"{{{NS_CUSTOM_PROPS}}}property"):
            name = prop.get("name", "")
            if name.startswith(PROP_PREFIX):
                value = "".join(child.text or "" for child in prop)
                result[name[len(PROP_PREFIX):]] = value
        return result

    def write_props(self, values: Dict[str, str]) -> None:
        part = self.custom_props_part()
        if part is None:
            part = "docProps/custom.xml"
            if self.has(part):  # vorhanden, aber nicht verknüpft
                root = self.xml(part)
            else:
                root = ET.Element(f"{{{NS_CUSTOM_PROPS}}}Properties")
            self.add_relationship("", REL_CUSTOM_PROPERTIES, part)
            self.ensure_override(part, CT_CUSTOM_PROPERTIES)
        else:
            root = self.xml(part)
        props = list(root.iter(f"{{{NS_CUSTOM_PROPS}}}property"))
        pids = [int(p.get("pid", "1")) for p in props if p.get("pid", "").isdigit()]
        next_pid = max([1] + pids) + 1
        by_name = {p.get("name"): p for p in props}
        for key, value in values.items():
            full = PROP_PREFIX + key
            prop = by_name.get(full)
            if prop is None:
                prop = ET.SubElement(root, f"{{{NS_CUSTOM_PROPS}}}property",
                                     {"fmtid": FMTID_USER_DEFINED, "pid": str(next_pid), "name": full})
                next_pid += 1
            for child in list(prop):
                prop.remove(child)
            ET.SubElement(prop, f"{{{NS_VT}}}lpwstr").text = value
        self.write(part, _serialize(root, NS_CUSTOM_PROPS))

    def remove_props(self) -> None:
        part = self.custom_props_part()
        if part is None:
            return
        root = self.xml(part)
        changed = False
        for prop in list(root):
            if prop.get("name", "").startswith(PROP_PREFIX):
                root.remove(prop)
                changed = True
        if changed:
            self.write(part, _serialize(root, NS_CUSTOM_PROPS))

    # -- Knowledge-Part ----------------------------------------------------

    def _item_props_name(self, item_name: str) -> Optional[str]:
        for rel in self.relationships(item_name):
            if rel.get("Type") == REL_CUSTOM_XML_PROPS:
                target = resolve_target(item_name, rel.get("Target", ""))
                if self.has(target):
                    return target
        return None

    def find_knowledge(self) -> Optional[KnowledgePart]:
        for name in self.names():
            if not ITEM_NAME.match(name):
                continue
            data = self.read(name)
            if NS_OMD.encode() not in data:
                continue
            root = _parse_xml(name, data)
            if root.tag != f"{{{NS_OMD}}}knowledge":
                continue
            graph = root.find(f"{{{NS_OMD}}}graph")
            markdown = root.find(f"{{{NS_OMD}}}markdown")
            payload = Payload(
                mode=root.get("mode", "raw"),
                fingerprint=root.get("fingerprint", ""),
                embedded_at=root.get("embedded-at", ""),
                source_id=root.get("source-id"),
                tool_version=root.get("tool-version", ""),
                graph_json=graph.text if graph is not None else None,
                markdown=markdown.text if markdown is not None else None,
            )
            props_name = self._item_props_name(name)
            guid = None
            if props_name:
                guid = self.xml(props_name).get(f"{{{NS_DS}}}itemID")
            main = self.main_part()
            registered = any(
                rel.get("Type") == REL_CUSTOM_XML and resolve_target(main, rel.get("Target", "")) == name
                for rel in self.relationships(main)
            )
            return KnowledgePart(name, props_name, guid, payload, registered)
        return None

    def _free_item_number(self) -> int:
        n = 1
        while self.has(f"customXml/item{n}.xml") or self.has(f"customXml/itemProps{n}.xml"):
            n += 1
        return n

    def embed(self, payload: Payload) -> str:
        """Schreibt oder ersetzt den Knowledge-Part und gibt die Datastore-GUID zurück."""
        existing = self.find_knowledge()
        if existing is not None:
            item_name = existing.item_name
            n = int(ITEM_NAME.match(item_name).group(1))
            guid = existing.guid or "{" + str(uuid.uuid4()).upper() + "}"
        else:
            n = self._free_item_number()
            item_name = f"customXml/item{n}.xml"
            guid = "{" + str(uuid.uuid4()).upper() + "}"
        props_name = (existing.props_name if existing and existing.props_name
                      else f"customXml/itemProps{n}.xml")

        self.write(item_name, _item_xml(payload))
        self.write(props_name, _item_props_xml(guid))
        self.add_relationship(item_name, REL_CUSTOM_XML_PROPS, props_name)
        self.ensure_override(props_name, CT_ITEM_PROPS)
        if not self.has_xml_default():
            self.ensure_override(item_name, CT_XML)
        self.add_relationship(self.main_part(), REL_CUSTOM_XML, item_name)
        self.write_props({
            "Fingerprint": payload.fingerprint,
            "Version": PAYLOAD_VERSION,
            "PartGuid": guid,
            "Mode": payload.mode,
            "EmbeddedAt": payload.embedded_at,
        })
        return guid

    def remove_knowledge(self, keep_props: bool = False) -> bool:
        part = self.find_knowledge()
        if part is not None:
            self.remove_relationships_to(self.main_part(), part.item_name)
            for name in (part.item_name, part.props_name, rels_name_for(part.item_name)):
                if name:
                    self.delete(name)
                    self.remove_override(name)
        if not keep_props:
            self.remove_props()
        return part is not None

    # -- Speichern ---------------------------------------------------------

    def save(self, target: Optional[Path] = None) -> None:
        """Atomar speichern: temporäre Datei im selben Ordner, dann ``os.replace``."""
        target = Path(target) if target else self.path
        lock = existing_lock(target)
        if lock is not None:
            raise LockedError(f"Office hat die Datei geöffnet ({lock.name}); bitte zuerst schließen")
        if target == self.path:
            st = self.path.stat()
            if (st.st_mtime_ns, st.st_size) != self._stat_key:
                raise ConcurrentModificationError("Datei wurde seit dem Lesen verändert")
        fd, tmp_name = tempfile.mkstemp(prefix=".omd-", suffix=target.suffix, dir=str(target.parent))
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                with zipfile.ZipFile(handle, "w") as archive:
                    for info, data in self._ordered_entries():
                        clone = zipfile.ZipInfo(info.filename, info.date_time)
                        clone.compress_type = info.compress_type
                        clone.external_attr = info.external_attr
                        clone.comment = info.comment
                        archive.writestr(clone, data)
                handle.flush()
                os.fsync(handle.fileno())
            with zipfile.ZipFile(tmp) as check:
                bad = check.testzip()
                if bad is not None:
                    raise OoxmlError(f"Prüfsumme defekt nach dem Schreiben: {bad}")
            os.chmod(tmp, self._mode)
            os.replace(tmp, target)
        except BaseException:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
            raise
        st = target.stat()
        self._stat_key = (st.st_mtime_ns, st.st_size)
        self.path = target

    def _ordered_entries(self) -> List[Tuple[zipfile.ZipInfo, bytes]]:
        # [Content_Types].xml steht in Office-Dateien vorn; das behalten wir bei.
        first = [e for e in self.entries if e[0].filename == "[Content_Types].xml"]
        rest = [e for e in self.entries if e[0].filename != "[Content_Types].xml"]
        return first + rest


def _item_xml(payload: Payload) -> bytes:
    attrs = {
        "version": PAYLOAD_VERSION,
        "mode": payload.mode,
        "fingerprint": payload.fingerprint,
        "embedded-at": payload.embedded_at,
        "tool-version": payload.tool_version,
    }
    if payload.source_id:
        attrs["source-id"] = payload.source_id
    attr_text = "".join(f" {k}={quoteattr(v)}" for k, v in attrs.items())
    parts = [f'<omd:knowledge xmlns:omd="{NS_OMD}"{attr_text}>']
    if payload.graph_json is not None:
        parts.append(f'<omd:graph format="knowledge.json">{_cdata(payload.graph_json)}</omd:graph>')
    if payload.markdown is not None:
        parts.append(f"<omd:markdown>{_cdata(payload.markdown)}</omd:markdown>")
    parts.append("</omd:knowledge>")
    return XML_DECLARATION + "".join(parts).encode("utf-8")


def _item_props_xml(guid: str) -> bytes:
    return XML_DECLARATION + (
        f'<ds:datastoreItem ds:itemID="{guid}" xmlns:ds="{NS_DS}">'
        f'<ds:schemaRefs><ds:schemaRef ds:uri="{NS_OMD}"/></ds:schemaRefs>'
        "</ds:datastoreItem>"
    ).encode("utf-8")
