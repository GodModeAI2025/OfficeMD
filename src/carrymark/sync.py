"""Prüfen und bei Bedarf neu einbetten: ``carrymark sync``.

| Zustand vorher | Aktion |
|---|---|
| Aktuell | nichts |
| Nicht eingebettet, Veraltet | Markdown mit markitdown erzeugen und einbetten |
| Verloren | aus der Sicherung wiederherstellen; passt der Inhalt nicht mehr, neu einbetten |
| Nicht lesbar, in Office geöffnet | überspringen |

Jede Datei wird genau einmal umgewandelt: die Prüfung liefert die Umwandlung, ``embed``
übernimmt sie. Alles läuft lokal.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from . import ops


def sync_file(path: Path, *, dry_run: bool = False) -> Dict[str, Any]:
    path = Path(path)
    found = ops.inspect(path)
    before = found.report
    state = before["state"]
    entry: Dict[str, Any] = {"file": str(path), "before": state, "after": state}

    def done(action: str, message: str, after: str) -> Dict[str, Any]:
        entry.update(action=action, message=message, after=after)
        return entry

    if state == ops.UNREADABLE:
        return done("skip", f"Übersprungen: {before['message']}", state)
    if before.get("locked"):
        return done("skip", "Übersprungen: Office hat die Datei geöffnet.", state)
    if state == ops.CURRENT:
        return done("none", "Aktuell, nichts zu tun.", state)

    restorable = before["sidecar"]["restorable"]
    if dry_run:
        plan = {ops.NEVER: "Markdown einbetten", ops.STALE: "Markdown neu einbetten",
                ops.LOST: "aus der Sicherung wiederherstellen" if restorable else "Markdown neu einbetten"}[state]
        return done("plan", f"Würde {plan}.", state)

    conv = found.conversion
    if state == ops.LOST and restorable:
        restored = ops.restore(path)
        if conv is not None and restored["fingerprint"] == conv.fingerprint:
            return done("restore", "Aus der Sicherung wiederhergestellt.", ops.CURRENT)
        if conv is None:  # Inhalt nicht umwandelbar, aber die Sicherung ist zurück
            return done("restore", "Aus der Sicherung wiederhergestellt.", ops.STALE)
        found = ops.inspect(path)  # Paket hat sich durch restore geändert

    result = ops.embed(path, found.conversion, found.package)
    verb = "eingebettet" if state == ops.NEVER else "neu eingebettet"
    return done("embed" if state == ops.NEVER else "reembed",
                f"Markdown {verb} ({result['characters']} Zeichen, {result['converter']}).", ops.CURRENT)
