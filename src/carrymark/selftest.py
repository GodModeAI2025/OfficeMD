"""Selbsttest: Überlebt der Part das Speichern?

Ohne ``--office`` läuft ein reiner Pakettest mit selbst erzeugten Dateien (einbetten, prüfen,
Part entfernen, Verlust erkennen, wiederherstellen). Mit ``--office`` erzeugen Word, Excel und
PowerPoint per AppleScript je eine echte Datei, Carrymark bettet ein, die App öffnet die Datei,
ändert sie, speichert und schließt. Danach wird geprüft, ob Part und Fingerprint-Eintrag noch
da sind. Das Ergebnis landet als Kompatibilitätsbericht in ``compat/``.

Office-Apps auf dem Mac sind sandboxed. Beim ersten Lauf fragt Word/Excel/PowerPoint unter
Umständen nach Zugriff auf den Testordner (``compat/files``) und macOS nach der Erlaubnis,
die Apps per AppleScript zu steuern. Beides einmal bestätigen.
"""
from __future__ import annotations

import json
import platform
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from . import __version__, converter, fixtures, ooxml, ops

APPS = {
    "word": {"name": "Microsoft Word", "bundle": "com.microsoft.Word", "suffix": ".docx"},
    "excel": {"name": "Microsoft Excel", "bundle": "com.microsoft.Excel", "suffix": ".xlsx"},
    "powerpoint": {"name": "Microsoft PowerPoint", "bundle": "com.microsoft.Powerpoint", "suffix": ".pptx"},
}

# Nach "Speichern unter" ist die alte Objektreferenz ungültig, daher wird über den
# Dateinamen geschlossen und weitergearbeitet.
CREATE = {
    "word": '''
with timeout of 600 seconds
tell application "Microsoft Word"
    set d to make new document
    insert text "Carrymark Selbsttest. Erster Absatz." at end of text object of d
    save as d file name "{hfs}" file format format document
    close document "{name}" saving no
end tell
end timeout''',
    "excel": '''
with timeout of 600 seconds
tell application "Microsoft Excel"
    set wb to make new workbook
    set value of range "A1" of active sheet of wb to "Carrymark Selbsttest"
    set value of range "B1" of active sheet of wb to 42
    save workbook as wb filename "{hfs}" file format Excel XML file format
    close workbook "{name}" saving no
end tell
end timeout''',
    "powerpoint": '''
with timeout of 600 seconds
tell application "Microsoft PowerPoint"
    set p to make new presentation
    set s to make new slide at end of p with properties {{layout:slide layout title only}}
    set content of text range of text frame of shape 1 of s to "Carrymark Selbsttest"
    save p in "{hfs}" as save as Open XML presentation
    close presentation "{name}" saving no
end tell
end timeout''',
}

MODIFY = {
    "word": '''
with timeout of 600 seconds
tell application "Microsoft Word"
    open file name "{hfs}"
    set d to document "{name}"
    insert text " Nachtrag aus Word." at end of text object of d
    save d
    close document "{name}" saving no
end tell
end timeout''',
    "excel": '''
with timeout of 600 seconds
tell application "Microsoft Excel"
    open workbook workbook file name "{hfs}"
    set wb to workbook "{name}"
    set value of range "A2" of active sheet of wb to "Nachtrag aus Excel"
    save wb
    close workbook "{name}" saving no
end tell
end timeout''',
    "powerpoint": '''
with timeout of 600 seconds
tell application "Microsoft PowerPoint"
    open "{hfs}"
    set p to presentation "{name}"
    set s to make new slide at end of p with properties {{layout:slide layout title only}}
    set content of text range of text frame of shape 1 of s to "Nachtrag aus PowerPoint"
    save p
    close presentation "{name}" saving no
end tell
end timeout''',
}


def _osascript(script: str, timeout: int = 660) -> None:
    proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"osascript Exit {proc.returncode}")


