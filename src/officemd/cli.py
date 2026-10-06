"""Kommandozeile ``officemd``."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, List, Optional

from . import __version__, distiller, ops


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _print_check(report: dict) -> None:
    lock = "  [gesperrt]" if report.get("locked") else ""
    mode = f" ({report['mode']})" if report.get("mode") else ""
    print(f"{report['label']:<13} {report['file']}{mode}{lock}")
    print(f"              {report['message']}")
    for warning in report.get("warnings", []):
        print(f"  Hinweis:    {warning}")
    for cause in report.get("possible_causes", []):
        print(f"  Mögliche Ursache: {cause}")


def cmd_check(args) -> int:
    files = ops.office_files([Path(p) for p in args.paths])
    if not files:
        print("Keine DOCX-, XLSX- oder PPTX-Dateien gefunden.", file=sys.stderr)
        return 2
    reports = [ops.check(f, with_evidence=not args.no_evidence) for f in files]
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


def cmd_extract(args) -> int:
    path = Path(args.file)
    normalized = distiller.extract(path)
    if args.markdown:
        from . import rawmd
        from .fingerprint import text_fingerprint

        text = rawmd.render(normalized, text_fingerprint(normalized), ops.now())
    else:
        text = json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


def cmd_embed(args) -> int:
    result = ops.embed(Path(args.file), Path(args.graph) if args.graph else None,
                       source_id=args.source_id, allow_missing_evidence=args.allow_missing_evidence,
                       replace=args.replace, include_markdown=not args.no_markdown)
    if args.json:
        _print_json(result)
    else:
        print(f"Eingebettet ({result['mode']}): {result['file']}")
        print(f"  Fingerprint: {result['fingerprint']}")
        print(f"  Part-GUID:   {result['guid']}")
        print(f"  Sidecar:     {result['sidecar']}")
        ev = result.get("evidence")
        if ev:
            c = ev["counts"]
            print(f"  Belege:      {c['verified']} von {ev['total']} gefunden"
                  + (f", {c['not_found']} fehlen" if c["not_found"] else ""))
    return 0


def cmd_verify(args) -> int:
    report = ops.check(Path(args.file))
    ev = report.get("evidence")
    if args.json:
        _print_json({"state": report["state"], "evidence": ev})
    elif ev is None:
        print(f"{report['label']}: keine Belege zu prüfen ({report['message']})")
    else:
        c = ev["counts"]
        print(f"{c['verified']} von {ev['total']} Belegen gefunden, {c['not_found']} nicht auffindbar, "
              f"{c['unverifiable']} nicht prüfbar.")
        for item in ev["missing"]:
            print(f"  {item['status'].upper():<13} {item['evidence']}: {item['detail']}")
    if ev is None:
        return 2
    return 1 if ev["counts"]["not_found"] else 0


def cmd_update(args) -> int:
    result = ops.update(Path(args.file), Path(args.graph), allow_missing_evidence=args.allow_missing_evidence)
    if args.json:
        _print_json(result)
    else:
        print(f"Zusammengeführt und neu eingebettet: {result['file']}")
        print(f"  Diff:      {result['merge']['diff_md']}")
        print(f"  Versionen: {result['merge']['versions']}")
    return 0


def cmd_render(args) -> int:
    text = ops.render(Path(args.file))
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


def cmd_restore(args) -> int:
    result = ops.restore(Path(args.file))
    print(f"Wiederhergestellt ({result['mode']}) aus {result['restored_from']}")
    return 0


def cmd_strip(args) -> int:
    removed = ops.strip(Path(args.file), keep_props=args.keep_props)
    print("Part entfernt." if removed else "Kein Part vorhanden.")
    return 0


def cmd_selftest(args) -> int:
    from . import selftest

    return selftest.main(office=args.office, apps=args.apps, outdir=Path(args.outdir),
                         keep=args.keep)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="officemd", description="Wissen in Office-Dateien einbetten und prüfen.")
    parser.add_argument("--version", action="version", version=f"officemd {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check", help="Zustand einer Datei oder eines Ordners anzeigen")
    p.add_argument("paths", nargs="+")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-evidence", action="store_true", help="Belege nicht prüfen (schneller)")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("extract", help="Normalisierte Segmente oder Roh-Markdown ausgeben")
    p.add_argument("file")
    p.add_argument("--markdown", action="store_true", help="Roh-Markdown statt JSON")
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("embed", help="Roh-Markdown oder Wissensgraph einbetten")
    p.add_argument("file")
    p.add_argument("--graph", help=".knowledge.json; ohne Angabe wird Roh-Markdown eingebettet")
    p.add_argument("--source-id", help="Graph-Quelle, die für diese Datei steht")
    p.add_argument("--allow-missing-evidence", action="store_true")
    p.add_argument("--replace", action="store_true", help="vorhandenen Graphen bewusst ersetzen (wird archiviert)")
    p.add_argument("--no-markdown", action="store_true", help="kein gerendertes Markdown mit einbetten")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_embed)

    p = sub.add_parser("verify", help="Belege gegen den aktuellen Text prüfen")
    p.add_argument("file")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("update", help="Neuen Graphen additiv mergen und neu einbetten")
    p.add_argument("file")
    p.add_argument("graph")
    p.add_argument("--allow-missing-evidence", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("render", help="Markdown aus dem eingebetteten Wissen ausgeben")
    p.add_argument("file")
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_render)

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
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ops.OpError, distiller.ExtractionFailed, distiller.DistillerError, FileNotFoundError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
