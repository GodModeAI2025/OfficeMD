from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any, Dict, List

import pytest

from officemd import cli, compiler, config, ooxml, ops, providers, sync
from officemd.fixtures import make_docx

PARAS = ["Die Anlage läuft seit Mai.", "Wartung erfolgt quartalsweise.", "Team Nord ist verantwortlich."]


def compact(quotes: Dict[str, str], title: str = "Anlagenbericht") -> Dict[str, Any]:
    """Kompakte Modellausgabe: je Zitat ein Begriff, ein Claim und ein Beleg."""
    keys = list(quotes)
    return {
        "title": title,
        "domain": "Betrieb",
        "language": "de",
        "evidence": [{"id": f"evidence-{k}", "quote": q, "support": "supports"} for k, q in quotes.items()],
        "clusters": [{"id": "betrieb", "label": "Betrieb", "description": "Betrieb der Anlage"}],
        "nodes": [{
            "id": k, "label": k.title(), "cluster": "betrieb", "confidence": "high",
            "definition": f"{k.title()} im Anlagenbetrieb.", "relevance": "Für den Betrieb wichtig.",
            "statements": [q], "evidence": [f"evidence-{k}"], "source_date": None,
            "valid_from": None, "valid_until": None, "temporal_confidence": "unknown",
        } for k, q in quotes.items()],
        "claims": [{"id": f"claim-{k}", "node": k, "statement": q, "confidence": "high",
                    "origin": "source_stated", "evidence": [f"evidence-{k}"]} for k, q in quotes.items()],
        "edges": ([{"source": keys[0], "target": keys[1], "type": "uses", "weight": 0.7,
                    "confidence": "medium", "evidence": [f"evidence-{keys[0]}"], "explanation": "laut Text"}]
                  if len(keys) > 1 else []),
        "open_questions": [],
    }


class FakeProvider(providers.Provider):
    name = "fake"
    model = "fake-1"

    def __init__(self, outputs: List[Any]) -> None:
        self.outputs = list(outputs)
        self.calls: List[Dict[str, Any]] = []

    def generate(self, system, user, schema):
        self.calls.append({"system": system, "user": user, "schema": schema})
        out = self.outputs.pop(0)
        return (out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)), {"model": self.model}


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    return tmp_path.resolve()


