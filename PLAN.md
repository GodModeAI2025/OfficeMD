# OfficeMD: Plan und Stand

Erster Entwurf vom 2026-10-06 (damals noch unter dem Arbeitstitel ZugPferd), am selben Tag
umgesetzt. Dieses Dokument hält fest, was entschieden wurde, warum, und was offen ist. Die
Bedienung steht in der [README](README.md).

## 1. Was der Distiller-Code am ursprünglichen Konzept korrigiert hat

**Der Hash ist ein Byte-Hash, kein Text-Hash.** `extract_source.py` setzt `content_sha256` und
die Source-ID aus den rohen Dateibytes. `verify_evidence.py` ordnet Graph und Extraktion nur
darüber zu. Jedes Speichern in Office und schon das Einbetten selbst ändern diese Bytes.
Umgesetzt: eigener Text-Fingerprint `omd-text-v1` über Selektoren und `text_sha256` der
Segmente.

**Kanonisch ist `.knowledge.json`, nicht `.knowledge.md`.** Eingebettet wird der Graph, das
Markdown kommt aus `build_md.py` und liegt als zweites Element mit im Part. Zusätzliche
Frontmatter-Felder wie `source_sha256` waren unnötig, SPEC §4.2 hat `content_sha256` und `type`.

**PPTX brauchte keinen neuen Selektor.** Ursprünglich war ein `SlideSelector` angedacht.
Umgesetzt ist `FragmentSelector` `ppt/slides/slideN.xml#paragraph=K` wie beim DOCX-Adapter, dazu
`PageSelector` mit der Foliennummer. `verify_evidence.py` löst Fragment-Selektoren ohne Änderung
auf. Keine Spec-Erweiterung nötig.

Der DOCX-Leser des Distillers liest nur Text-Parts, ein zusätzlicher `customXml`-Part stört die
Extraktion nicht. Durch Tests belegt: Der Fingerprint bleibt beim Einbetten gleich.

## 2. Entscheidungen

