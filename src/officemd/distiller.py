"""Aufruf der Knowledge-Distiller-Skripte als Subprozess.

Die Skripte bleiben unverändert im Submodule ``vendor/knowledge-distiller``. Sie laufen mit
``python -E -s``: Umgebungsvariablen und User-Site werden ignoriert, das Skriptverzeichnis
bleibt im Pfad, weil die Skripte ihre Geschwistermodule (``strict_json``) so importieren.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


class DistillerError(Exception):
    def __init__(self, script: str, returncode: int, stderr: str, stdout: str = "") -> None:
        detail = (stderr or stdout).strip()
        super().__init__(f"{script} ist fehlgeschlagen (Exit {returncode}): {detail}")
        self.script = script
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout


def distiller_root() -> Path:
    override = os.environ.get("OFFICEMD_DISTILLER")
    if override:
        root = Path(override)
    else:
        root = Path(__file__).resolve().parents[2] / "vendor" / "knowledge-distiller"
    if not (root / "scripts" / "extract_source.py").is_file():
        raise FileNotFoundError(
            f"Knowledge Distiller nicht gefunden unter {root}. "
            "Submodule holen: git submodule update --init"
        )
    return root


def script_path(name: str) -> Path:
    return distiller_root() / "scripts" / f"{name}.py"


@dataclass
class Result:
    returncode: int
    stdout: str
    stderr: str


def run(name: str, args: Sequence[str], *, ok_codes: Sequence[int] = (0,), cwd: Optional[Path] = None) -> Result:
    cmd = [sys.executable, "-E", "-s", str(script_path(name)), *[str(a) for a in args]]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(cwd) if cwd else None)
    if proc.returncode not in ok_codes:
        raise DistillerError(name, proc.returncode, proc.stderr, proc.stdout)
    return Result(proc.returncode, proc.stdout, proc.stderr)


# -- Extraktion --------------------------------------------------------------

class ExtractionFailed(Exception):
    """Die Quelle ist nicht lesbar (verschlüsselt, Makros, defekt, nicht unterstützt)."""


def extract(path: Path) -> Dict[str, Any]:
    """Normalisierte Segmente im Format von ``extract_source.py``.

    DOCX läuft über das Distiller-Skript, XLSX und PPTX über die eigenen Adapter, die
    dieselben Sicherheitsregeln und dasselbe Ausgabeformat verwenden.
    """
    path = Path(path).resolve()
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".pptx"):
        from .adapters import ooxml_extract

        try:
            return ooxml_extract.extract(path)
        except ooxml_extract.ExtractionError as exc:
            raise ExtractionFailed(str(exc)) from exc
    try:
        result = run("extract_source", [path.name, "--input-root", str(path.parent), "--format", "json"])
    except DistillerError as exc:
        raise ExtractionFailed(exc.stderr.strip() or str(exc)) from exc
    return json.loads(result.stdout)


# -- Graph-Werkzeuge -----------------------------------------------------------

def build_graph(graph: Path) -> None:
    """Zähler, Cluster, Kanten-IDs und Scores neu berechnen (in place)."""
    run("build_graph", [graph, "--write"])


def validate(graph: Path, prev: Optional[Path] = None) -> Dict[str, Any]:
    args: List[Any] = [graph, "--json"]
    if prev is not None:
        args += ["--prev", prev]
    result = run("validate_knowledge", args, ok_codes=(0, 1))
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise DistillerError("validate_knowledge", result.returncode, result.stderr, result.stdout) from exc


def verify(graph: Path, normalized: Sequence[Path]) -> Dict[str, Any]:
    result = run("verify_evidence", [graph, *normalized, "--json"], ok_codes=(0, 1))
    return json.loads(result.stdout)


def build_md(graph: Path, out: Path) -> str:
    run("build_md", [graph, "-o", out])
    return Path(out).read_text(encoding="utf-8")


def merge(base: Path, incoming: Path, output: Path, versions_dir: Path) -> Result:
    return run("merge_knowledge", [
        base, incoming, "--output", output, "--versions-dir", versions_dir, "--force",
    ])
