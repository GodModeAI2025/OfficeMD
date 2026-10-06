from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from officemd import cli, distiller, ooxml, ops
from officemd.fingerprint import text_fingerprint
from officemd.fixtures import make_docx, make_graph, make_pptx, make_xlsx

QUOTES = {"anlage": "Die Anlage läuft seit Mai.", "wartung": "Wartung erfolgt quartalsweise."}


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    # Der Extraktor lehnt Symlinks im Pfad ab; /var -> /private/var auf macOS.
    return tmp_path.resolve()


@pytest.fixture
def docx(workdir: Path) -> Path:
    return make_docx(workdir / "Bericht.docx", list(QUOTES.values()))


def graph_for(path: Path, quotes=QUOTES, name="g.knowledge.json") -> Path:
    normalized = distiller.extract(path)
    return make_graph(path.parent / name, file_name=path.name, file_type=path.suffix.lstrip("."),
                      content_sha256=normalized["source"]["content_sha256"], quotes=quotes)


def replace_text(path: Path, old: str, new: str) -> None:
    pkg = ooxml.Package(path)
    part = pkg.main_part()
    pkg.write(part, pkg.read(part).replace(old.encode(), new.encode()))
    pkg.save()


# -- Paketstruktur -------------------------------------------------------------------

def test_embed_registers_part_like_office(docx: Path) -> None:
    ops.embed(docx)
    with zipfile.ZipFile(docx) as z:
        names = z.namelist()
        assert names[0] == "[Content_Types].xml"
        assert {"customXml/item1.xml", "customXml/itemProps1.xml",
                "customXml/_rels/item1.xml.rels", "docProps/custom.xml"} <= set(names)
        content_types = z.read("[Content_Types].xml").decode()
        assert 'PartName="/customXml/itemProps1.xml"' in content_types
        assert 'PartName="/docProps/custom.xml"' in content_types
        assert "relationships/customXml" in z.read("word/_rels/document.xml.rels").decode()
        assert "custom-properties" in z.read("_rels/.rels").decode()
        props = z.read("customXml/itemProps1.xml").decode()
        assert "ds:itemID=\"{" in props and ooxml.NS_OMD in props
    part = ooxml.Package(docx).find_knowledge()
    assert part.registered and part.guid
    assert ooxml.Package(docx).read_props()["PartGuid"] == part.guid


def test_document_parts_stay_byte_identical(docx: Path) -> None:
    with zipfile.ZipFile(docx) as z:
        before = z.read("word/document.xml")
    ops.embed(docx)
    with zipfile.ZipFile(docx) as z:
        assert z.read("word/document.xml") == before


def test_own_part_does_not_change_fingerprint(docx: Path) -> None:
    before = text_fingerprint(distiller.extract(docx))
    ops.embed(docx, graph_for(docx))
    after = distiller.extract(docx)
    assert text_fingerprint(after) == before
    assert after["source"]["content_sha256"] != json.loads(
        (ops.sidecar_dir(docx) / "knowledge.json").read_text())["metadata"]["sources"][0]["content_sha256"]


def test_reembed_keeps_guid_and_single_part(docx: Path) -> None:
    ops.embed(docx)
    guid = ooxml.Package(docx).find_knowledge().guid
    ops.embed(docx)
    pkg = ooxml.Package(docx)
    assert pkg.find_knowledge().guid == guid
    assert sum(1 for n in pkg.names() if n.startswith("customXml/item") and n.endswith(".xml")
               and "Props" not in n) == 1


def test_existing_custom_props_are_preserved(workdir: Path) -> None:
    path = make_docx(workdir / "p.docx", ["Text."])
    pkg = ooxml.Package(path)
    pkg.write_props({"Tmp": "x"})  # legt custom.xml samt Relationship an
    pkg.write("docProps/custom.xml", pkg.read("docProps/custom.xml").replace(b"OfficeMDTmp", b"Kunde"))
    pkg.save()
    ops.embed(path)
    ops.strip(path)
    custom = ooxml.Package(path).read("docProps/custom.xml").decode()
    assert 'name="Kunde"' in custom and "OfficeMD" not in custom


