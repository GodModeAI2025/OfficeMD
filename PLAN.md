# Carrymark: Stand und Entscheidungen

Stand 2026-10-07. Bedienung steht in der [README](README.md).

## Kurs

Carrymark (vorher OfficeMD, Arbeitstitel ZugPferd) bettet Markdown in DOCX, XLSX und PPTX ein und
hält es aktuell. Die Umwandlung übernimmt [microsoft/markitdown](https://github.com/microsoft/markitdown).
Keine KI, kein Netz.

Die erste Fassung kompilierte mit dem Knowledge Distiller und optional mit Anthropic oder OpenAI
einen belegten Wissensgraphen. Auf Wunsch wurde das am 2026-10-07 durch markitdown ersetzt. Die
Graph-Fassung liegt in der Git-Historie (bis Commit `d2b16ee`). Aus ihr stammt ein Beitrag zum
Distiller: `normalized_sha256` und `verify_evidence.py --bind`
([knowledge-distiller#13](https://github.com/GodModeAI2025/knowledge-distiller/pull/13)).

## Entscheidungen

| Frage | Entscheidung |
|---|---|
| Woher kommt das Markdown? | markitdown, lokal, ohne Plugins |
| Welche Formate? | Nur DOCX, XLSX, PPTX: nur sie haben einen Platz für eingebettete Daten. Die App nimmt andere Dateien gar nicht erst an. |
| Fingerprint | SHA-256 über das normalisierte Markdown ohne Frontmatter (`cm-md-v1:`). Neues Speichern ohne inhaltliche Änderung ändert ihn nicht. |
| Frontmatter | Open Knowledge Format v0.2: `type`, `title`, `resource`, `tags`, `sources`, `generated`, eigene Felder unter `carrymark:` |
| Export | `.md` neben der Datei oder OKF-Bundle mit `index.md` |
| Einbettung | Registrierter Custom-XML-Datastore mit itemProps und GUID, dazu Fingerprint in `docProps/custom.xml` |
| Sicherung | Ordner `Datei.docx.carrymark/` mit `markdown.md` und `embed.json` |
| Alte Dateien | Namespace und Properties von OfficeMD werden weiter erkannt und beim nächsten Einbetten umgestellt |

## Ergebnisse

- Office-Roundtrip am 2026-10-07 mit Word 16.113.4, Excel 16.113.3 und PowerPoint 16.113.4
  bestanden: Part, GUID, Fingerprint-Eintrag und Markdown bleiben beim Speichern erhalten
  (`compat/`).
- Signiertes App-Bundle mit eingebettetem Python und markitdown (`scripts/build-app.sh`).
- CI auf GitHub Actions: Tests und Selbsttest auf Linux und macOS, Build der Mac-App.

## Offen

- Office für Windows und im Browser, Dokumentinspektor, Pages, Google Docs, LibreOffice testen.
- App notarisieren (`NOTARY_PROFILE=… scripts/build-app.sh`).
- Bundle verkleinern: onnxruntime kommt über markitdowns Dateityperkennung; prüfen, ob sich das
  bei bekannten Office-Typen umgehen lässt.
- Optional einen für KIs lesbaren Hinweis in die Dokumenteigenschaften schreiben, wo das Markdown liegt.
- Name: Marken- und Domainprüfung für „Carrymark“; GitHub-Repo ggf. umbenennen.
