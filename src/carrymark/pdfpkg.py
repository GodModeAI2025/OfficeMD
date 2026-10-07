"""Markdown als Anhang in PDF-Dateien.

PDF kann Dateien tragen: als eingebettete Datei im Namensbaum ``/EmbeddedFiles``. PDF/A-3 und
ZUGFeRD nutzen genau das, etwa für die XML-Fassung einer Rechnung. Carrymark legt dort
``carrymark.md`` ab, mit Beziehung ``/Alternative`` (eine andere Darstellung desselben
Inhalts) und Medientyp ``text/markdown``. Fingerprint, GUID und Zeitpunkt stehen zusätzlich in
den Dokumentinformationen; fehlt der Anhang, die Einträge aber nicht, ist er verloren gegangen.

Gespeichert wird, indem pypdf das Dokument mit allen Seiten und Objekten übernimmt und neu
schreibt; Seiteninhalt und Text bleiben gleich. Verschlüsselte und signierte PDFs werden
abgelehnt, weil Neuschreiben eine Signatur ungültig machen würde.
"""
from __future__ import annotations

import io
import logging
import os
import re
import stat
import tempfile
import uuid
from pathlib import Path
from typing import Dict, Optional

from . import ooxml
from .ooxml import KnowledgePart, OoxmlError, Payload, UnreadableError, existing_lock, LockedError
from .ooxml import ConcurrentModificationError, MAX_PACKAGE_BYTES

# pypdf meldet reparierbare Strukturfehler realer PDFs als Log-Zeilen; für Carrymark zählt nur,
# ob sich die Datei lesen und schreiben lässt.
logging.getLogger("pypdf").setLevel(logging.CRITICAL)

ATTACHMENT_NAME = "carrymark.md"
INFO_PREFIX = "/Carrymark"
PDF_DATE = re.compile(r"^D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?")


def _iso(pdf_date: Optional[str]) -> Optional[str]:
    """``D:20260930080000+02'00'`` → ``2026-09-30T08:00:00``."""
    match = PDF_DATE.match(pdf_date or "")
    if not match:
        return None
    year, month, day, hour, minute, second = (g or default for g, default in
                                              zip(match.groups(), ("", "01", "01", "00", "00", "00")))
    return f"{year}-{month}-{day}T{hour}:{minute}:{second}"


def _frontmatter_value(document: str, key: str) -> Optional[str]:
    match = re.search(rf'^\s*{re.escape(key)}:\s*"([^"]*)"', document.split("\n---\n", 1)[0], re.M)
    return match.group(1) if match else None


