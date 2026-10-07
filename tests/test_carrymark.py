from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from carrymark import cli, container, converter, ooxml, ops, sync
from carrymark.fixtures import make_docx, make_pdf, make_pptx, make_xlsx

PARAS = ["Die Anlage läuft seit Mai.", "Wartung erfolgt quartalsweise."]


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    return tmp_path.resolve()


@pytest.fixture
def docx(workdir: Path) -> Path:
    return make_docx(workdir / "Bericht.docx", PARAS)


def replace_text(path: Path, old: str, new: str) -> None:
    pkg = ooxml.Package(path)
    part = pkg.main_part()
    pkg.write(part, pkg.read(part).replace(old.encode(), new.encode()))
    pkg.save()


# -- Konvertierung mit markitdown ------------------------------------------------------

def test_markitdown_converts_all_three_formats(workdir: Path) -> None:
    docx = make_docx(workdir / "a.docx", PARAS)
    xlsx = make_xlsx(workdir / "b.xlsx", {"Umsatz": [["Monat", "Wert"], ["Mai", "12.5"]]})
    pptx = make_pptx(workdir / "c.pptx", [["Titel", "Punkt"], ["Zwei"]], notes={1: "Notiz"})
    assert "Die Anlage läuft seit Mai." in converter.convert(docx).body
    assert "| Mai | 12.5 |" in converter.convert(xlsx).body
    body = converter.convert(pptx).body
    assert "Titel" in body and "Notiz" in body and "Zwei" in body


def test_normalize_and_fingerprint_ignore_whitespace_noise() -> None:
    a = converter.normalize("Zeile  \r\n\r\n\r\n\r\nNoch eine\n\n")
    assert a == "Zeile\n\nNoch eine\n"
    assert converter.fingerprint("Zeile\n\nNoch eine") == converter.fingerprint(a)
    assert converter.fingerprint("anders") != converter.fingerprint(a)


def test_document_has_okf_frontmatter(docx: Path) -> None:
    conv = converter.convert(docx)
    doc = conv.document(docx.name, "2026-10-07T10:00:00Z")
    head, body = converter.split_frontmatter(doc)
    assert body == conv.body
    assert 'type: "Office Document"' in head
    assert 'title: "Bericht"' in head and 'resource: "Bericht.docx"' in head
    assert 'tags: ["docx"]' in head
    assert 'generated: { by: "carrymark/' in head and '"2026-10-07T10:00:00Z"' in head
    assert f'fingerprint: "{conv.fingerprint}"' in head
    assert 'converter: "markitdown/' in head


def test_frontmatter_uses_core_properties(workdir: Path) -> None:
    path = make_docx(workdir / "m.docx", ["Text."])
    pkg = ooxml.Package(path)
    pkg.write("docProps/core.xml", (
        '<?xml version="1.0" encoding="UTF-8"?><cp:coreProperties '
        'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>Quartalsbericht</dc:title>'
        '<dc:creator>Erika Muster</dc:creator><dcterms:modified xsi:type="dcterms:W3CDTF">2026-09-30T08:00:00Z'
        '</dcterms:modified></cp:coreProperties>').encode())
    pkg.save()
    head, _ = converter.split_frontmatter(converter.convert(path).document(path.name))
    assert 'title: "Quartalsbericht"' in head
    assert 'author: "Erika Muster"' in head and 'last_modified: "2026-09-30T08:00:00Z"' in head


# -- Paketstruktur -------------------------------------------------------------------

def test_embed_registers_part_like_office(docx: Path) -> None:
    ops.embed(docx)
    with zipfile.ZipFile(docx) as z:
        names = z.namelist()
        assert names[0] == "[Content_Types].xml"
        assert {"customXml/item1.xml", "customXml/itemProps1.xml",
                "customXml/_rels/item1.xml.rels", "docProps/custom.xml"} <= set(names)
        assert 'PartName="/customXml/itemProps1.xml"' in z.read("[Content_Types].xml").decode()
        assert "relationships/customXml" in z.read("word/_rels/document.xml.rels").decode()
        assert "custom-properties" in z.read("_rels/.rels").decode()
    part = ooxml.Package(docx).find_knowledge()
    assert part.registered and part.guid
    assert part.payload.markdown.startswith("---\ntype:")
    assert ooxml.Package(docx).read_props()["PartGuid"] == part.guid


def test_document_parts_stay_byte_identical(docx: Path) -> None:
    with zipfile.ZipFile(docx) as z:
        before = z.read("word/document.xml")
    ops.embed(docx)
    with zipfile.ZipFile(docx) as z:
        assert z.read("word/document.xml") == before