| Frage | Entscheidung |
|---|---|
| Belegprüfung trotz Byte-Hash | Erst Weg (b), Umbindung in temporären Kopien. Seit 2026-10-07 Weg (a): Der Distiller liefert `normalized_sha256` und `verify_evidence.py --bind` (knowledge-distiller#13), OfficeMD nutzt beides. |
| Merge nach Neukompilierung | `merge_knowledge.py` lehnt abweichende `content_sha256` derselben Quelle ab. `update` setzt den Wert der eingehenden Quelle auf den der Basis und meldet das (`aligned_source_digest`). |
| Distiller einbinden | Git-Submodule `vendor/knowledge-distiller`, Aufruf per Subprozess mit `python -E -s` |
| XLSX/PPTX | Eigene Adapter mit den Sicherheitsfunktionen aus `extract_source.py` |
| Ablage von Archiv und Diffs | Sidecar `Datei.docx.knowledge/` mit `knowledge.json`, `knowledge.md`, `embed.json`, `versions/`, Diffs |
| Markdown zusätzlich einbetten | Ja, als `<omd:markdown>` neben `<omd:graph>` |
| Name | OfficeMD, Repo `GodModeAI2025/OfficeMD`, Paket und CLI `officemd` |

## 3. Phasen

| Phase | Inhalt | Stand |
|---|---|---|
| 1 | DOCX, Roh-Markdown, registrierter Part, `check`/`embed`/`restore`/`strip` | erledigt |
| 2 | Wissensgraph: `build_graph`, `validate`, `verify`, `update` (Merge), `render` | erledigt |
| 3 | XLSX- und PPTX-Adapter | erledigt |
| 4 | Office-Roundtrip per AppleScript, Kompatibilitätsbericht | erledigt für macOS |
| 5 | SwiftUI-Oberfläche über dem CLI | erledigt, Darstellung aller Zustände per `--snapshot` geprüft |
| 6 | KI-Kompilierung mit Anthropic/OpenAI, `sync`, Einstellungen in der App | erledigt, mit Fake-Anbieter getestet; Live-Lauf mit gültigem Key offen |

## 4. Ergebnis des Office-Roundtrips

`officemd selftest --office` auf macOS 27.2, Bericht in `compat/`:

| App | Part überlebt | GUID gleich | Fingerprint-Eintrag | Belege nach dem Speichern |
|---|---|---|---|---|
| Word 16.113.4 | ja | ja | ja | 1 von 1 gefunden |
| Excel 16.113.3 | ja | ja | ja | 1 von 1 gefunden |
| PowerPoint 16.113.3 | ja | ja | ja | 1 von 1 gefunden |

Zwei Fallstricke beim AppleScript, beide behoben: Nach „Speichern unter“ ist die alte
Dokumentreferenz ungültig, geschlossen wird deshalb über den Dateinamen. Excel braucht beim
ersten Start länger als das Standard-Timeout von AppleScript.

## 5. KI-Kompilierung

Entscheidungen:

- Das Modell liefert nur Inhalt in einem kompakten, per JSON-Schema erzwungenen Format. Den
  Spec-Graphen baut OfficeMD selbst. So hängen Format, IDs, Quelle und Herkunftsfelder nie am
  Modell, und das Schema bleibt mit den strikten Modi beider Anbieter verträglich.
- Prompt aus `SKILL.md` („Nicht tun“, Phase 2 bis vor 2.7) und `profiles/default.json`, zur
  Laufzeit aus dem Submodule gelesen. Fakten, Chunks, räumliche Angaben und Inferenz bleiben
  vorerst draußen, wie es das Standardprofil ohnehin vorsieht.
- Korrekturschleife: build_graph, validate, verify; Fehler zurück ans Modell, höchstens zwei
  Runden, danach Abbruch ohne Einbetten.
- Offizielle SDKs (`anthropic`, `openai`) als optionales Extra; der Rest bleibt
  Standardbibliothek. `./officemd setup-ai` legt `.venv` an, das Startskript nutzt sie.
- Keys: Umgebungsvariable, sonst macOS-Schlüsselbund, sonst Datei mit 0600. Übergabe nur per
  stdin.

**Veraltete Graphen werden neu kompiliert, nicht per Delta gemergt.** Ein Delta-Merge, der
wieder „Aktuell“ erreicht, scheitert heute an fünf Stellen:

1. Belege, die nicht mehr im Text stehen, bleiben im additiven Merge erhalten; sie müssten
   vorher als `rejected` markiert und in der Zählung ausgenommen werden.
2. Der eingehende Graph muss für sich allein validieren und braucht daher die referenzierten
   Bestandsknoten, und zwar byte-genau, sonst lehnt der Merge die abweichende Nutzlast ab.
3. ID-Kollisionen bei Belegen, Claims und Fakten müssten umbenannt und alle Verweise
   nachgezogen werden.
4. Kanten mit gleichem Tripel, aber anderem Gewicht oder anderer Konfidenz sind ein Konflikt.
5. Cluster mit gleicher ID und anderem Label ebenso.

Der Neuaufbau archiviert die alte Fassung in `versions/` und übernimmt menschliche Ergänzungen,
deren Zitat noch im Text steht. Der Delta-Merge ist der nächste Schritt, sobald echte
Modellausgaben zum Testen vorliegen.

## 6. Offen

- Office für Windows, Office im Browser, Dokumentinspektor, Pages, Google Docs, LibreOffice
  testen. Die Liste der möglichen Ursachen bei „Verloren“ ist bis dahin eine Annahme.
- SwiftUI-App: Bundle mit eingebettetem Python und Distiller bauen, Signierung.
- KI-Kompilierung mit gültigem Key gegen beide Anbieter laufen lassen; Prompt und Schema an
  echten Ausgaben nachschärfen.
- Delta-Merge für veraltete Graphen (siehe Abschnitt 5).
- Fakten (`facts[]`) und Chunks im Ausgabeschema ergänzen.
- Sehr große Dokumente in Abschnitten kompilieren statt abzulehnen.