def test_cdata_terminator_in_payload_roundtrips(docx: Path) -> None:
    pkg = ooxml.Package(docx)
    pkg.embed(ooxml.Payload(mode="raw", fingerprint="f", embedded_at="t", markdown="a ]]> b"))
    pkg.save()
    assert ooxml.Package(docx).find_knowledge().payload.markdown == "a ]]> b"


# -- Zustände ------------------------------------------------------------------------

def test_states_never_current_lost_restore(docx: Path) -> None:
    assert ops.check(docx)["state"] == ops.NEVER
    ops.embed(docx, graph_for(docx))
    report = ops.check(docx)
    assert report["state"] == ops.CURRENT
    assert report["evidence"]["counts"]["verified"] == 2
    ops.strip(docx, keep_props=True)
    lost = ops.check(docx)
    assert lost["state"] == ops.LOST and lost["sidecar"]["restorable"]
    ops.restore(docx)
    assert ops.check(docx)["state"] == ops.CURRENT


def test_stale_counts_missing_evidence(docx: Path) -> None:
    ops.embed(docx, graph_for(docx))
    replace_text(docx, "quartalsweise", "monatlich")
    report = ops.check(docx)
    assert report["state"] == ops.STALE
    assert report["evidence"]["total"] == 2
    assert report["evidence"]["counts"]["not_found"] == 1
    assert "1 von 2 Belegen" in report["message"]
    assert report["evidence"]["missing"][0]["evidence"] == "evidence-wartung"


def test_stale_without_missing_evidence_says_so(docx: Path) -> None:
    ops.embed(docx, graph_for(docx, quotes={"anlage": QUOTES["anlage"]}))
    replace_text(docx, "quartalsweise", "monatlich")
    report = ops.check(docx)
    assert report["state"] == ops.STALE
    assert "alle 1 Belege sind weiterhin auffindbar" in report["message"]


def test_raw_mode_stale_by_fingerprint(docx: Path) -> None:
    ops.embed(docx)
    replace_text(docx, "Mai", "Juni")
    report = ops.check(docx)
    assert report["state"] == ops.STALE and report["mode"] == "raw"


def test_unreadable_inputs(workdir: Path) -> None:
    junk = workdir / "kaputt.docx"
    junk.write_bytes(b"kein zip")
    assert ops.check(junk)["state"] == ops.UNREADABLE
    macro = make_docx(workdir / "makro.docm", ["x"])
    assert ops.check(macro)["state"] == ops.UNREADABLE
    with zipfile.ZipFile(make_docx(workdir / "vba.docx", ["x"]), "a") as z:
        z.writestr("word/vbaProject.bin", b"\0")
    assert ops.check(workdir / "vba.docx")["state"] == ops.UNREADABLE


def test_lock_file_blocks_writing(docx: Path) -> None:
    (docx.parent / ("~$" + docx.name[2:])).write_bytes(b"")
    assert ops.check(docx)["locked"]
    with pytest.raises(ops.OpError, match="geöffnet"):
        ops.embed(docx)


def test_foreign_app_warning(workdir: Path) -> None:
    path = make_docx(workdir / "pages.docx", ["x"], app="Pages")
    assert any("Pages" in w for w in ops.check(path)["warnings"])


# -- Graph-Abläufe -------------------------------------------------------------------

def test_embed_refuses_missing_evidence(docx: Path) -> None:
    bad = graph_for(docx, quotes={"x": "Dieser Satz steht nicht im Dokument."})
    with pytest.raises(ops.OpError, match="nicht auffindbar"):
        ops.embed(docx, bad)
    assert ooxml.Package(docx).find_knowledge() is None
    ops.embed(docx, bad, allow_missing_evidence=True)


def test_embed_refuses_invalid_graph(docx: Path) -> None:
    graph = graph_for(docx)
    data = json.loads(graph.read_text())
    del data["nodes"][0]["temporal"]
    graph.write_text(json.dumps(data))
    with pytest.raises(ops.OpError, match="nicht konform"):
        ops.embed(docx, graph)


def test_second_embed_requires_update_or_replace(docx: Path) -> None:
    ops.embed(docx, graph_for(docx))
    other = graph_for(docx, quotes={"anlage": QUOTES["anlage"]}, name="other.json")
    with pytest.raises(ops.OpError, match="update"):
        ops.embed(docx, other)
    ops.embed(docx, other, replace=True)
    assert list((ops.sidecar_dir(docx) / "versions").glob("*.replaced.json"))


