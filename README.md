# OfficeMD

Wissen gehört in die Datei, aus der es stammt. OfficeMD bettet einen geprüften Wissensgraphen
oder einen Markdown-Abzug direkt in DOCX, XLSX und PPTX ein und merkt später, ob dieses Wissen
noch zum Dokument passt.

Grundlage ist [Knowledge Distiller](https://github.com/GodModeAI2025/knowledge-distiller): Sein
Format `.knowledge.json` trennt Begriffe, Aussagen und Belege, und jeder Beleg zeigt auf eine
konkrete Stelle im Quelltext. OfficeMD nimmt diesen Graphen, legt ihn als regulären
Custom-XML-Part ins Office-Paket und prüft die Belege bei jedem `check` gegen den aktuellen Text.

Landingpage mit dem Konzept: [`docs/index.html`](docs/index.html)

## Was man davon hat

- Die Datei trägt ihr Wissen selbst. Wer sie per Mail bekommt, bekommt den Graphen mit.
- „Veraltet“ ist keine Ja/Nein-Frage, sondern eine Zahl: *1 von 3 Belegen sind im Dokument nicht
  mehr auffindbar.* Neu bewertet werden nur die betroffenen Aussagen.
- Aktualisieren ist ein additiver Merge. Menschliche Ergänzungen (`human_added`) und der
  Review-Status bleiben erhalten, jede alte Fassung wird archiviert, jede Änderung als Diff
  festgehalten.
- Geht der Part beim Speichern verloren, erkennt OfficeMD das und stellt ihn aus dem Sidecar
  wieder her.

## Schnellstart

```bash
git clone --recurse-submodules https://github.com/GodModeAI2025/OfficeMD.git
cd OfficeMD
./officemd selftest          # Pakettest ohne Office, ein paar Sekunden
```

Python 3.9 oder neuer, keine Abhängigkeiten außer der Standardbibliothek. Wer lieber
installiert: `pip install -e .` stellt den Befehl `officemd` bereit. Fehlt das Submodule, hilft
`git submodule update --init`.

```text
$ officemd embed Bericht.docx --graph bericht.knowledge.json
Eingebettet (graph): Bericht.docx
  Fingerprint: omd-text-v1:4eb3839de4ff…
  Part-GUID:   {056801AB-A23E-4A9E-B544-4F9F1DC3728C}
  Sidecar:     Bericht.docx.knowledge
  Belege:      3 von 3 gefunden

$ officemd check Bericht.docx          # nachdem jemand den Text geändert hat
Veraltet      Bericht.docx (graph)
              Text hat sich geändert. 1 von 3 Belegen sind im Dokument nicht mehr
              auffindbar. Betroffene Claims neu bewerten und mit officemd update einspielen.

$ officemd verify Bericht.docx
2 von 3 Belegen gefunden, 1 nicht auffindbar, 0 nicht prüfbar.
  NOT_FOUND     evidence-wartung: excerpt does not occur in the normalized source
```

## Zwei Modi

**Roh-Markdown** braucht keine KI. `officemd embed Datei.docx` extrahiert den Text
deterministisch und bettet ihn als Markdown ein: Absätze bei Word, eine Tabelle pro Blatt bei
Excel, Folien samt Notizen bei PowerPoint. Aktualität wird über den Text-Fingerprint geprüft.

**Wissensgraph** braucht einmal einen externen Agenten, der aus den Segmenten einen
`.knowledge.json` kompiliert. Die Regeln dafür stehen im Distiller (`SKILL.md`,
`profiles/default.json`). Alles danach läuft offline: Graph normalisieren, validieren, Belege
verifizieren, Markdown rendern, mergen.

```bash
officemd extract Bericht.docx -o bericht.segments.json   # Eingabe für den Agenten
# ... Agent schreibt bericht.knowledge.json ...
officemd embed Bericht.docx --graph bericht.knowledge.json
```

## Befehle

| Befehl | Zweck |
|---|---|
| `check DATEI\|ORDNER [--json]` | Zustand anzeigen. Ordner werden rekursiv durchsucht. |
| `extract DATEI [--markdown]` | Normalisierte Segmente (JSON) oder Roh-Markdown ausgeben |
| `embed DATEI [--graph G]` | Roh-Markdown oder Graph einbetten. Bricht ab, wenn Belege fehlen. |
| `verify DATEI` | Belege gegen den aktuellen Text prüfen |
| `update DATEI NEU.knowledge.json` | Additiv mergen, alte Fassung archivieren, neu einbetten |
| `render DATEI` | Markdown aus dem eingebetteten Wissen |
| `restore DATEI` | Verlorenen Part aus dem Sidecar zurückschreiben |
| `strip DATEI [--keep-props]` | Part entfernen; mit `--keep-props` lässt sich „Verloren“ simulieren |
| `selftest [--office]` | Pakettest, mit `--office` echter Roundtrip in Word, Excel und PowerPoint |

Exit-Codes von `check`: 0 alles aktuell oder nie eingebettet, 1 mindestens eine Datei veraltet,
3 verloren oder nicht lesbar, 2 Bedienfehler.

## Zustände

| Zustand | Bedeutung |
|---|---|
| Nie vorhanden | Kein Part, kein Fingerprint-Eintrag |
| Aktuell | Text-Fingerprint stimmt mit dem beim Einbetten überein |
| Veraltet | Text geändert; im Graph-Modus mit Zählung der nicht mehr auffindbaren Belege |
| Verloren | Fingerprint-Eintrag in `docProps/custom.xml` vorhanden, Part fehlt |
| Nicht lesbar | Verschlüsselt, Makros (`.docm`, `vbaProject.bin`), defektes Archiv, DTD im XML |

Zusätzlich meldet `check`, wenn Office die Datei gerade offen hat (`~$…`). Geschrieben wird dann
nicht.

## Wie der Part im Paket liegt

So, wie Office selbst Custom-XML-Datastores anlegt:

```text
customXml/item1.xml              <omd:knowledge> mit Graph (CDATA) und gerendertem Markdown
customXml/itemProps1.xml         ds:datastoreItem mit eigener GUID und Schema-Referenz
customXml/_rels/item1.xml.rels   Item -> ItemProps
word/_rels/document.xml.rels     Relationship vom Typ customXml auf das Item
[Content_Types].xml              Override für itemProps
docProps/custom.xml              OfficeMDFingerprint, -Version, -PartGuid, -Mode, -EmbeddedAt
```

Bei Excel und PowerPoint hängt die Relationship an `xl/workbook.xml` bzw.
`ppt/presentation.xml`. Dokumentinhalt wie `word/document.xml` fasst OfficeMD nie an, die Bytes
bleiben identisch. Geschrieben wird atomar über eine temporäre Datei im selben Ordner; hat sich
die Datei seit dem Lesen geändert, bricht OfficeMD ab.

Neben der Datei liegt der Sidecar-Ordner `Datei.docx.knowledge/` mit `knowledge.json`,
`knowledge.md`, `embed.json`, dem Versionsarchiv `versions/` und den Merge-Diffs.

## Was getestet ist und was nicht

Getestet mit echtem Office (macOS 27.2, `officemd selftest --office`, Bericht in
[`compat/`](compat/office-roundtrip-20261006.md)): Word 16.113.4, Excel 16.113.3 und
PowerPoint 16.113.3 behalten Part, Registrierung, GUID und Fingerprint-Eintrag, nachdem die
Datei in der App geöffnet, geändert und gespeichert wurde. Die Belegprüfung funktioniert danach
weiter.

Nicht getestet: Office für Windows, Office im Browser, der Dokumentinspektor, Pages, Google Docs,
LibreOffice und Konverter. Die Liste der „möglichen Ursachen“, die `check` beim Zustand
„Verloren“ ausgibt, ist eine Annahme und noch nicht belegt. Wer einen dieser Wege prüft, gern
einen Bericht in `compat/` ergänzen.

Die Testsuite (`python3 -m pytest`) läuft ohne Office und deckt Paketstruktur, alle Zustände,
Merge, Adapter und CLI ab.

## Drei Stellen, an denen OfficeMD vom Distiller abweicht

**Fingerprint.** Der Distiller hasht die rohen Dateibytes (`content_sha256`). Die ändern sich
bei jedem Speichern in Office und durch den eingebetteten Part selbst. OfficeMD rechnet deshalb
einen eigenen Fingerprint über Reihenfolge, Selektoren und Text der Segmente
(`omd-text-v1:…`). Für die Belegprüfung bindet OfficeMD in einer temporären Kopie genau die
Graph-Quelle, die für diese Datei steht, an die aktuelle Extraktion und ruft dann das
unveränderte `verify_evidence.py` auf. Besser wäre ein textbasierter Hash direkt im Distiller;
das ist als Änderung dort vorgesehen.

**Merge.** Ein neu kompilierter Graph trägt für dieselbe Quelle einen neuen Byte-Hash, und
`merge_knowledge.py` lehnt das als Widerspruch ab. `officemd update` setzt den Hash der
eingehenden Quelle deshalb auf den der Basis und meldet das im Ergebnis
(`aligned_source_digest`).

**XLSX und PPTX.** Der Distiller liest nur DOCX. OfficeMD bringt eigene Adapter mit, die die
Hilfsfunktionen des Distillers nutzen (pfadsichere Eingabe, ZIP-Grenzen, DTD-Sperre) und dasselbe
Segmentformat liefern. Excel-Zeilen bekommen einen `CsvSelector`, PowerPoint-Absätze einen
`FragmentSelector` wie bei Word plus `PageSelector` mit der Foliennummer. Eine Erweiterung der
Spec war dafür nicht nötig. Formeln werden nie ausgewertet, übernommen wird der gespeicherte Wert.

## Aufbau

```text
officemd                    Startskript ohne Installation
src/officemd/
  cli.py                    Kommandozeile
  ops.py                    check, embed, update, restore, render, strip
  ooxml.py                  Part, Registrierung, custom.xml, atomares Schreiben
  evidence.py               Belegprüfung über verify_evidence.py
  fingerprint.py            Text-Fingerprint
  rawmd.py                  Roh-Markdown
  distiller.py              Subprozess-Aufrufe der Distiller-Skripte
  adapters/ooxml_extract.py XLSX- und PPTX-Extraktion
  selftest.py               Pakettest und Office-Roundtrip per AppleScript
  fixtures.py               Minimale Office-Dateien für Tests
vendor/knowledge-distiller  Submodule, unverändert
tests/                      pytest
compat/                     Kompatibilitätsberichte
docs/index.html             Landingpage
app/                        SwiftUI-Oberfläche (macOS)
```

## Mac-App

Unter `app/` liegt eine schlanke SwiftUI-Oberfläche. Sie ruft nur `officemd check --json` und
die anderen Befehle auf und enthält selbst keine Paketlogik.

```bash
cd app && swift run
```

![OfficeMD-App mit einer verlorenen Excel-Datei](docs/app.png)

Ordner oder Dateien ins Fenster ziehen oder beim Start übergeben
(`swift run OfficeMDApp ~/Dokumente`), dann zeigt die Liste den Zustand jeder Datei. Je nach
Zustand gibt es Roh-Markdown einbetten, Wiederherstellen und Markdown anzeigen. Den Pfad zu
`officemd` findet die App selbst, solange sie aus dem Repository gestartet wird, sonst in den
Einstellungen setzen.

Für Tests fotografiert die App ihr eigenes Fenster, ohne Berechtigung zur Bildschirmaufnahme:
`swift run OfficeMDApp ORDNER --select datei.docx --snapshot bild.png`. So ist das Bild oben
entstanden. Die Darstellung aller Zustände ist damit geprüft; die Aktionen rufen nur die
getesteten CLI-Befehle auf. Ein signiertes App-Bundle mit eingebettetem Python gibt es noch
nicht.

## Lizenz

MIT