@pytest.fixture
def cfg(tmp_path: Path, monkeypatch) -> Dict[str, Any]:
    monkeypatch.setenv("OFFICEMD_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("OFFICEMD_KEYCHAIN", "0")
    for names in config.ENV_KEYS.values():
        for name in names:
            monkeypatch.delenv(name, raising=False)
    return config.load()


@pytest.fixture
def docx(workdir: Path) -> Path:
    return make_docx(workdir / "Bericht.docx", PARAS)


def factory(fake: FakeProvider):
    return lambda _cfg: fake


ALL = {"anlage": PARAS[0], "wartung": PARAS[1], "team": PARAS[2]}


# -- Schema und Prompt -------------------------------------------------------------

def _walk(schema: Dict[str, Any]):
    yield schema
    for value in schema.get("properties", {}).values():
        yield from _walk(value)
    if "items" in schema:
        yield from _walk(schema["items"])


def test_output_schema_is_strict_mode_compatible() -> None:
    for node in _walk(compiler.OUTPUT_SCHEMA):
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert sorted(node["required"]) == sorted(node["properties"])
        for banned in ("minimum", "maximum", "format", "pattern", "minItems"):
            assert banned not in node


def test_system_prompt_uses_distiller_rules() -> None:
    prompt = compiler.system_prompt()
    assert "Evidence zuerst" in prompt and "Keine Ortsrolle" in prompt
    assert '"evidence_policy"' in prompt
    assert "### 2.7" not in prompt


def test_assemble_builds_valid_graph(docx: Path, workdir: Path) -> None:
    from officemd import distiller

    normalized = distiller.extract(docx)
    graph = compiler.assemble(compact(ALL), normalized=normalized, file_name=docx.name,
                              depth="standard", source_id="source-bericht")
    result = compiler.check_graph(graph, normalized, "source-bericht", workdir)
    assert result["ok"], result["problems"]
    assert result["evidence"]["counts"]["verified"] == 3
    assert graph["metadata"]["sources"][0]["content_sha256"] == normalized["source"]["content_sha256"]
    assert all(e["attribution_basis"] == "source_explicit" for e in graph["evidence"])


def test_assemble_normalizes_ids_and_dates(docx: Path) -> None:
    from officemd import distiller

    data = compact({"Anlage Größe": PARAS[0]})
    data["nodes"][0]["source_date"] = "irgendwann"
    data["nodes"][0]["valid_from"] = "2026-05"
    graph = compiler.assemble(data, normalized=distiller.extract(docx), file_name=docx.name,
                              depth="quick", source_id="source-bericht")
    node = graph["nodes"][0]
    assert node["id"] == "anlage-groesse"
    assert graph["claims"][0]["node"] == "anlage-groesse"
    assert node["temporal"]["source_date"] is None and node["temporal"]["valid_from"] == "2026-05"


# -- Kompilieren und Korrekturschleife ----------------------------------------------------

def test_repair_round_fixes_bad_quote(docx: Path) -> None:
    bad = compact({"anlage": "Die Anlage läuft seit Juni."})
    fake = FakeProvider([bad, compact(ALL)])
    result = compiler.compile_document(docx, fake)
    assert result.rounds == 2
    assert "nicht im Dokument gefunden" in fake.calls[1]["user"]
    assert "Die Anlage läuft seit Juni." in fake.calls[1]["user"]
    assert result.evidence["counts"]["verified"] == 3


def test_invalid_json_triggers_repair(docx: Path) -> None:
    fake = FakeProvider(["kein json", compact(ALL)])
    assert compiler.compile_document(docx, fake).rounds == 2


def test_gives_up_after_two_repairs_without_embedding(docx: Path, cfg) -> None:
    bad = compact({"anlage": "Steht nicht im Text."})
    fake = FakeProvider([bad, bad, bad])
    with pytest.raises(compiler.CompileError) as info:
        sync.sync_file(docx, cfg, provider_factory=factory(fake))
    assert len(fake.calls) == 3
    assert info.value.report["problems"]
    assert ooxml.Package(docx).find_knowledge() is None


def test_refuses_oversized_input_without_truncating(docx: Path) -> None:
    with pytest.raises(compiler.CompileError, match="nicht gekürzt"):
        compiler.compile_document(docx, FakeProvider([]), max_input_chars=10)


# -- sync ------------------------------------------------------------------------------

def test_sync_never_compiles_and_embeds(docx: Path, cfg) -> None:
    fake = FakeProvider([compact(ALL)])
    result = sync.sync_file(docx, cfg, provider_factory=factory(fake))
    assert (result["before"], result["action"], result["after"]) == ("never", "compile", "current")
    assert "3 von 3 Belegen" in result["message"]
    meta = json.loads((ops.sidecar_dir(docx) / "embed.json").read_text())
    assert meta["compiled_by"]["provider"] == "fake" and meta["source_id"] == "source-bericht"


def test_sync_current_does_nothing(docx: Path, cfg) -> None:
    sync.sync_file(docx, cfg, provider_factory=factory(FakeProvider([compact(ALL)])))
    fake = FakeProvider([])
    result = sync.sync_file(docx, cfg, provider_factory=factory(fake))
    assert result["action"] == "none" and not fake.calls


def test_sync_stale_recompiles_archives_and_keeps_human_additions(docx: Path, cfg) -> None:
    sync.sync_file(docx, cfg, provider_factory=factory(FakeProvider([compact(ALL)])))
    # Eine menschliche Ergänzung in den eingebetteten Graphen schreiben.
    pkg = ooxml.Package(docx)
    part = pkg.find_knowledge()
    graph = json.loads(part.payload.graph_json)
    graph["evidence"].append({
        "id": "evidence-human-note", "source": "source-bericht",
        "selector": {"type": "TextQuoteSelector", "exact": "Team Nord"},
        "support": "contextualizes", "attribution_basis": "human_added", "review_status": "reviewed"})
    part.payload.graph_json = json.dumps(graph, ensure_ascii=False)
    pkg.embed(part.payload)
    pkg.save()
    # Text ändern: "quartalsweise" -> "monatlich".
    pkg = ooxml.Package(docx)
    pkg.write("word/document.xml", pkg.read("word/document.xml").replace("quartalsweise".encode(), "monatlich".encode()))
    pkg.save()
    assert ops.check(docx)["state"] == "stale"

    new = dict(ALL, wartung="Wartung erfolgt monatlich.")
    fake = FakeProvider([compact(new)])
    result = sync.sync_file(docx, cfg, provider_factory=factory(fake))
    assert (result["action"], result["after"]) == ("recompile", "current")
    assert result["compile"]["carried_human_additions"] == ["evidence-human-note"]
    assert list((ops.sidecar_dir(docx) / "versions").glob("*.replaced.json"))
    embedded = json.loads(ooxml.Package(docx).find_knowledge().payload.graph_json)
    assert any(e["id"] == "evidence-human-note" for e in embedded["evidence"])
    assert ops.check(docx)["evidence"]["counts"]["not_found"] == 0


def test_sync_lost_restores_without_model(docx: Path, cfg) -> None:
    sync.sync_file(docx, cfg, provider_factory=factory(FakeProvider([compact(ALL)])))
    ops.strip(docx, keep_props=True)
    fake = FakeProvider([])
    result = sync.sync_file(docx, cfg, provider_factory=factory(fake))
    assert (result["before"], result["action"], result["after"]) == ("lost", "restore", "current")
    assert not fake.calls


def test_sync_raw_mode_needs_no_provider(docx: Path, cfg) -> None:
    result = sync.sync_file(docx, cfg, mode="raw", provider_factory=None)
    assert (result["action"], result["after"]) == ("embed-raw", "current")


def test_sync_dry_run_changes_nothing(docx: Path, cfg) -> None:
    result = sync.sync_file(docx, cfg, dry_run=True, provider_factory=factory(FakeProvider([])))
    assert result["action"] == "plan" and "kompilieren" in result["message"]
    assert ooxml.Package(docx).find_knowledge() is None


def test_sync_skips_unreadable_and_locked(workdir: Path, docx: Path, cfg) -> None:
    junk = workdir / "kaputt.docx"
    junk.write_bytes(b"x")
    assert sync.sync_file(junk, cfg)["action"] == "skip"
    (workdir / ("~$" + docx.name[2:])).write_bytes(b"")
    assert sync.sync_file(docx, cfg, provider_factory=factory(FakeProvider([])))["action"] == "skip"


def test_sync_without_provider_explains_setup(docx: Path, cfg) -> None:
    with pytest.raises(providers.ProviderError, match="officemd config set provider"):
        sync.sync_file(docx, cfg)


# -- Konfiguration und Keys ---------------------------------------------------------------

def test_config_set_validates(cfg) -> None:
    config.set_value(cfg, "provider", "anthropic")
    config.set_value(cfg, "anthropic.model", "claude-sonnet-5-5")
    config.set_value(cfg, "anthropic.fallbacks", "false")
    config.save(cfg)
    loaded = config.load()
    assert loaded["provider"] == "anthropic" and loaded["anthropic"]["model"] == "claude-sonnet-5-5"
    assert loaded["anthropic"]["fallbacks"] is False and loaded["openai"]["model"] == "gpt-6-astra"
    with pytest.raises(config.ConfigError):
        config.set_value(cfg, "provider", "gemini")
    with pytest.raises(config.ConfigError):
        config.set_value(cfg, "openai.effort", "extrem")
    with pytest.raises(config.ConfigError):
        config.set_value(cfg, "geheim", "x")


def test_key_file_store_is_private_and_never_shown(cfg, capsys) -> None:
    assert config.set_key("openai", "sk-test-123") == "file"
    path = config.config_dir() / "credentials.json"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert config.get_key("openai") == ("sk-test-123", "file")
    cli.main(["config", "show", "--json"])
    out = capsys.readouterr().out
    assert "sk-test-123" not in out
    assert json.loads(out)["providers"]["openai"]["key_source"] == "file"
    assert config.delete_key("openai") and config.get_key("openai") == (None, "none")


def test_env_key_wins(cfg, monkeypatch) -> None:
    config.set_key("anthropic", "aus-datei")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "aus-env")
    assert config.get_key("anthropic") == ("aus-env", "env")