def test_update_merges_additively_and_archives(docx: Path) -> None:
    ops.embed(docx, graph_for(docx, quotes={"anlage": QUOTES["anlage"]}))
    replace_text(docx, "quartalsweise", "monatlich")
    incoming = graph_for(docx, quotes={"wartung": "Wartung erfolgt monatlich."}, name="neu.json")
    result = ops.update(docx, incoming)
    assert result["merge"]["aligned_source_digest"]["source_id"] == "source-doc"
    side = ops.sidecar_dir(docx)
    assert (side / "knowledge.diff.json").is_file() and (side / "knowledge.diff.md").is_file()
    assert any((side / "versions").iterdir())
    merged = json.loads(ooxml.Package(docx).find_knowledge().payload.graph_json)
    assert {n["id"] for n in merged["nodes"]} == {"anlage", "wartung"}
    report = ops.check(docx)
    assert report["state"] == ops.CURRENT
    assert report["evidence"]["counts"] == {"verified": 2, "not_found": 0, "unverifiable": 0}


def test_render_graph_markdown(docx: Path) -> None:
    ops.embed(docx, graph_for(docx))
    text = ops.render(docx)
    assert text.startswith("---") and "Anlage" in text


# -- Adapter -------------------------------------------------------------------------

def test_xlsx_rows_and_embed(workdir: Path) -> None:
    path = make_xlsx(workdir / "t.xlsx", {"Umsatz": [["Monat", "Wert"], ["Mai", "12.5"]], "Leer": []})
    normalized = distiller.extract(path)
    assert [s["text"] for s in normalized["segments"]] == ["Monat\tWert", "Mai\t12.5"]
    assert normalized["segments"][1]["selector"] == {"type": "CsvSelector", "sheet": "Umsatz", "cell_range": "A2:B2"}
    ops.embed(path)
    assert ops.check(path)["state"] == ops.CURRENT
    assert "| Mai | 12.5 |" in ops.render(path)


def test_pptx_fragments_pages_and_notes(workdir: Path) -> None:
    path = make_pptx(workdir / "f.pptx", [["Titel", "Punkt"], ["Zwei"]], notes={1: "Notiz"})
    segments = distiller.extract(path)["segments"]
    assert [s["locator"] for s in segments] == [
        "ppt/slides/slide1.xml#paragraph=1", "ppt/slides/slide1.xml#paragraph=2",
        "ppt/notesSlides/notesSlide1.xml#paragraph=1", "ppt/slides/slide2.xml#paragraph=1"]
    assert {"type": "PageSelector", "page": 2} in segments[-1]["selectors"]
    graph = graph_for(path, quotes={"titel": "Titel"})
    ops.embed(path, graph)
    assert ops.check(path)["evidence"]["counts"]["verified"] == 1


def test_adapter_rejects_dtd(workdir: Path) -> None:
    path = make_xlsx(workdir / "dtd.xlsx", {"A": [["x"]]})
    pkg = ooxml.Package(path)
    pkg.write("xl/workbook.xml", b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]>' + pkg.read("xl/workbook.xml").split(b"?>", 1)[1])
    pkg.save()
    assert ops.check(path)["state"] == ops.UNREADABLE


# -- CLI -----------------------------------------------------------------------------

def test_cli_check_directory_json(docx: Path, capsys) -> None:
    ops.embed(docx)
    make_xlsx(docx.parent / "t.xlsx", {"A": [["x"]]})
    code = cli.main(["check", str(docx.parent), "--json"])
    reports = json.loads(capsys.readouterr().out)
    assert code == 0
    assert {Path(r["file"]).name: r["state"] for r in reports} == {"Bericht.docx": "current", "t.xlsx": "never"}


def test_cli_exit_codes(docx: Path, capsys) -> None:
    ops.embed(docx)
    replace_text(docx, "Mai", "Juni")
    assert cli.main(["check", str(docx)]) == 1
    ops.strip(docx, keep_props=True)
    assert cli.main(["check", str(docx)]) == 3
    assert cli.main(["embed", str(docx.parent / "fehlt.docx")]) == 2
