"""Kommandozeile ``officemd``."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, List, Optional

from . import __version__, compiler, config, distiller, ops, providers


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



def _log(message: str) -> None:
    print(f"  {message}", file=sys.stderr, flush=True)


def cmd_config(args) -> int:
    cfg = config.load()
    if args.action == "show":
        info = config.describe(cfg)
        if args.json:
            _print_json(info)
            return 0
        print(f"Konfiguration: {info['config_path']}")
        print(f"  provider        {info['provider'] or '(nicht gesetzt)'}")
        print(f"  mode            {info['mode']}")
        print(f"  depth           {info['depth']}")
        print(f"  max_input_chars {info['max_input_chars']}")
        print(f"  section_chars   {info['section_chars']}")
        print(f"  repair_rounds   {info['repair_rounds']}")
        for name, p in info["providers"].items():
            extra = ", fallbacks" if p.get("fallbacks") else ""
            sdk = "" if p["sdk_available"] else ", SDK fehlt (./officemd setup-ai)"
            print(f"  {name:<15} {p['model']} (effort {p['effort']}{extra}), Key: {p['key_source']}{sdk}")
        print(f"  Keys liegen im: {'macOS-Schlüsselbund' if info['key_store'] == 'keychain' else 'credentials.json (0600)'}")
        return 0
    if args.action == "set":
        if len(args.values) != 2:
            raise config.ConfigError("Aufruf: officemd config set SCHLÜSSEL WERT")
        config.save(config.set_value(cfg, args.values[0], args.values[1]))
        print(f"{args.values[0]} = {args.values[1]}")
        return 0
    if args.action in ("set-key", "delete-key", "test"):
        if len(args.values) != 1 and not (args.action == "test" and not args.values):
            raise config.ConfigError(f"Aufruf: officemd config {args.action} anthropic|openai")
        name = args.values[0] if args.values else cfg.get("provider")
        if args.action == "set-key":
            if sys.stdin.isatty():
                import getpass

                key = getpass.getpass(f"API-Key für {name}: ")
            else:
                key = sys.stdin.readline()
            where = config.set_key(name, key)
            print(f"Key für {name} gespeichert ({'Schlüsselbund' if where == 'keychain' else 'credentials.json'}).")
            return 0
        if args.action == "delete-key":
            print("Key gelöscht." if config.delete_key(name) else "Kein gespeicherter Key gefunden.")
            return 0
        provider = providers.get_provider(cfg, name)
        schema = {"type": "object", "additionalProperties": False, "required": ["ok"],
                  "properties": {"ok": {"type": "boolean"}}}
        text, meta = provider.generate("Antworte nur im vorgegebenen Schema.", "Setze ok auf true.", schema)
        print(f"Verbindung zu {provider.name} ({meta.get('model')}) funktioniert: {text.strip()}")
        return 0
    raise config.ConfigError(f"Unbekannte Aktion {args.action}")


def cmd_compile(args) -> int:
    cfg = config.load()
    provider = providers.get_provider(cfg)
    path = Path(args.file)
    _log(f"Dokumenttext geht an {provider.name} ({provider.model}).")
    result = compiler.compile_document(
        path, provider, depth=args.depth or cfg.get("depth", "standard"),
        repair_rounds=int(cfg.get("repair_rounds", 2)),
        max_input_chars=int(cfg.get("max_input_chars", 2_000_000)),
        section_chars=int(cfg.get("section_chars", 120_000)), log=_log)
    out = Path(args.output) if args.output else path.with_name(path.stem + ".knowledge.json")
    out.write_text(result.graph_text, encoding="utf-8")
    ev = result.evidence or {}
    print(f"Graph geschrieben: {out}")
    print(f"  {ev.get('counts', {}).get('verified', 0)} von {ev.get('total', 0)} Belegen gefunden, "
          f"{result.rounds} Durchgang/Durchgänge")
    print(f"  Einbetten mit: officemd embed {path} --graph {out} --source-id {result.source_id}")
    return 0


def cmd_sync(args) -> int:
    from . import sync

    cfg = config.load()
    files = ops.office_files([Path(p) for p in args.paths])
    if not files:
        print("Keine DOCX-, XLSX- oder PPTX-Dateien gefunden.", file=sys.stderr)
        return 2
    mode = "raw" if args.raw else None
    results = []
    failed = False
    for f in files:
        if not args.json:
            print(f"{f}", file=sys.stderr, flush=True)
        try:
            results.append(sync.sync_file(f, cfg, mode=mode, dry_run=args.dry_run,
                                          log=_log if not args.json else (lambda _m: None)))
        except (ops.OpError, compiler.CompileError, providers.ProviderError, distiller.ExtractionFailed) as exc:
            failed = True
            entry = {"file": str(f), "action": "error", "message": str(exc)}
            report = getattr(exc, "report", None)
            if report and report.get("problems"):
                entry["problems"] = report["problems"]
            results.append(entry)
    if args.json:
        _print_json(results)
    else:
        labels = ops.STATE_LABELS
        for r in results:
            after = labels.get(r.get("after"), "-") if r.get("after") else "-"
            print(f"{after:<13} {Path(r['file']).name}: {r['message']}")
            for problem in r.get("problems", [])[:10]:
                print(f"    {problem}")
    return 2 if failed else 0


def cmd_setup_ai(args) -> int:
    import shutil
    import subprocess

    root = Path(__file__).resolve().parents[2]
    venv = root / ".venv"
    uv = shutil.which("uv")
    if uv:
        steps = [[uv, "venv", str(venv), "--python", args.python],
                 [uv, "pip", "install", "--python", str(venv / "bin" / "python"), "-e", f"{root}[ai]"]]
    else:
        steps = [[sys.executable, "-m", "venv", str(venv)],
                 [str(venv / "bin" / "python"), "-m", "pip", "install", "-e", f"{root}[ai]"]]
        if sys.version_info < (3, 10):
            print("Ohne uv braucht die Einrichtung Python 3.10 oder neuer. uv installieren: "
                  "https://docs.astral.sh/uv/", file=sys.stderr)
            return 2
    for step in steps:
        print("$ " + " ".join(step))
        if subprocess.run(step).returncode != 0:
            return 2
    print(f"Fertig. ./officemd nutzt ab jetzt {venv / 'bin' / 'python'}.")
    return 0

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

    p = sub.add_parser("config", help="KI-Anbieter, Modelle und API-Keys einstellen")
    p.add_argument("action", choices=["show", "set", "set-key", "delete-key", "test"])
    p.add_argument("values", nargs="*")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("compile", help="Wissensgraph per KI kompilieren (ohne Einbetten)")
    p.add_argument("file")
    p.add_argument("-o", "--output")
    p.add_argument("--depth", choices=["quick", "standard", "deep"])
    p.set_defaults(func=cmd_compile)

    p = sub.add_parser("sync", help="Prüfen und bei Bedarf per KI aktualisieren und einbetten")
    p.add_argument("paths", nargs="+")
    p.add_argument("--raw", action="store_true", help="neue Dateien nur mit Roh-Markdown, ohne KI")
    p.add_argument("--dry-run", action="store_true", help="nur anzeigen, was passieren würde")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("setup-ai", help="Python-Umgebung mit Anthropic- und OpenAI-SDK einrichten")
    p.add_argument("--python", default="3.12")
    p.set_defaults(func=cmd_setup_ai)

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
    except (ops.OpError, distiller.ExtractionFailed, distiller.DistillerError, FileNotFoundError,
            config.ConfigError, providers.ProviderError, compiler.CompileError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
