# Carrymark

Markdown, das in der Datei bleibt. Carrymark wandelt Word-, Excel-, PowerPoint- und PDF-Dateien
mit **[microsoft/markitdown](https://github.com/microsoft/markitdown)** in Markdown um, legt das
Ergebnis direkt in die Datei und merkt später, ob es noch zum Inhalt passt.

Keine KI, kein Netz: alles läuft lokal auf deinem Rechner.

![Carrymark-App mit einer geänderten Excel-Datei und Tabellenvorschau](docs/app.png)

Landingpage: [godmodeai2025.github.io/OfficeMD](https://godmodeai2025.github.io/OfficeMD/)

## Wofür

- **Die Datei trägt ihr Markdown selbst.** Wer sie per Mail bekommt, bekommt die
  Markdown-Fassung mit: in Office-Dateien als regulären Custom-XML-Part, in PDFs als Anhang
  `carrymark.md`, so wie ZUGFeRD-Rechnungen ihr XML tragen.
- **Veraltet wird erkannt.** Ändert jemand die Datei, merkt Carrymark das und bettet auf Wunsch
  neu ein. Ein `sync` über einen ganzen Ordner hält alles aktuell.
- **Verlust wird erkannt.** Entfernt ein Programm den Part beim Speichern, bleibt ein kleiner
  Fingerprint-Eintrag in den Dokumenteigenschaften zurück. Carrymark meldet dann „Verloren“ und
  stellt das Markdown aus einer Sicherung wieder her.
- **Bereit für Wissenssysteme.** Das Markdown trägt eine Frontmatter nach dem
  [Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)
  (OKF), und ganze Ordner lassen sich als OKF-Bundle exportieren.

## Die Umwandlung kommt von Microsoft

Die eigentliche Arbeit macht **[markitdown](https://github.com/microsoft/markitdown)** von
Microsoft (MIT-Lizenz): Absätze und Überschriften aus Word, Tabellen je Blatt aus Excel, Folien
samt Sprechernotizen aus PowerPoint, Text aus PDFs. Carrymark ruft markitdown lokal auf und kümmert sich um
das Einbetten, Prüfen, Wiederherstellen und Exportieren. Die verwendete markitdown-Version steht
in jedem eingebetteten Dokument (`converter: "markitdown/0.1.8"`).

markitdown kann noch mehr Formate lesen (HTML, Bilder, Audio). Einbetten lässt sich Markdown aber
nur in Formate, die dafür einen Platz haben: Office-Pakete und PDFs. Deshalb nimmt Carrymark nur
DOCX, XLSX, PPTX und PDF an.

Excel liest markitdown über pandas; leere Zellen kämen dabei als `NaN` und Spalten ohne Kopf als
`Unnamed: 3` heraus. Carrymark leert beides in den Tabellen, der Inhalt bleibt vollständig.

## Schnellstart

```bash
git clone https://github.com/GodModeAI2025/OfficeMD.git carrymark
cd carrymark
./carrymark setup              # einmalig: .venv mit Python 3.12 und markitdown
./carrymark sync ~/Dokumente   # alles prüfen, bei Bedarf einbetten oder wiederherstellen
```

`setup` nutzt [uv](https://docs.astral.sh/uv/), sonst `python3 -m venv` (Python 3.10 oder neuer).
Alternativ: `pip install -e .`, dann steht der Befehl `carrymark` bereit.

```text
$ carrymark check Projektbericht.docx
Aktuell       Projektbericht.docx
              Eingebettetes Markdown passt zum aktuellen Inhalt.

$ carrymark sync ~/Dokumente
Aktuell       Projektbericht.docx: Markdown neu eingebettet (114 Zeichen, markitdown/0.1.8).
Aktuell       Umsatz 2026.xlsx: Aktuell, nichts zu tun.
Aktuell       Vertrag Lieferant.docx: Aus dem Sidecar wiederhergestellt.
```

## Befehle

| Befehl | Zweck |
|---|---|
| `check PFAD… [--json]` | Zustand anzeigen. Ordner werden rekursiv durchsucht. |
| `sync PFAD… [--dry-run]` | Prüfen und bei Bedarf einbetten, neu einbetten oder wiederherstellen |
| `embed PFAD…` | Markdown erzeugen und einbetten |
| `convert DATEI [-o] [--plain]` | Markdown erzeugen und ausgeben, ohne einzubetten |
| `render DATEI [-o] [--plain]` | Eingebettetes Markdown ausgeben |
| `export PFAD… [-o ORDNER] [--okf]` | `.md` neben die Dateien schreiben oder als OKF-Bundle exportieren |
| `restore DATEI` | Verlorenen Part aus der Sicherung zurückschreiben |
| `strip DATEI [--keep-props]` | Part entfernen; mit `--keep-props` lässt sich „Verloren“ simulieren |
| `selftest [--office]` | Pakettest, mit `--office` echter Roundtrip in Word, Excel und PowerPoint |
| `setup` | `.venv` mit markitdown anlegen |

`--plain` lässt die Frontmatter weg. Exit-Codes von `check`: 0 alles aktuell oder nicht
eingebettet, 1 mindestens eine Datei veraltet, 3 verloren oder nicht lesbar, 2 Bedienfehler.

## Zustände

| Zustand | Bedeutung |
|---|---|
| Nicht eingebettet | Kein Part, kein Fingerprint-Eintrag |
| Aktuell | Das eingebettete Markdown entspricht dem, was markitdown heute erzeugt |
| Veraltet | Die Datei wurde seit dem Einbetten geändert |
| Verloren | Fingerprint-Eintrag vorhanden, Part fehlt |
| Nicht lesbar | Verschlüsselt, signiert (PDF), Makros (`.docm`, `vbaProject.bin`), beschädigt, DTD im XML |

Der Fingerprint (`cm-md-v1:…`) ist ein SHA-256 über das normalisierte Markdown, ohne
Frontmatter. Neues Speichern in Office ohne inhaltliche Änderung lässt ihn unverändert. Eine
neue markitdown-Version kann die Ausgabe ändern; dann meldet Carrymark „Veraltet“ und `sync`
bettet einfach neu ein.

## Das eingebettete Markdown

```markdown
---
type: "Office Document"         # bei PDFs: "PDF Document"
title: "Quartalsbericht"
resource: "Quartalsbericht.docx"
tags: ["docx"]
sources:
  - id: "document"
    resource: "Quartalsbericht.docx"
    title: "Quartalsbericht"
    author: "Erika Muster"
    last_modified: "2026-09-30T08:00:00Z"
generated: { by: "carrymark/0.2.0", at: "2026-10-07T10:00:00Z" }
carrymark:
  fingerprint: "cm-md-v1:…"
  converter: "markitdown/0.1.8"
---
Die Anlage läuft seit Mai stabil.
…
```

Die Felder folgen OKF v0.2: `type` ist Pflicht, `title`, `resource` und `tags` sind empfohlen,
`sources` und `generated` beschreiben Herkunft und Erzeugung. Titel, Autor und Änderungsdatum
stammen aus den Dokumenteigenschaften (`docProps/core.xml`). Eigene Felder stehen unter
`carrymark:`; OKF erlaubt zusätzliche Felder ausdrücklich.

`carrymark export ORDNER --okf -o bundle/` legt je Dokument eine `.md` an und schreibt eine
`index.md` mit `okf_version: "0.2"` und Links auf alle Dokumente, so wie OKF es für Bundles
vorsieht.

## Wie der Part im Paket liegt

So, wie Office selbst Custom-XML-Datastores anlegt:

```text
customXml/item1.xml              <cm:knowledge> mit dem Markdown (CDATA)
customXml/itemProps1.xml         ds:datastoreItem mit eigener GUID und Schema-Referenz
customXml/_rels/item1.xml.rels   Item -> ItemProps
word/_rels/document.xml.rels     Relationship vom Typ customXml auf das Item
[Content_Types].xml              Override für itemProps
docProps/custom.xml              CarrymarkFingerprint, -Version, -PartGuid, -Mode, -EmbeddedAt
```

Bei Excel und PowerPoint hängt die Relationship an `xl/workbook.xml` bzw.
`ppt/presentation.xml`. Den Dokumentinhalt (`word/document.xml` usw.) fasst Carrymark nie an.
Geschrieben wird atomar über eine temporäre Datei; ist die Datei in Office geöffnet (`~$…`) oder
hat sie sich seit dem Lesen geändert, bricht Carrymark ab. Neben der Datei liegt eine Sicherung
im Ordner `Datei.docx.carrymark/` (`markdown.md`, `embed.json`).

### In PDFs

PDF kann Dateien tragen. Carrymark legt das Markdown als eingebettete Datei `carrymark.md` an,
Medientyp `text/markdown`, Beziehung `Alternative` (eine andere Darstellung desselben Inhalts,
wie PDF/A-3 es vorsieht). Fingerprint, GUID und Zeitpunkt stehen zusätzlich in den
Dokumentinformationen (`/CarrymarkFingerprint`, `/CarrymarkPartGuid` …); fehlt der Anhang, die
Einträge aber nicht, meldet Carrymark „Verloren“. Seiten und Text bleiben unverändert, die
Datei wird mit [pypdf](https://github.com/py-pdf/pypdf) neu geschrieben. Signierte und
verschlüsselte PDFs fasst Carrymark nicht an, weil das eine Signatur ungültig machen würde.
PDF-Programme mit Anhangsansicht, etwa Acrobat Reader, zeigen das Markdown als Anhang.

Ältere Dateien aus der Vorgängerversion (OfficeMD) erkennt Carrymark weiterhin; `sync` stellt sie
beim nächsten Einbetten auf das aktuelle Format um.

## Mit einer KI nutzen

Chat-Oberflächen lesen beim Hochladen einer Office-Datei oder PDF meist nur den sichtbaren Text,
nicht das eingebettete Markdown. Zwei Wege:

- der KI sagen: „Die Datei ist ein ZIP. Unter `customXml/item1.xml` liegt ein Markdown-Abbild
  (Carrymark). Lies es aus und arbeite damit.“ Bei PDFs: „Die PDF hat einen Anhang
  `carrymark.md`.“ Mit Code-Ausführung klappt das.
- oder direkt die `.md` aus `carrymark export` bzw. das OKF-Bundle mitgeben.

In der Mac-App stehen diese Hinweise unter Einstellungen > „Mit KI nutzen“, mit fertigen
Prompts für Office und PDF sowie einem Python-Schnipsel für Agenten, jeweils zum Kopieren.

## Was getestet ist und was nicht

Der Office-Roundtrip (`carrymark selftest --office`, Berichte in [`compat/`](compat/)) lief am
2026-10-07 auf macOS mit Word 16.113.4, Excel 16.113.3 und PowerPoint 16.113.4: Datei in der App
anlegen, Markdown einbetten, in der App öffnen, Text ergänzen, speichern. Part, Registrierung,
GUID, Fingerprint-Eintrag und das vollständige Markdown blieben in allen drei Apps erhalten.

Nicht getestet: Office für Windows und im Browser, der Dokumentinspektor, Pages, Google Docs,
LibreOffice, und für PDFs das Speichern in Vorschau oder Acrobat nach dem Einbetten. Die „möglichen Ursachen“, die `check` bei „Verloren“ nennt, sind bis dahin Annahmen.

Die Testsuite (`python -m pytest`) und der Selbsttest laufen in der
[CI](.github/workflows/ci.yml) auf Linux und macOS mit Python 3.10 und 3.12, dazu ein Build der
Mac-App.

## Mac-App

Unter `app/` liegt die SwiftUI-Oberfläche. Sie ruft das CLI auf und enthält selbst keine
Paketlogik. Dateien oder Ordner hineinziehen; angenommen werden nur DOCX, XLSX, PPTX und PDF.
Dateien, die sich nicht verarbeiten lassen (verschlüsselt, signiert, Makros, beschädigt),
erscheinen nicht in der Liste, sondern als Hinweis unten in der Seitenleiste. Die App merkt sich
die aufgenommenen Ordner und Dateien.

Je Datei: Zustand, Vorschau des Markdowns (Tabellen als Tabellen, Folien als Abschnitte),
„Einbetten“, „Aktualisieren“ oder „Wiederherstellen“, „Als .md sichern“. In der Symbolleiste:
alle prüfen, alle aktualisieren, als OKF-Bundle exportieren.

```bash
cd app && swift run                # Entwicklung, nutzt ./carrymark aus dem Repository
scripts/build-app.sh               # fertiges dist/Carrymark.app samt Zip
```

`scripts/build-app.sh` baut eine eigenständige App mit eingebettetem Python 3.12, markitdown und pypdf;
auf dem Zielrechner braucht es weder Python noch Git. Das Skript signiert jedes Binary und das
Bundle mit Hardened Runtime (Identität automatisch: Developer ID, sonst Apple Development, sonst
ad hoc; festlegen mit `SIGN_IDENTITY=…`) und lässt den Selbsttest im Bundle laufen. Rund 300 MB
gehen auf markitdowns Abhängigkeiten zurück (onnxruntime für die Dateityperkennung, pandas für
Excel).

Für andere Macs muss die App notarisiert sein:

```bash
xcrun notarytool store-credentials carrymark --apple-id <apple-id> --team-id <team-id>
NOTARY_PROFILE=carrymark scripts/build-app.sh
```

## Aufbau

```text
carrymark                   Startskript (nutzt .venv, falls vorhanden)
src/carrymark/
  cli.py                    Kommandozeile
  converter.py              markitdown-Aufruf, Normalisierung, Fingerprint, OKF-Frontmatter
  ops.py                    check, embed, restore, render, export, strip
  sync.py                   Prüfen und bei Bedarf aktualisieren
  ooxml.py                  Office: Part, Registrierung, custom.xml, atomares Schreiben
  pdfpkg.py                 PDF: Anhang carrymark.md, Dokumentinformationen
  container.py              öffnet Office-Paket oder PDF
  selftest.py               Pakettest und Office-Roundtrip per AppleScript
  fixtures.py               Minimale Office-Dateien für Tests
tests/                      pytest
app/                        SwiftUI-App (macOS)
scripts/build-app.sh        Signiertes App-Bundle bauen
docs/index.html             Landingpage
compat/                     Kompatibilitätsberichte
```

## Danke

- [microsoft/markitdown](https://github.com/microsoft/markitdown), MIT-Lizenz, © Microsoft
  Corporation: die Umwandlung nach Markdown.
- [Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)
  von Google Cloud Platform: Vorlage für Frontmatter und Bundles.
- [pypdf](https://github.com/py-pdf/pypdf), BSD-Lizenz: Anhänge und Metadaten in PDFs.

## Lizenz

MIT