def test_own_part_does_not_change_markitdown_output(workdir: Path) -> None:
    for path in (make_docx(workdir / "a.docx", PARAS),
                 make_xlsx(workdir / "b.xlsx", {"A": [["x", "1"]]}),
                 make_pptx(workdir / "c.pptx", [["Titel"]])):
        before = converter.convert(path).fingerprint
        ops.embed(path)
        assert converter.convert(path).fingerprint == before


def test_reembed_keeps_guid_and_single_part(docx: Path) -> None:
    ops.embed(docx)
    guid = ooxml.Package(docx).find_knowledge().guid
    replace_text(docx, "Mai", "Juni")
    ops.embed(docx)
    pkg = ooxml.Package(docx)
    assert pkg.find_knowledge().guid == guid
    assert sum(1 for n in pkg.names() if n.startswith("customXml/item") and "Props" not in n
               and n.endswith(".xml")) == 1


def test_foreign_custom_props_survive_strip(workdir: Path) -> None:
    path = make_docx(workdir / "p.docx", ["Text."])
    pkg = ooxml.Package(path)
    pkg.write_props({"Tmp": "x"})
    pkg.write("docProps/custom.xml", pkg.read("docProps/custom.xml").replace(b"CarrymarkTmp", b"Kunde"))
    pkg.save()
    ops.embed(path)
    ops.strip(path)
    custom = ooxml.Package(path).read("docProps/custom.xml").decode()
    assert 'name="Kunde"' in custom and "Carrymark" not in custom


def test_cdata_terminator_roundtrips(docx: Path) -> None:
    pkg = ooxml.Package(docx)
    pkg.embed(ooxml.Payload(fingerprint="f", embedded_at="t", markdown="a ]]> b"))
    pkg.save()
    assert ooxml.Package(docx).find_knowledge().payload.markdown == "a ]]> b"


# -- Zustände ------------------------------------------------------------------------

def test_states_never_current_stale_lost_restore(docx: Path) -> None:
    assert ops.check(docx)["state"] == ops.NEVER
    ops.embed(docx)
    assert ops.check(docx)["state"] == ops.CURRENT
    replace_text(docx, "quartalsweise", "monatlich")
    assert ops.check(docx)["state"] == ops.STALE
    ops.embed(docx)
    ops.strip(docx, keep_props=True)
    lost = ops.check(docx)
    assert lost["state"] == ops.LOST and lost["sidecar"]["restorable"]
    ops.restore(docx)
    assert ops.check(docx)["state"] == ops.CURRENT


def test_unreadable_inputs(workdir: Path) -> None:
    junk = workdir / "kaputt.docx"
    junk.write_bytes(b"kein zip")
    assert ops.check(junk)["state"] == ops.UNREADABLE
    assert ops.check(make_docx(workdir / "makro.docm", ["x"]))["state"] == ops.UNREADABLE
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


# -- sync ----------------------------------------------------------------------------

def test_sync_paths(docx: Path) -> None:
    first = sync.sync_file(docx)
    assert (first["before"], first["action"], first["after"]) == ("never", "embed", "current")
    assert sync.sync_file(docx)["action"] == "none"
    replace_text(docx, "Mai", "Juni")
    again = sync.sync_file(docx)
    assert (again["action"], again["after"]) == ("reembed", "current")
    assert "Juni" in ops.render(docx)


def test_sync_lost_restores_or_reembeds(docx: Path) -> None:
    sync.sync_file(docx)
    ops.strip(docx, keep_props=True)
    assert sync.sync_file(docx)["action"] == "restore"
    ops.strip(docx, keep_props=True)
    replace_text(docx, "Mai", "Juni")
    result = sync.sync_file(docx)
    assert (result["action"], result["after"]) == ("reembed", "current")


def test_sync_dry_run_and_skips(workdir: Path, docx: Path) -> None:
    assert sync.sync_file(docx, dry_run=True)["action"] == "plan"
    assert ooxml.Package(docx).find_knowledge() is None
    junk = workdir / "kaputt.docx"
    junk.write_bytes(b"x")
    assert sync.sync_file(junk)["action"] == "skip"
    (workdir / ("~$" + docx.name[2:])).write_bytes(b"")
    assert sync.sync_file(docx)["action"] == "skip"


# -- Export --------------------------------------------------------------------------

