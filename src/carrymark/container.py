"""Öffnet eine Datei als Office-Paket oder PDF; beide haben dieselbe Schnittstelle."""
from __future__ import annotations

from pathlib import Path
from typing import Union

from . import ooxml

SUPPORTED_SUFFIXES = {**ooxml.SUPPORTED_SUFFIXES, ".pdf": "pdf"}


def open_package(path: Path) -> Union[ooxml.Package, "pdfpkg.PdfPackage"]:  # noqa: F821
    if Path(path).suffix.lower() == ".pdf":
        from .pdfpkg import PdfPackage

        return PdfPackage(path)
    return ooxml.Package(path)