class PdfPackage:
    """Gleiche Schnittstelle wie ``ooxml.Package``, soweit Carrymark sie braucht."""

    kind = "pdf"

    def __init__(self, path: Path) -> None:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError

        self.path = Path(path)
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
        self._data = self.path.read_bytes()
        try:
            self.reader = PdfReader(io.BytesIO(self._data))
            if self.reader.is_encrypted:
                raise UnreadableError("Verschlüsselte PDFs werden nicht unterstützt")
            self._check_unsigned()
            self._info = {str(k): str(v) for k, v in (self.reader.metadata or {}).items()}
        except PdfReadError as exc:
            raise UnreadableError(f"Kein lesbares PDF: {exc}") from exc
        self._writer = None

    def _check_unsigned(self) -> None:
        form = self.reader.trailer["/Root"].get("/AcroForm")
        form = form.get_object() if form is not None else None
        if form is not None and int(form.get("/SigFlags", 0)) & 1:
            raise UnreadableError("Signierte PDFs werden nicht verändert, sonst wäre die Signatur ungültig")

    # -- Lesen ---------------------------------------------------------------------

    def find_knowledge(self) -> Optional[KnowledgePart]:
        for attachment in self.reader.attachment_list:
            if attachment.name != ATTACHMENT_NAME:
                continue
            markdown = attachment.content.decode("utf-8", errors="replace")
            description = str(attachment.description or "")
            guid_match = re.search(r"\{[0-9A-F-]{36}\}", description)
            payload = Payload(
                fingerprint=_frontmatter_value(markdown, "fingerprint") or "",
                embedded_at=_frontmatter_value(markdown, "at") or "",
                markdown=markdown,
                tool_version=_frontmatter_value(markdown, "by") or "",
            )
            return KnowledgePart(item_name=f"EmbeddedFiles/{ATTACHMENT_NAME}", props_name="Info",
                                 guid=guid_match.group(0) if guid_match else None,
                                 payload=payload, registered=True)
        return None

    def read_props(self) -> Dict[str, str]:
        result: Dict[str, str] = {}
        for key, value in self._info.items():
            if key.startswith(INFO_PREFIX):
                result[key[len(INFO_PREFIX):]] = value
        return result

    def saving_app(self) -> Optional[str]:
        return self._info.get("/Producer") or self._info.get("/Creator")

    def core_metadata(self) -> Dict[str, Optional[str]]:
        return {"title": self._info.get("/Title") or None,
                "author": self._info.get("/Author") or None,
                "last_modified": _iso(self._info.get("/ModDate"))}

    # -- Schreiben -------------------------------------------------------------------

    def _writer_for_update(self):
        if self._writer is None:
            from pypdf import PdfReader, PdfWriter

            # Vollständig neu schreiben, Seiten und Objekte unverändert übernommen. Inkrementelles
            # Speichern von pypdf vergibt bei einer zweiten Aktualisierung Objektnummern doppelt.
            self._writer = PdfWriter(clone_from=PdfReader(io.BytesIO(self._data)))
        return self._writer

    def _remove_attachment(self) -> bool:
        writer = self._writer_for_update()
        removed = False
        for attachment in list(writer.attachment_list):
            if attachment.name == ATTACHMENT_NAME:
                attachment.delete()
                removed = True
        return removed

    def _set_info(self, values: Dict[str, Optional[str]]) -> None:
        writer = self._writer_for_update()
        info = {k: v for k, v in self._info.items() if not k.startswith(INFO_PREFIX)}
        info.update({f"{INFO_PREFIX}{k}": v for k, v in values.items() if v is not None})
        writer.metadata = None
        writer.add_metadata(info)
        self._info = info

    def embed(self, payload: Payload) -> str:
        existing = self.find_knowledge()
        guid = (existing.guid if existing and existing.guid
                else self.read_props().get("PartGuid") or "{" + str(uuid.uuid4()).upper() + "}")
        self._remove_attachment()
        from pypdf.generic import NameObject, TextStringObject

        attachment = self._writer_for_update().add_attachment(ATTACHMENT_NAME, (payload.markdown or "").encode("utf-8"))
        attachment.subtype = NameObject("/text/markdown")
        attachment.description = TextStringObject(f"Markdown-Fassung (Carrymark) {guid}")
        attachment.associated_file_relationship = NameObject("/Alternative")
        self._set_info({"Fingerprint": payload.fingerprint, "Version": ooxml.PAYLOAD_VERSION,
                        "PartGuid": guid, "EmbeddedAt": payload.embedded_at})
        return guid

    def remove_knowledge(self, keep_props: bool = False) -> bool:
        removed = self._remove_attachment()
        if not keep_props:
            self._set_info({})
        return removed

    def save(self) -> None:
        if self._writer is None:
            return
        lock = existing_lock(self.path)
        if lock is not None:
            raise LockedError(f"Die Datei ist gesperrt ({lock.name})")
        st = self.path.stat()
        if (st.st_mtime_ns, st.st_size) != self._stat_key:
            raise ConcurrentModificationError("Datei wurde seit dem Lesen verändert")
        fd, tmp_name = tempfile.mkstemp(prefix=".cm-", suffix=".pdf", dir=str(self.path.parent))
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                self._writer.write(handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, self._mode)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
            raise
        self.__init__(self.path)  # frisch einlesen, damit weitere Schritte den neuen Stand sehen


__all__ = ["PdfPackage", "ATTACHMENT_NAME", "OoxmlError"]
