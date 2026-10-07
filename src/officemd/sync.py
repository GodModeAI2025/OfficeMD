"""Prüfen und bei Bedarf aktualisieren: ``officemd sync``.

Pro Datei:

| Zustand vorher | Aktion |
|---|---|
| Aktuell | nichts |
| Nie vorhanden | Graph-Modus: KI-Kompilierung und Einbetten. Roh-Modus: Roh-Markdown einbetten. |
| Veraltet, Roh-Markdown | Roh-Markdown neu einbetten (ohne KI) |
| Veraltet, Graph | aktuellen Text neu kompilieren, alten Graphen archivieren, menschliche Ergänzungen mit noch auffindbarem Zitat übernehmen, einbetten |
| Verloren | aus dem Sidecar wiederherstellen, danach wie oben weiter; ohne Sidecar neu kompilieren |
| Nicht lesbar, gesperrt | überspringen, mit Grund |

Ein veralteter Graph wird bewusst neu kompiliert statt per Delta gemergt. Der Merge des
Distillers lehnt gleiche IDs mit geänderter Nutzlast ab, und ein Graph mit Belegen, die nicht
mehr im Text stehen, würde nie wieder "Aktuell". Die alte Fassung bleibt in ``versions/``.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from . import compiler, ooxml, ops, providers

Log = Callable[[str], None]


def _old_graph(path: Path) -> Optional[Dict[str, Any]]:
    try:
        part = ooxml.Package(path).find_knowledge()
    except ooxml.OoxmlError:
        part = None
    if part and part.payload.graph_json:
        return json.loads(part.payload.graph_json)
    side = ops.sidecar_dir(path) / "knowledge.json"
    if side.is_file():
        return json.loads(side.read_text(encoding="utf-8"))
    return None


def _compile_and_embed(path: Path, cfg: Dict[str, Any], provider_factory, log: Log,
                       old_graph: Optional[Dict[str, Any]], source_id: Optional[str]) -> Dict[str, Any]:
    provider = provider_factory(cfg)
    log(f"Dokumenttext geht an {provider.name} ({provider.model}).")
    result = compiler.compile_document(
        path, provider, depth=cfg.get("depth", "standard"),
        repair_rounds=int(cfg.get("repair_rounds", 2)),
        max_input_chars=int(cfg.get("max_input_chars", 400_000)),
        old_graph=old_graph, source_id=source_id, log=log)
    info = {"provider": result.provider, "model": result.model, "rounds": result.rounds,
            "carried_human_additions": result.carried, "compiled_at": ops.now()}
    with tempfile.TemporaryDirectory(prefix="omd-sync-") as tmp:
        graph_path = Path(tmp) / "graph.knowledge.json"
        graph_path.write_text(result.graph_text, encoding="utf-8")
        embedded = ops.embed(path, graph_path, source_id=result.source_id, replace=True, compile_info=info)
    return {"compile": info, "evidence": embedded.get("evidence")}


def sync_file(path: Path, cfg: Dict[str, Any], *, mode: Optional[str] = None, dry_run: bool = False,
              provider_factory=None, log: Log = lambda _m: None) -> Dict[str, Any]:
    path = Path(path)
    provider_factory = provider_factory or providers.get_provider
    mode = mode or cfg.get("mode", "graph")
    before = ops.check(path)
    entry: Dict[str, Any] = {"file": str(path), "before": before["state"], "action": None,
                             "after": before["state"], "message": before["message"]}

    def done(action: str, message: str, after: Optional[str] = None, **extra: Any) -> Dict[str, Any]:
        entry.update(action=action, message=message, **extra)
        entry["after"] = after or ops.check(path, with_evidence=False)["state"]
        return entry

    state = before["state"]
    if state == ops.UNREADABLE:
        return done("skip", f"Übersprungen: {before['message']}", state)
    if before.get("locked"):
        return done("skip", "Übersprungen: Office hat die Datei geöffnet.", state)
    if state == ops.CURRENT:
        return done("none", "Aktuell, nichts zu tun.", state)

    plan = {
        ops.NEVER: "einbetten (Roh-Markdown)" if mode == "raw" else "kompilieren und einbetten",
        ops.STALE: "Roh-Markdown neu einbetten" if before.get("mode") == "raw" else "neu kompilieren und einbetten",
        ops.LOST: "aus dem Sidecar wiederherstellen" if before["sidecar"]["restorable"] else "neu kompilieren und einbetten",
    }[state]
    if dry_run:
        return done("plan", f"Würde {plan}.", state)

    if state == ops.LOST:
        if before["sidecar"]["restorable"]:
            ops.restore(path)
            log("Aus dem Sidecar wiederhergestellt.")
            after_restore = ops.check(path)
            if after_restore["state"] == ops.CURRENT:
                return done("restore", "Aus dem Sidecar wiederhergestellt, wieder aktuell.", ops.CURRENT)
            # Veraltet nach dem Wiederherstellen: weiter wie bei einer veralteten Datei.
            before = after_restore
            state = after_restore["state"]
            entry["restored"] = True
        else:
            state = ops.NEVER

    if state == ops.STALE and before.get("mode") == "raw":
        ops.embed(path)
        return done("embed-raw", "Roh-Markdown neu eingebettet.")
    if state == ops.NEVER and mode == "raw":
        ops.embed(path)
        return done("embed-raw", "Roh-Markdown eingebettet.")

    old = _old_graph(path) if state == ops.STALE else None
    source_id = (before.get("part") or {}).get("source_id") if old else None
    outcome = _compile_and_embed(path, cfg, provider_factory, log, old, source_id)
    ev = outcome.get("evidence") or {}
    counts = ev.get("counts", {})
    message = (f"Mit {outcome['compile']['provider']}/{outcome['compile']['model']} kompiliert "
               f"({outcome['compile']['rounds']} Durchgang/Durchgänge), "
               f"{counts.get('verified', 0)} von {ev.get('total', 0)} Belegen gefunden.")
    if outcome["compile"]["carried_human_additions"]:
        message += f" {len(outcome['compile']['carried_human_additions'])} menschliche Ergänzung(en) übernommen."
    action = "recompile" if state == ops.STALE else "compile"
    return done(action, message, compile=outcome["compile"])