def test_export_next_to_file_and_okf_bundle(workdir: Path) -> None:
    src = workdir / "src"
    src.mkdir()
    make_docx(src / "Bericht.docx", PARAS)
    make_xlsx(src / "Zahlen.xlsx", {"A": [["x", "1"]]})
    results = ops.export([src])
    assert sorted(Path(r["markdown"]).name for r in results) == ["Bericht.docx.md", "Zahlen.xlsx.md"]
    bundle = workdir / "bundle"
    ops.export([src], bundle, okf=True)
    index = (bundle / "index.md").read_text()
    assert index.startswith('---\nokf_version: "0.2"\n---\n')
    assert "* [Bericht.docx](/bericht-docx.md) - DOCX-Dokument" in index
    concept = (bundle / "bericht-docx.md").read_text()
    head, _ = converter.split_frontmatter(concept)
    assert 'type: "Office Document"' in head


def test_export_prefers_embedded_current_document(docx: Path) -> None:
    ops.embed(docx)
    embedded = ops.render(docx)
    ops.export([docx])
    assert docx.with_name(docx.name + ".md").read_text() == embedded


# -- CLI -----------------------------------------------------------------------------

def test_cli_check_directory_json(docx: Path, capsys) -> None:
    ops.embed(docx)
    make_xlsx(docx.parent / "t.xlsx", {"A": [["x"]]})
    assert cli.main(["check", str(docx.parent), "--json"]) == 0
    reports = json.loads(capsys.readouterr().out)
    assert {Path(r["file"]).name: r["state"] for r in reports} == {"Bericht.docx": "current", "t.xlsx": "never"}


def test_cli_exit_codes_and_render(docx: Path, capsys) -> None:
    assert cli.main(["sync", str(docx)]) == 0
    replace_text(docx, "Mai", "Juni")
    assert cli.main(["check", str(docx)]) == 1
    ops.strip(docx, keep_props=True)
    assert cli.main(["check", str(docx)]) == 3
    cli.main(["sync", str(docx)])
    capsys.readouterr()
    assert cli.main(["render", str(docx), "--plain"]) == 0
    assert capsys.readouterr().out.startswith("Die Anlage")
    assert cli.main(["embed", str(docx.parent / "fehlt.docx")]) == 2


def test_legacy_officemd_part_is_recognized_and_migrated(docx: Path) -> None:
    ops.embed(docx)
    pkg = ooxml.Package(docx)
    part = pkg.find_knowledge()
    # Simuliert eine mit OfficeMD 0.2 eingebettete Datei: alter Namespace, alte Properties.
    pkg.write(part.item_name, pkg.read(part.item_name).replace(b"urn:carrymark:knowledge:1", b"urn:officemd:knowledge:1"))
    pkg.write("docProps/custom.xml", pkg.read("docProps/custom.xml").replace(b'name="Carrymark', b'name="OfficeMD'))
    pkg.save()
    legacy = ooxml.Package(docx)
    assert legacy.find_knowledge() is not None
    assert legacy.read_props()["PartGuid"] == part.guid
    assert ops.check(docx)["state"] in (ops.CURRENT, ops.STALE)
    ops.embed(docx)
    migrated = ooxml.Package(docx)
    assert b"urn:carrymark:knowledge:1" in migrated.read(part.item_name)
    custom = migrated.read("docProps/custom.xml").decode()
    assert "OfficeMD" not in custom and "CarrymarkPartGuid" in custom


# -- Vereinfachungen aus /simplify und /code-review --------------------------------------

def test_lost_is_detected_even_if_conversion_fails(docx: Path, monkeypatch) -> None:
    ops.embed(docx)
    ops.strip(docx, keep_props=True)

    def broken(*_args, **_kwargs):
        raise converter.ConversionError("python-pptx kann das nicht lesen")

    monkeypatch.setattr(converter, "convert", broken)
    report = ops.check(docx)
    assert report["state"] == ops.LOST and report["sidecar"]["restorable"]


