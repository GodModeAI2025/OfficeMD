"""Prüfen und bei Bedarf neu einbetten: ``carrymark sync``.

| Zustand vorher | Aktion |
|---|---|
| Aktuell | nichts |
| Nie vorhanden, Veraltet | Markdown mit markitdown erzeugen und einbetten |
| Verloren | aus dem Sidecar wiederherstellen; passt der Inhalt nicht mehr, neu einbetten |
| Nicht lesbar, in Office geöffnet | überspringen |

Alles läuft lokal, ohne Netz.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Optional

from . import ops

Log = Callable[[str], None]


def sync_file(path: Path, *, dry_run: bool = False, log: Log = lambda _m: None) -> Dict[str, Any]:
    path = Path(path)
    before = ops.check(path)
    entry: Dict[str, Any] = {"file": str(path), "before": before["state"], "action": None,
                             "after": before["state"], "message": before["message"]}

    def done(action: str, message: str, after: Optional[str] = None) -> Dict[str, Any]:
        entry.update(action=action, message=message)
        entry["after"] = after or ops.check(path)["state"]
        return entry

    state = before["state"]
    if state == ops.UNREADABLE:
        return done("skip", f"Übersprungen: {before['message']}", state)
    if before.get("locked"):
        return done("skip", "Übersprungen: Office hat die Datei geöffnet.", state)
    if state == ops.CURRENT:
        return done("none", "Aktuell, nichts zu tun.", state)

    restorable = before["sidecar"]["restorable"]
    plan = {
        ops.NEVER: "Markdown einbetten",
        ops.STALE: "Markdown neu einbetten",
        ops.LOST: "aus dem Sidecar wiederherstellen" if restorable else "Markdown neu einbetten",
    }[state]
    if dry_run:
        return done("plan", f"Würde {plan}.", state)

    if state == ops.LOST and restorable:
        ops.restore(path)
        log("Aus dem Sidecar wiederhergestellt.")
        if ops.check(path)["state"] == ops.CURRENT:
            return done("restore", "Aus dem Sidecar wiederhergestellt.", ops.CURRENT)
        log("Inhalt hat sich inzwischen geändert, bette neu ein.")

    result = ops.embed(path)
    action = "embed" if state == ops.NEVER else "reembed"
    verb = "eingebettet" if state == ops.NEVER else "neu eingebettet"
    return done(action, f"Markdown {verb} ({result['characters']} Zeichen, {result['converter']}).")