def test_rejects_suspicious_keys(cfg) -> None:
    for bad in ("", "mit leerzeichen", 'mit"quote'):
        with pytest.raises(config.ConfigError):
            config.set_key("openai", bad)


# -- Anfragen der echten Anbieter (ohne Netz) ------------------------------------------------

def test_anthropic_request_shape() -> None:
    pytest.importorskip("anthropic")
    p = providers.AnthropicProvider("sk-ant-test", "claude-opus-5-5", "high", fallbacks=True)
    kwargs = p.request_kwargs("SYS", "USER", compiler.OUTPUT_SCHEMA)
    assert kwargs["model"] == "claude-opus-5-5" and kwargs["max_tokens"] == 64000
    assert kwargs["output_config"] == {"effort": "high", "format": {"type": "json_schema", "schema": compiler.OUTPUT_SCHEMA}}
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kwargs["fallbacks"] == "default" and kwargs["betas"] == ["server-side-fallback-2026-07-01"]
    assert "thinking" not in kwargs
    plain = providers.AnthropicProvider("sk-ant-test", "claude-opus-5-5", fallbacks=False).request_kwargs("S", "U", {})
    assert "betas" not in plain and "fallbacks" not in plain


def test_openai_request_shape() -> None:
    pytest.importorskip("openai")
    p = providers.OpenAIProvider("sk-test", "gpt-6-astra", "high")
    kwargs = p.request_kwargs("SYS", "USER", compiler.OUTPUT_SCHEMA)
    fmt = kwargs["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True and fmt["schema"] is compiler.OUTPUT_SCHEMA
    assert kwargs["instructions"] == "SYS" and kwargs["input"] == "USER" and kwargs["model"] == "gpt-6-astra"


def test_cli_set_key_reads_stdin_like_the_app(cfg, monkeypatch, capsys) -> None:
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("sk-aus-stdin\n"))
    assert cli.main(["config", "set-key", "openai"]) == 0
    assert "sk-aus-stdin" not in capsys.readouterr().out
    assert config.get_key("openai") == ("sk-aus-stdin", "file")
    assert cli.main(["config", "set", "provider", "openai"]) == 0
    assert cli.main(["config", "set", "provider", "none"]) == 0
    assert config.load()["provider"] is None