def test_sync_converts_each_file_once(docx: Path, monkeypatch) -> None:
    calls = []
    original = converter.convert

    def counting(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(converter, "convert", counting)
    assert sync.sync_file(docx)["after"] == ops.CURRENT
    assert len(calls) == 1
    assert ops.check(docx)["state"] == ops.CURRENT


def test_office_files_ignores_unsupported_paths(workdir: Path, docx: Path) -> None:
    notes = workdir / "Notizen.txt"
    notes.write_text("x")
    (workdir / "Bericht.docx.carrymark").mkdir()
    files, ignored = ops.office_files([workdir, notes])
    assert files == [docx] and ignored == [notes]


def test_export_keeps_same_named_files_apart(workdir: Path) -> None:
    for folder in ("a", "b"):
        (workdir / folder).mkdir()
        make_docx(workdir / folder / "Bericht.docx", [f"Text {folder}"])
    out = workdir / "out"
    results = ops.export([workdir / "a", workdir / "b"], out)
    assert sorted(Path(r["markdown"]).name for r in results) == ["Bericht.docx-2.md", "Bericht.docx.md"]


def test_okf_index_escapes_brackets() -> None:
    index = converter.okf_index([("Plan [v2].docx", "/plan-v2-docx.md", "DOCX-Dokument")])
    assert "* [Plan \\[v2\\].docx](/plan-v2-docx.md)" in index


def test_office_files_lists_each_file_once(workdir: Path, docx: Path) -> None:
    files, _ = ops.office_files([workdir, docx, workdir / "." / docx.name])
    assert files == [docx]


# -- PDF -------------------------------------------------------------------------------

PDF_PAGES = [["Angebot Nr. 4711", "Preis: 12.500 EUR netto"], ["Gueltig bis 31.12.2026"]]


@pytest.fixture
def pdf(workdir: Path) -> Path:
    return make_pdf(workdir / "Angebot.pdf", PDF_PAGES, title="Angebot 4711")


def test_pdf_embed_as_attachment_with_metadata(pdf: Path) -> None:
    from pypdf import PdfReader

    assert ops.check(pdf)["state"] == ops.NEVER
    result = ops.embed(pdf)
    reader = PdfReader(str(pdf))
    attachments = list(reader.attachment_list)
    assert [a.name for a in attachments] == ["carrymark.md"]
    assert str(attachments[0].subtype) == "/text/markdown"
    assert str(attachments[0].associated_file_relationship) == "/Alternative"
    assert reader.metadata["/CarrymarkPartGuid"] == result["guid"]
    assert reader.metadata["/Title"] == "Angebot 4711"  # vorhandene Metadaten bleiben
    document = ops.render(pdf)
    assert 'type: "PDF Document"' in document and "Angebot Nr. 4711" in document
    assert ops.check(pdf)["state"] == ops.CURRENT


def test_pdf_reembed_keeps_single_attachment_and_guid(pdf: Path) -> None:
    from pypdf import PdfReader

    guid = ops.embed(pdf)["guid"]
    for _ in range(2):
        assert ops.embed(pdf)["guid"] == guid
    assert len(list(PdfReader(str(pdf)).attachment_list)) == 1
    assert ops.check(pdf)["state"] == ops.CURRENT


def test_pdf_states_stale_lost_restore(pdf: Path, workdir: Path) -> None:
    ops.embed(pdf)
    embedded = ops.render(pdf)
    ops.strip(pdf, keep_props=True)
    assert ops.check(pdf)["state"] == ops.LOST
    ops.restore(pdf)
    assert ops.check(pdf)["state"] == ops.CURRENT
    # Gleiches Markdown in einer PDF mit anderem Text: veraltet.
    other = make_pdf(workdir / "Neu.pdf", [["Angebot Nr. 4712"]])
    pkg = container.open_package(other)
    pkg.embed(ooxml.Payload(fingerprint=converter.split_frontmatter(embedded)[0].split('fingerprint: "')[1].split('"')[0],
                            embedded_at="t", markdown=embedded))
    pkg.save()
    assert ops.check(other)["state"] == ops.STALE


def test_pdf_signed_and_encrypted_are_not_touched(workdir: Path) -> None:
    from pypdf import PdfReader, PdfWriter

    signed = make_pdf(workdir / "signiert.pdf", [["Vertrag"]], signed=True)
    report = ops.check(signed)
    assert report["state"] == ops.UNREADABLE and "Signatur" in report["message"]
    plain = make_pdf(workdir / "offen.pdf", [["Geheim"]])
    writer = PdfWriter(clone_from=PdfReader(str(plain)))
    writer.encrypt("passwort")
    with open(workdir / "verschluesselt.pdf", "wb") as handle:
        writer.write(handle)
    assert ops.check(workdir / "verschluesselt.pdf")["state"] == ops.UNREADABLE
    with pytest.raises(ops.OpError):
        ops.embed(signed)


def test_pdf_sync_and_export(pdf: Path, workdir: Path) -> None:
    assert sync.sync_file(pdf)["after"] == ops.CURRENT
    files, _ = ops.office_files([workdir])
    assert pdf in files
    ops.export([pdf])
    assert pdf.with_name("Angebot.pdf.md").read_text() == ops.render(pdf)


# -- Excel-Artefakte von markitdown --------------------------------------------------------

def test_nan_and_unnamed_cells_are_cleared() -> None:
    text = converter.normalize("| Titel | Unnamed: 1 |\n| --- | --- |\n| NaN | NaN |\n| a | NaN-Wert |\n")
    assert text == "| Titel | |\n| --- | --- |\n| | |\n| a | NaN-Wert |\n"
    assert converter.normalize("Text NaN bleibt\n") == "Text NaN bleibt\n"
