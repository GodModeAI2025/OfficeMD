import SwiftUI

struct SettingsView: View {
    @AppStorage("settingsTab") private var tab = "general"

    var body: some View {
        TabView(selection: $tab) {
            GeneralSettingsView()
                .tabItem { Label("Allgemein", systemImage: "gearshape") }
                .tag("general")
            AIUsageView()
                .tabItem { Label("Mit KI nutzen", systemImage: "sparkles") }
                .tag("ai")
            AboutView()
                .tabItem { Label("Über", systemImage: "info.circle") }
                .tag("about")
        }
        .frame(width: 640, height: 640)
    }
}

struct GeneralSettingsView: View {
    @AppStorage("cliPath") private var cliPath = ""
    @State private var version: String?

    var body: some View {
        Form {
            Section {
                LabeledContent("Programm") {
                    Text(cliPath.isEmpty ? CLI.guessExecutable() : cliPath)
                        .font(.callout.monospaced())
                        .lineLimit(1)
                        .truncationMode(.middle)
                        .textSelection(.enabled)
                }
                if let version { LabeledContent("Version", value: version) }
                TextField("Eigener Pfad", text: $cliPath, prompt: Text("automatisch"))
            } header: {
                Text("Kommandozeile")
            } footer: {
                Text("Die App nutzt das mitgelieferte carrymark aus dem App-Bundle. Nur für Entwicklung einen eigenen Pfad eintragen.")
                    .foregroundStyle(.secondary)
            }
            Section {
                Label("Alles läuft lokal. Carrymark sendet keine Dokumente ins Netz und nutzt keine KI.",
                      systemImage: "lock.shield")
            } header: {
                Text("Datenschutz")
            }
        }
        .formStyle(.grouped)
        .task {
            version = try? await CLI.current().action(["--version"]).trimmingCharacters(in: .whitespacesAndNewlines)
        }
    }
}

struct AboutView: View {
    var body: some View {
        VStack(spacing: 14) {
            Image(nsImage: NSApplication.shared.applicationIconImage)
                .resizable()
                .frame(width: 72, height: 72)
            Text("Carrymark").font(.title2.weight(.semibold))
            Text("Markdown, das in der Datei bleibt.")
                .foregroundStyle(.secondary)
            Divider().padding(.horizontal, 60)
            VStack(alignment: .leading, spacing: 10) {
                credit("Markdown-Umwandlung", "microsoft/markitdown",
                       "https://github.com/microsoft/markitdown", "MIT-Lizenz, © Microsoft Corporation")
                credit("Frontmatter und Bundles", "Open Knowledge Format v0.2",
                       "https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md",
                       "Google Cloud Platform")
                credit("Quellcode", "GodModeAI2025/OfficeMD",
                       "https://github.com/GodModeAI2025/OfficeMD", "MIT-Lizenz")
            }
        }
        .padding(28)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func credit(_ title: String, _ name: String, _ url: String, _ note: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text(title).foregroundStyle(.secondary).frame(width: 170, alignment: .trailing)
            VStack(alignment: .leading, spacing: 1) {
                Link(name, destination: URL(string: url)!)
                Text(note).font(.caption).foregroundStyle(.tertiary)
            }
        }
        .font(.callout)
    }
}

/// Hinweise, wie man Dateien mit eingebettetem Markdown an eine KI gibt.
struct AIUsageView: View {
    static let officePrompt = """
    Diese Datei enthält ein von Carrymark eingebettetes Markdown-Abbild ihres Inhalts. \
    Eine DOCX-, XLSX- oder PPTX-Datei ist ein ZIP-Archiv. Öffne es und suche im Ordner customXml/ \
    die Datei itemN.xml mit dem Element <cm:knowledge> (Namespace urn:carrymark:knowledge:1). \
    Das Markdown steht im Element <cm:markdown>. Arbeite mit diesem Markdown statt mit selbst \
    extrahiertem Text. Die Frontmatter oben nennt Titel, Quelle und Stand; Tabellen sind als \
    Markdown-Tabellen enthalten, je Excel-Blatt eine.
    """

    static let pdfPrompt = """
    Diese PDF trägt einen Anhang namens carrymark.md: ein von Carrymark eingebettetes \
    Markdown-Abbild ihres Inhalts. Lies diesen Anhang aus (zum Beispiel in Python mit pypdf: \
    PdfReader(datei).attachments["carrymark.md"][0]) und arbeite mit diesem Markdown statt mit \
    selbst extrahiertem Text. Die Frontmatter oben nennt Titel, Quelle und Stand.
    """

    static let codeSnippet = """
    import re, zipfile
    with zipfile.ZipFile("Datei.docx") as z:
        for name in z.namelist():
            if re.fullmatch(r"customXml/item\\d+\\.xml", name):
                m = re.search(r"<cm:markdown><!\\[CDATA\\[(.*)\\]\\]></cm:markdown>", z.read(name).decode(), re.S)
                if m:
                    print(m.group(1))
    """

    var body: some View {
        Form {
            Section {
                Label {
                    Text("Chat-Oberflächen wie ChatGPT, Claude oder Copilot lesen beim Hochladen in der Regel nur den sichtbaren Text eines Dokuments. Das eingebettete Markdown in Office-Dateien (customXml) und der PDF-Anhang werden dabei normalerweise nicht beachtet.")
                } icon: {
                    Image(systemName: "eye.slash")
                }
                Label {
                    Text("Hat die KI Code-Ausführung (etwa Datenanalyse in ChatGPT oder Claude), kann sie das Markdown selbst auslesen, wenn du sagst, wo es liegt. Dafür sind die Prompts unten.")
                } icon: {
                    Image(systemName: "terminal")
                }
            } header: {
                Text("Was eine KI von selbst findet")
            }

            Section {
                Label {
                    Text("Am zuverlässigsten: das Markdown direkt mitgeben. In der Detailansicht „Als .md sichern“ wählen oder in der Symbolleiste ein OKF-Bundle exportieren und diese Dateien hochladen.")
                } icon: {
                    Image(systemName: "square.and.arrow.up")
                }
            } header: {
                Text("Einfachster Weg")
            }

            promptSection("Prompt für Word, Excel und PowerPoint", Self.officePrompt)
            promptSection("Prompt für PDF", Self.pdfPrompt)
            promptSection("Für Agenten mit Python", Self.codeSnippet, monospaced: true)

            Section {
                Text("Tipp: Die Frontmatter enthält unter carrymark: den Fingerprint. Zeigt Carrymark die Datei als „Aktuell“, entspricht das Markdown genau dem heutigen Inhalt; bei „Veraltet“ erst aktualisieren, dann weitergeben.")
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
    }

    private func promptSection(_ title: String, _ text: String, monospaced: Bool = false) -> some View {
        Section {
            Text(text)
                .font(monospaced ? .callout.monospaced() : .callout)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
            HStack {
                Spacer()
                CopyButton(text: text)
            }
        } header: {
            Text(title)
        }
    }
}

struct CopyButton: View {
    let text: String
    @State private var copied = false

    var body: some View {
        Button {
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(text, forType: .string)
            withAnimation(.snappy) { copied = true }
            Task {
                try? await Task.sleep(for: .seconds(1.5))
                withAnimation(.snappy) { copied = false }
            }
        } label: {
            Label(copied ? "Kopiert" : "Kopieren", systemImage: copied ? "checkmark" : "doc.on.doc")
        }
    }
}