# -- Fakten, Chunks, Abschnitte -------------------------------------------------------------

def with_fact(data: Dict[str, Any], quote: str, value: str) -> Dict[str, Any]:
    data = json.loads(json.dumps(data))
    data["evidence"].append({"id": "evidence-wert", "quote": quote, "support": "supports"})
    data["facts"] = [{"id": "fact-wartung", "statement": "Wartungsintervall", "value": value,
                      "metric": "Intervall", "concept": "wartung", "context": None, "confidence": "high",
                      "evidence": ["evidence-wert"], "valid_from": "2026-05", "valid_until": None,
                      "temporal_confidence": "explicit"}]
    return data


def test_facts_are_assembled_and_verified(docx: Path) -> None:
    fake = FakeProvider([with_fact(compact(ALL), "quartalsweise", "quartalsweise")])
    result = compiler.compile_document(docx, fake)
    graph = json.loads(result.graph_text)
    fact = graph["facts"][0]
    assert fact["id"] == "fact-wartung" and fact["concept"] == "wartung" and fact["source"] == "source-bericht"
    assert fact["value"] == "quartalsweise" and fact["temporal"]["valid_from"] == "2026-05"
    assert result.evidence["counts"] == {"verified": 4, "not_found": 0, "unverifiable": 0}


def test_fact_with_invented_value_is_repaired(docx: Path) -> None:
    bad = with_fact(compact(ALL), "monatlich", "monatlich")
    good = with_fact(compact(ALL), "quartalsweise", "quartalsweise")
    fake = FakeProvider([bad, good])
    assert compiler.compile_document(docx, fake).rounds == 2


