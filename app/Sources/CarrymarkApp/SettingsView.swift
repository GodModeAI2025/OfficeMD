import SwiftUI

struct SettingsView: View {
    var body: some View {
        TabView {
            GeneralSettingsView()
                .tabItem { Label("Allgemein", systemImage: "gearshape") }
            AboutView()
                .tabItem { Label("Über", systemImage: "info.circle") }
        }
        .frame(width: 560, height: 420)
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
