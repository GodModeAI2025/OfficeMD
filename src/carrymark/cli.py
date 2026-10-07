"""Kommandozeile ``carrymark``."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, List, Optional

from . import __version__


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _print_check(report: dict) -> None:
    lock = "  [gesperrt]" if report.get("locked") else ""
    print(f"{report['label']:<13} {report['file']}{lock}")
    print(f"              {report['message']}")
    for warning in report.get("warnings", []):
        print(f"  Hinweis:    {warning}")
    for cause in report.get("possible_causes", []):
        print(f"  Mögliche Ursache: {cause}")


def cmd_check(args) -> int:
    from . import ops

    files = ops.office_files([Path(p) for p in args.paths])
    if not files:
        print("Keine DOCX-, XLSX- oder PPTX-Dateien gefunden.", file=sys.stderr)
        return 2
    reports = [ops.check(f) for f in files]
    if args.json:
        _print_json(reports if len(reports) > 1 or Path(args.paths[0]).is_dir() else reports[0])
    else:
        for report in reports:
            _print_check(report)
    states = {r["state"] for r in reports}
    if ops.UNREADABLE in states or ops.LOST in states:
        return 3
    if ops.STALE in states:
        return 1
    return 0


def cmd_convert(args) -> int:
    from . import converter

    conv = converter.convert(Path(args.file))
    text = conv.body if args.plain else conv.document(Path(args.file).name)
    _write_or_print(text, args.output)
    return 0


def cmd_embed(args) -> int:
    from . import ops

    for f in ops.office_files([Path(p) for p in args.paths]):
        result = ops.embed(f)
        if args.json:
            _print_json(result)
        else:
            print(f"Eingebettet: {result['file']} ({result['characters']} Zeichen, {result['converter']})")
    return 0


def cmd_sync(args) -> int:
    from . import ops, sync

    files = ops.office_files([Path(p) for p in args.paths])
    if not files:
        print("Keine DOCX-, XLSX- oder PPTX-Dateien gefunden.", file=sys.stderr)
        return 2
    results = []
    failed = False
    for f in files:
        try:
            results.append(sync.sync_file(f, dry_run=args.dry_run))
        except ops.OpError as exc:
            failed = True
            results.append({"file": str(f), "action": "error", "message": str(exc)})
    if args.json:
        _print_json(results)
    else:
        for r in results:
            after = ops.STATE_LABELS.get(r.get("after"), "-") if r.get("after") else "Fehler"
            print(f"{after:<13} {Path(r['file']).name}: {r['message']}")
    return 2 if failed else 0


def cmd_render(args) -> int:
    from . import converter, ops

    text = ops.render(Path(args.file))
    if args.plain:
        text = converter.split_frontmatter(text)[1]
    _write_or_print(text, args.output)
    return 0


def cmd_export(args) -> int:
    from . import ops

    out = Path(args.output) if args.output else None
    results = ops.export([Path(p) for p in args.paths], out, okf=args.okf)
    if args.json:
        _print_json(results)
    else:
        for r in results:
            print(f"Fehler  {r['file']}: {r['error']}" if "error" in r else f"{r['markdown']}")
        if args.okf and out:
            print(f"OKF-Bundle: {out / 'index.md'}")
    return 2 if any("error" in r for r in results) else 0


def cmd_restore(args) -> int:
    from . import ops

    result = ops.restore(Path(args.file))
    print(f"Wiederhergestellt aus {result['restored_from']}")
    return 0


def cmd_strip(args) -> int:
    from . import ops

    removed = ops.strip(Path(args.file), keep_props=args.keep_props)
    print("Part entfernt." if removed else "Kein Part vorhanden.")
    return 0


def cmd_selftest(args) -> int:
    from . import selftest

    return selftest.main(office=args.office, apps=args.apps, outdir=Path(args.outdir), keep=args.keep)


def cmd_setup(args) -> int:
    """Legt .venv mit Python 3.12 und markitdown an. Läuft auch unter Python 3.9."""
    import shutil
    import subprocess

    root = Path(__file__).resolve().parents[2]
    venv = root / ".venv"
    uv = shutil.which("uv")
    if uv:
        steps = [[uv, "venv", str(venv), "--python", args.python, "--allow-existing"],
                 [uv, "pip", "install", "--python", str(venv / "bin" / "python"), "-e", f"{root}[test]"]]
    else:
        if sys.version_info < (3, 10):
            print("Ohne uv braucht die Einrichtung Python 3.10 oder neuer. uv: https://docs.astral.sh/uv/",
                  file=sys.stderr)
            return 2
        steps = [[sys.executable, "-m", "venv", str(venv)],
                 [str(venv / "bin" / "python"), "-m", "pip", "install", "-e", f"{root}[test]"]]
    for step in steps:
        print("$ " + " ".join(step))
        if subprocess.run(step).returncode != 0:
            return 2
    print(f"Fertig. ./carrymark nutzt ab jetzt {venv / 'bin' / 'python'}.")
    return 0


def _write_or_print(text: str, output: Optional[str]) -> None:
    if output:
        Path(output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="carrymark",
        description="Markdown (via microsoft/markitdown) in Office-Dateien einbetten und aktuell halten.")
    parser.add_argument("--version", action="version", version=f"carrymark {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check", help="Zustand einer Datei oder eines Ordners anzeigen")
    p.add_argument("paths", nargs="+")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("sync", help="Prüfen und bei Bedarf neu einbetten oder wiederherstellen")
    p.add_argument("paths", nargs="+")
    p.add_argument("--dry-run", action="store_true", help="nur anzeigen, was passieren würde")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("embed", help="Markdown erzeugen und einbetten")
    p.add_argument("paths", nargs="+")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_embed)

    p = sub.add_parser("convert", help="Markdown erzeugen und ausgeben, ohne einzubetten")
    p.add_argument("file")
    p.add_argument("-o", "--output")
    p.add_argument("--plain", action="store_true", help="ohne OKF-Frontmatter")
    p.set_defaults(func=cmd_convert)

    p = sub.add_parser("render", help="Eingebettetes Markdown ausgeben")
    p.add_argument("file")
    p.add_argument("-o", "--output")
    p.add_argument("--plain", action="store_true", help="ohne OKF-Frontmatter")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("export", help="Markdown als .md-Dateien oder als OKF-Bundle exportieren")
    p.add_argument("paths", nargs="+")
    p.add_argument("-o", "--output", help="Zielordner (Standard: neben den Dateien)")
    p.add_argument("--okf", action="store_true", help="als Open-Knowledge-Format-Bundle mit index.md")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("restore", help="Verlorenen Part aus dem Sidecar wiederherstellen")
    p.add_argument("file")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("strip", help="Part entfernen")
    p.add_argument("file")
    p.add_argument("--keep-props", action="store_true",
                   help="Fingerprint-Eintrag behalten (simuliert den Zustand Verloren)")
    p.set_defaults(func=cmd_strip)

    p = sub.add_parser("selftest", help="Roundtrip-Selbsttest")
    p.add_argument("--office", action="store_true", help="Word, Excel und PowerPoint per AppleScript speichern lassen")
    p.add_argument("--apps", nargs="+", default=["word", "excel", "powerpoint"],
                   choices=["word", "excel", "powerpoint"])
    p.add_argument("--outdir", default="compat")
    p.add_argument("--keep", action="store_true", help="Testdateien behalten")
    p.set_defaults(func=cmd_selftest)

    p = sub.add_parser("setup", help="Python-Umgebung mit markitdown einrichten (.venv)")
    p.add_argument("--python", default="3.12")
    p.set_defaults(func=cmd_setup)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "setup" and sys.version_info < (3, 10):
        print("Carrymark braucht Python 3.10 oder neuer (für markitdown). Einmalig: ./carrymark setup",
              file=sys.stderr)
        return 2
    from . import converter, ops

    try:
        return args.func(args)
    except (ops.OpError, converter.ConversionError, FileNotFoundError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