def test_chunks_are_clean_evidence_backed_and_bounded() -> None:
    graph = {"nodes": [
        {"id": "a", "label": "Alpha", "definition": "Erster -> zweiter **Schritt**", "evidence": ["e1"],
         "statements": ["Satz " + "x" * 3000, "Noch ein Satz " + "y" * 3000], "temporal": {"valid_from": "2026"}},
        {"id": "b", "label": "Beta", "definition": "ohne Beleg", "evidence": [], "statements": ["s"]},
    ]}
    chunks = compiler.build_chunks(graph)
    assert [c["id"] for c in chunks] == ["chunk-a-1", "chunk-a-2"]
    for chunk in chunks:
        assert len(chunk["text"]) <= compiler.MAX_CHUNK_CHARS
        assert "->" not in chunk["text"] and "**" not in chunk["text"]
        assert chunk["kind"] == "source_claims" and chunk["include_in_default_retrieval"] is True
        assert chunk["evidence"] == ["e1"] and chunk["temporal_scope"] == "2026"


def test_compiled_graph_carries_chunks(docx: Path) -> None:
    result = compiler.compile_document(docx, FakeProvider([compact(ALL)]))
    graph = json.loads(result.graph_text)
    assert {c["id"] for c in graph["chunks"]} == {"chunk-anlage", "chunk-wartung", "chunk-team"}


def test_large_document_is_compiled_in_sections(workdir: Path) -> None:
    paras = [f"Absatz {i}: Die Pumpe {i} läuft mit Stufe {i}." for i in range(1, 9)]
    path = make_docx(workdir / "Gross.docx", paras)
    first = compact({"pumpe": paras[0], "stufe": paras[1]})
    # Abschnitt 2 greift den Begriff "pumpe" wieder auf und ergänzt einen neuen.
    second = compact({"pumpe": paras[5], "lager": paras[6]})
    fake = FakeProvider([first, second])
    total = sum(len(p) for p in paras)
    result = compiler.compile_document(path, fake, section_chars=total // 2 + 5)
    assert result.sections == 2 and len(fake.calls) == 2
    assert "Abschnitt 2 von 2" in fake.calls[1]["user"]
    assert "- pumpe: Pumpe" in fake.calls[1]["user"]
    assert paras[0] in fake.calls[0]["user"] and paras[0] not in fake.calls[1]["user"]
    graph = json.loads(result.graph_text)
    assert {n["id"] for n in graph["nodes"]} == {"pumpe", "stufe", "lager"}
    pumpe = next(n for n in graph["nodes"] if n["id"] == "pumpe")
    assert paras[0] in pumpe["statements"] and paras[5] in pumpe["statements"]
    assert {e["id"] for e in graph["evidence"]} >= {"evidence-s1-pumpe", "evidence-s2-pumpe"}
    assert result.evidence["counts"]["not_found"] == 0


def test_split_sections_keeps_segment_boundaries() -> None:
    segments = [{"text": "a" * 40}, {"text": "b" * 40}, {"text": "c" * 40}]
    assert [len(s) for s in compiler.split_sections(segments, 85)] == [2, 1]
    assert [len(s) for s in compiler.split_sections(segments, 10)] == [1, 1, 1]