def _hfs(path: Path) -> str:
    out = subprocess.run(["osascript", "-e", f'POSIX file "{path}" as string'],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def _app_version(app_name: str) -> str:
    plist = Path("/Applications") / f"{app_name}.app" / "Contents" / "Info.plist"
    try:
        out = subprocess.run(["defaults", "read", str(plist.with_suffix("")), "CFBundleShortVersionString"],
                             capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unbekannt"


# -- Offline -------------------------------------------------------------------------

def offline() -> List[Dict[str, Any]]:
    results = []
    with tempfile.TemporaryDirectory(prefix="cm-selftest-") as tmp:
        tmp_path = Path(tmp).resolve()
        files = {
            "docx": fixtures.make_docx(tmp_path / "test.docx", ["Erster Absatz.", "Zweiter Absatz."]),
            "xlsx": fixtures.make_xlsx(tmp_path / "test.xlsx", {"Daten": [["Monat", "Wert"], ["Mai", "12"]]}),
            "pptx": fixtures.make_pptx(tmp_path / "test.pptx", [["Titel", "Punkt"]], notes={1: "Notiz"}),
            "pdf": fixtures.make_pdf(tmp_path / "test.pdf", [["Erste Seite"], ["Zweite Seite"]]),
        }
        for kind, path in files.items():
            steps = {}
            try:
                ops.embed(path)
                steps["embed"] = ops.check(path)["state"]
                ops.strip(path, keep_props=True)
                steps["strip"] = ops.check(path)["state"]
                ops.restore(path)
                steps["restore"] = ops.check(path)["state"]
                ok = steps == {"embed": ops.CURRENT, "strip": ops.LOST, "restore": ops.CURRENT}
                results.append({"kind": kind, "ok": ok, "steps": steps})
            except Exception as exc:  # Selbsttest soll alle Formate durchlaufen
                results.append({"kind": kind, "ok": False, "steps": steps, "error": str(exc)})
    return results


# -- Office --------------------------------------------------------------------------

def office_roundtrip(app: str, workdir: Path, keep: bool = False) -> Dict[str, Any]:
    spec = APPS[app]
    result: Dict[str, Any] = {"app": spec["name"], "version": _app_version(spec["name"])}
    if not (Path("/Applications") / f"{spec['name']}.app").exists():
        result.update(ok=False, error="App nicht installiert")
        return result
    workdir.mkdir(parents=True, exist_ok=True)
    path = workdir / f"carrymark-roundtrip{spec['suffix']}"
    for leftover in (path, ops.sidecar_dir(path)):
        if leftover.is_dir():
            shutil.rmtree(leftover)
        elif leftover.exists():
            leftover.unlink()
    try:
        hfs = _hfs(path)
        _osascript(CREATE[app].format(hfs=hfs, name=path.name))
        result["created"] = path.exists()
        embedded = ops.embed(path)
        result["fingerprint_before"] = embedded["fingerprint"]
        _osascript(MODIFY[app].format(hfs=hfs, name=path.name))
        pkg = ooxml.Package(path)
        part = pkg.find_knowledge()
        props = pkg.read_props()
        report = ops.check(path)
        result.update(
            state_after_save=report["state"],
            part_survived=part is not None,
            part_registered=bool(part and part.registered),
            payload_intact=bool(part and part.payload.markdown),
            props_survived=bool(props.get("Fingerprint")),
            guid_kept=bool(part and part.guid == embedded["guid"]),
            saved_by=report.get("saved_by"),
            warnings=report.get("warnings", []),
        )
        result["markdown_after_save"] = "vollständig" if result["payload_intact"] else "fehlt"
        result["ok"] = result["part_survived"] and result["props_survived"]
    except Exception as exc:
        result.update(ok=False, error=str(exc))
    finally:
        if not keep:
            shutil.rmtree(ops.sidecar_dir(path), ignore_errors=True)
            if path.exists():
                path.unlink()
    return result


def _write_report(outdir: Path, report: Dict[str, Any]) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    json_path = outdir / f"office-roundtrip-{stamp}.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [f"# Office-Roundtrip {stamp}", "",
             f"carrymark {__version__}, macOS {report['macos']}", "",
             "Ablauf je App: Datei in der App anlegen, Markdown mit Carrymark einbetten, Datei in "
             "der App öffnen, Text ergänzen, speichern, schließen. Weil der Test Text hinzufügt, ist "
             "„Veraltet“ das erwartete Ergebnis. Entscheidend sind die Spalten zu Part, Eintrag und GUID.", "",
             "| App | Version | Part überlebt | Registrierung | Fingerprint-Eintrag | GUID gleich | Zustand danach | Markdown |",
             "|---|---|---|---|---|---|---|---|"]
    yes = {True: "ja", False: "nein", None: "-"}
    for r in report["office"]:
        if "error" in r:
            lines.append(f"| {r['app']} | {r['version']} | Fehler: {r['error']} | | | | |")
            continue
        lines.append(f"| {r['app']} | {r['version']} | {yes[r['part_survived']]} | {yes[r['part_registered']]} | "
                     f"{yes[r['props_survived']]} | {yes[r['guid_kept']]} | {ops.STATE_LABELS[r['state_after_save']]} | "
                     f"{r.get('markdown_after_save', '-')} |")
    (outdir / f"office-roundtrip-{stamp}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path


def main(office: bool, apps: List[str], outdir: Path, keep: bool = False) -> int:
    ok = True
    print("Offline-Pakettest (einbetten, entfernen, Verlust erkennen, wiederherstellen):")
    for r in offline():
        print(f"  {r['kind']}: {'ok' if r['ok'] else 'FEHLER'} {r['steps']}" + (f" {r['error']}" if r.get("error") else ""))
        ok &= r["ok"]
    if not office:
        return 0 if ok else 1
    if platform.system() != "Darwin":
        print("Office-Roundtrip läuft nur auf macOS.")
        return 2
    print("Office-Roundtrip (die Apps öffnen sich kurz):")
    results = []
    for app in apps:
        r = office_roundtrip(app, (outdir / "files").resolve(), keep=keep)
        results.append(r)
        if "error" in r:
            print(f"  {r['app']} {r['version']}: FEHLER {r['error']}")
        else:
            print(f"  {r['app']} {r['version']}: Part {'überlebt' if r['part_survived'] else 'VERLOREN'}, "
                  f"Fingerprint-Eintrag {'da' if r['props_survived'] else 'weg'}, "
                  f"Zustand {ops.STATE_LABELS[r['state_after_save']]}, Markdown {r.get('markdown_after_save', '-')}")
        ok &= bool(r.get("ok"))
    path = _write_report(outdir, {"tool_version": __version__, "macos": platform.mac_ver()[0],
                                  "date": converter.now(),
                                  "office": results})
    print(f"Bericht: {path}")
    return 0 if ok else 1
