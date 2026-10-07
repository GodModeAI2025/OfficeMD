import AppKit
import SwiftUI
import UniformTypeIdentifiers

@main
struct OfficeMDApp: App {
    init() {
        // Als SwiftPM-Binary gestartet, braucht die App eine reguläre Aktivierung.
        NSApplication.shared.setActivationPolicy(.regular)
    }

    var body: some Scene {
        WindowGroup("OfficeMD") {
            ContentView()
                .frame(minWidth: 820, minHeight: 480)
        }
        Settings {
            SettingsView()
        }
    }
}

@MainActor
final class Store: ObservableObject {
    @Published var roots: [URL] = []
    @Published var reports: [FileReport] = []
    @Published var selection: FileReport.ID?
    @Published var busy = false
    @Published var error: String?
    @Published var markdown: String?

    @Published var syncStatus: String?
    @Published var syncResults: [SyncResult]?

    var cli: CLI { CLI.current() }

    var selected: FileReport? { reports.first { $0.id == selection } }

    init() {
        // Pfade aus der Kommandozeile übernehmen: swift run OfficeMDApp ~/Dokumente
        let options = LaunchOptions.parse(CommandLine.arguments)
        roots = options.paths.map { URL(fileURLWithPath: ($0 as NSString).expandingTildeInPath) }
        guard !roots.isEmpty else { return }
        Task {
            await refresh()
            if let name = options.select {
                selection = reports.first { $0.fileName == name }?.id ?? selection
            }
            if let path = options.snapshot {
                try? await Task.sleep(for: .seconds(2.5))
                Snapshot.write(to: path, settings: options.settings)
                NSApplication.shared.terminate(nil)
            }
        }
    }

    func add(_ urls: [URL]) {
        for url in urls where !roots.contains(url) { roots.append(url) }
        Task { await refresh() }
    }

    func refresh() async {
        guard !roots.isEmpty else { reports = []; return }
        busy = true
        defer { busy = false }
        do {
            reports = try await cli.check(roots.map(\.path))
            if selection == nil || !reports.contains(where: { $0.id == selection }) {
                selection = reports.first?.id
            }
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    func perform(_ arguments: [String]) async {
        busy = true
        do {
            _ = try await cli.action(arguments)
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
        busy = false
        await refresh()
    }

    /// Prüfen und bei Bedarf per KI aktualisieren. Dauert mit KI gern mehrere Minuten.
    func sync(_ paths: [String], label: String) async {
        busy = true
        syncStatus = "\(label): prüfen und aktualisieren … (mit KI kann das einige Minuten dauern)"
        do {
            syncResults = try await cli.sync(paths)
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
        syncStatus = nil
        busy = false
        await refresh()
    }

    func showMarkdown(for report: FileReport) async {
        do {
            markdown = try await cli.action(["render", report.file])
        } catch {
            self.error = error.localizedDescription
        }
    }
}

struct ContentView: View {
    @StateObject private var store = Store()
    @State private var importing = false
    @Environment(\.openSettings) private var openSettings

    var body: some View {
        NavigationSplitView {
            List(store.reports, selection: $store.selection) { report in
                HStack(spacing: 10) {
                    StatePill(state: report.state, label: report.label)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(report.fileName).lineLimit(1)
                        if let ev = report.evidence {
                            Text("\(ev.counts.verified) von \(ev.total) Belegen gefunden")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    if report.locked {
                        Image(systemName: "lock.fill").foregroundStyle(.secondary).help("In Office geöffnet")
                    }
                }
                .tag(report.id)
            }
            .overlay {
                if store.reports.isEmpty {
                    ContentUnavailableView("Keine Dateien",
                                           systemImage: "doc.badge.plus",
                                           description: Text("Ordner oder Office-Dateien hierher ziehen oder über + hinzufügen."))
                }
            }
            .navigationSplitViewColumnWidth(min: 280, ideal: 320)
        } detail: {
            if let report = store.selected {
                DetailView(report: report, store: store)
            } else {
                Text("Datei auswählen").foregroundStyle(.secondary)
            }
        }
        .toolbar {
            ToolbarItemGroup {
                if store.busy { ProgressView().controlSize(.small) }
                Button { importing = true } label: { Label("Hinzufügen", systemImage: "plus") }
                Button {
                    Task { await store.sync(store.roots.map(\.path), label: "Alle Dateien") }
                } label: {
                    Label("Alle prüfen & aktualisieren", systemImage: "wand.and.stars")
                }
                .help("Prüft alle Dateien und kompiliert bei Bedarf neu (KI laut Einstellungen)")
                .disabled(store.roots.isEmpty || store.busy)
                Button { Task { await store.refresh() } } label: {
                    Label("Neu prüfen", systemImage: "arrow.clockwise")
                }
                .disabled(store.roots.isEmpty)
            }
        }
        .fileImporter(isPresented: $importing,
                      allowedContentTypes: [.folder, UTType(filenameExtension: "docx")!,
                                            UTType(filenameExtension: "xlsx")!, UTType(filenameExtension: "pptx")!],
                      allowsMultipleSelection: true) { result in
            if case .success(let urls) = result { store.add(urls) }
        }
        .onAppear {
            if LaunchOptions.parse(CommandLine.arguments).settings { openSettings() }
        }
        .dropDestination(for: URL.self) { urls, _ in
            store.add(urls)
            return true
        }
        .alert("Fehler", isPresented: Binding(get: { store.error != nil }, set: { if !$0 { store.error = nil } })) {
            Button("OK") { store.error = nil }
        } message: {
            Text(store.error ?? "")
        }
        .sheet(isPresented: Binding(get: { store.markdown != nil }, set: { if !$0 { store.markdown = nil } })) {
            MarkdownSheet(text: store.markdown ?? "") { store.markdown = nil }
        }
        .sheet(isPresented: Binding(get: { store.syncResults != nil }, set: { if !$0 { store.syncResults = nil } })) {
            SyncResultSheet(results: store.syncResults ?? []) { store.syncResults = nil }
        }
        .safeAreaInset(edge: .bottom) {
            if let status = store.syncStatus {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text(status).font(.callout)
                    Spacer()
                }
                .padding(10)
                .background(.bar)
            }
        }
    }
}

struct StatePill: View {
    let state: String
    let label: String

    var color: Color {
        switch state {
        case "current": return .green
        case "stale": return .orange
        case "lost", "unreadable": return .red
        default: return .secondary
        }
    }

    var body: some View {
        Text(label)
            .font(.caption.weight(.semibold))
            .padding(.horizontal, 8).padding(.vertical, 3)
            .background(color.opacity(0.15), in: Capsule())
            .foregroundStyle(color)
            .frame(width: 110, alignment: .leading)
    }
}

struct DetailView: View {
    let report: FileReport
    @ObservedObject var store: Store

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                HStack {
                    Text(report.fileName).font(.title2.weight(.semibold))
                    StatePill(state: report.state, label: report.label)
                }
                Text(report.message)
                if let ev = report.evidence {
                    Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 6) {
                        GridRow { Text("Belege gefunden").foregroundStyle(.secondary); Text("\(ev.counts.verified) von \(ev.total)") }
                        GridRow { Text("Nicht auffindbar").foregroundStyle(.secondary); Text("\(ev.counts.notFound)") }
                        GridRow { Text("Nicht prüfbar").foregroundStyle(.secondary); Text("\(ev.counts.unverifiable)") }
                    }
                }
                if !report.warnings.isEmpty {
                    VStack(alignment: .leading, spacing: 4) {
                        ForEach(report.warnings, id: \.self) { Label($0, systemImage: "exclamationmark.triangle") }
                    }
                    .foregroundStyle(.orange)
                }
                if let causes = report.possibleCauses, !causes.isEmpty {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Mögliche Ursachen").font(.headline)
                        ForEach(causes, id: \.self) { Text("• " + $0) }
                    }
                }
                Divider()
                HStack {
                    if ["never", "stale", "lost"].contains(report.state) {
                        Button("Prüfen & aktualisieren") {
                            Task { await store.sync([report.file], label: report.fileName) }
                        }
                        .buttonStyle(.borderedProminent)
                        .help("Kompiliert bei Bedarf mit der KI aus den Einstellungen neu und bettet ein")
                    }
                    if report.state == "never" || report.state == "stale", report.mode != "graph" {
                        Button("Roh-Markdown einbetten") { Task { await store.perform(["embed", report.file]) } }
                    }
                    if report.state == "lost", report.sidecar?.restorable == true {
                        Button("Wiederherstellen") { Task { await store.perform(["restore", report.file]) } }
                            .buttonStyle(.borderedProminent)
                    }
                    if report.state == "current" || report.state == "stale" {
                        Button("Markdown anzeigen") { Task { await store.showMarkdown(for: report) } }
                    }
                    Button("Im Finder zeigen") {
                        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: report.file)])
                    }
                }
                .disabled(store.busy || report.locked)
                if report.state == "never" || (report.mode == "graph" && report.state == "stale") {
                    Text("„Prüfen & aktualisieren“ schickt den Dokumenttext an den KI-Anbieter aus den Einstellungen. Ohne KI: „Roh-Markdown einbetten“.")
                        .font(.callout).foregroundStyle(.secondary)
                }
            }
            .padding(24)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

struct MarkdownSheet: View {
    let text: String
    let close: () -> Void

    var body: some View {
        VStack(alignment: .leading) {
            ScrollView {
                Text(text).font(.system(.body, design: .monospaced)).textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            HStack {
                Spacer()
                Button("Kopieren") {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(text, forType: .string)
                }
                Button("Schließen", action: close).keyboardShortcut(.defaultAction)
            }
        }
        .padding()
        .frame(minWidth: 640, minHeight: 480)
    }
}

struct SettingsView: View {
    var body: some View {
        TabView {
            AISettingsView().tabItem { Label("KI", systemImage: "wand.and.stars") }
            GeneralSettingsView().tabItem { Label("Allgemein", systemImage: "gearshape") }
        }
        .frame(width: 620, height: 760)
    }
}

struct GeneralSettingsView: View {
    @AppStorage("cliPath") private var cliPath = ""

    var body: some View {
        Form {
            TextField("Pfad zu officemd", text: $cliPath, prompt: Text(CLI.guessExecutable()))
            Text("Leer lassen, um officemd automatisch neben der App oder im PATH zu suchen.")
                .font(.caption).foregroundStyle(.secondary)
        }
        .padding()
    }
}

@MainActor
final class AISettingsModel: ObservableObject {
    @Published var config: AIConfig?
    @Published var keys: [String: String] = ["anthropic": "", "openai": ""]
    @Published var models: [String: String] = [:]
    @Published var message: String?
    @Published var busy = false

    let cli = CLI.current()

    func load() async {
        do {
            config = try await cli.config()
            for (name, info) in config?.providers ?? [:] { models[name] = info.model }
        } catch {
            message = error.localizedDescription
        }
    }

    func set(_ key: String, _ value: String) async {
        await run(["config", "set", key, value])
    }

    func saveKey(_ provider: String) async {
        let key = keys[provider, default: ""].trimmingCharacters(in: .whitespacesAndNewlines)
        guard !key.isEmpty else { return }
        await run(["config", "set-key", provider], stdin: key + "\n")
        keys[provider] = ""
    }

    func test(_ provider: String) async {
        await run(["config", "test", provider], showOutput: true)
    }

    func run(_ arguments: [String], stdin: String? = nil, showOutput: Bool = false) async {
        busy = true
        do {
            let out = try await cli.action(arguments, stdin: stdin)
            message = showOutput || stdin != nil ? out.trimmingCharacters(in: .whitespacesAndNewlines) : nil
        } catch {
            message = error.localizedDescription
        }
        busy = false
        await load()
    }
}

struct AISettingsView: View {
    @StateObject private var model = AISettingsModel()
    private let efforts = ["low", "medium", "high", "xhigh", "max"]

    var body: some View {
        Form {
            if let config = model.config {
                Section {
                    Picker("Anbieter", selection: Binding(
                        get: { config.provider ?? "" },
                        set: { value in Task { await model.set("provider", value.isEmpty ? "none" : value) } })) {
                        Text("Keiner").tag("")
                        Text("Anthropic (Claude)").tag("anthropic")
                        Text("OpenAI").tag("openai")
                    }
                    Picker("Neue Dateien", selection: Binding(
                        get: { config.mode },
                        set: { value in Task { await model.set("mode", value) } })) {
                        Text("Wissensgraph (KI)").tag("graph")
                        Text("Roh-Markdown (ohne KI)").tag("raw")
                    }
                    Picker("Tiefe", selection: Binding(
                        get: { config.depth },
                        set: { value in Task { await model.set("depth", value) } })) {
                        Text("quick").tag("quick")
                        Text("standard").tag("standard")
                        Text("deep").tag("deep")
                    }
                } footer: {
                    Text("Beim Kompilieren geht der Dokumenttext an den gewählten Anbieter. Prüfen, Wiederherstellen und Roh-Markdown laufen lokal.")
                        .font(.caption).foregroundStyle(.secondary)
                }

                ForEach(["anthropic", "openai"], id: \.self) { name in
                    if let info = config.providers[name] {
                        providerSection(name, info)
                    }
                }
            } else {
                ProgressView()
            }
            if let message = model.message {
                Text(message).font(.callout).textSelection(.enabled)
            }
        }
        .formStyle(.grouped)
        .disabled(model.busy)
        .task { await model.load() }
    }

    @ViewBuilder
    private func providerSection(_ name: String, _ info: AIConfig.ProviderInfo) -> some View {
        Section(name == "anthropic" ? "Anthropic" : "OpenAI") {
            HStack {
                TextField("Modell", text: Binding(get: { model.models[name, default: info.model] },
                                                  set: { model.models[name] = $0 }))
                    .onSubmit { Task { await model.set("\(name).model", model.models[name, default: info.model]) } }
                Button("Übernehmen") {
                    Task { await model.set("\(name).model", model.models[name, default: info.model]) }
                }
            }
            Picker("Effort", selection: Binding(
                get: { info.effort },
                set: { value in Task { await model.set("\(name).effort", value) } })) {
                ForEach(efforts, id: \.self) { Text($0).tag($0) }
            }
            if let fallbacks = info.fallbacks {
                Toggle("Bei Ablehnung auf anderes Claude-Modell ausweichen", isOn: Binding(
                    get: { fallbacks },
                    set: { value in Task { await model.set("\(name).fallbacks", value ? "true" : "false") } }))
            }
            LabeledContent("API-Key") {
                Text(keyLabel(info.keySource)).foregroundStyle(info.keySource == "none" ? .orange : .secondary)
            }
            HStack {
                SecureField("Neuen API-Key einfügen", text: Binding(get: { model.keys[name, default: ""] },
                                                                    set: { model.keys[name] = $0 }))
                Button("Speichern") { Task { await model.saveKey(name) } }
                    .disabled(model.keys[name, default: ""].isEmpty)
            }
            HStack {
                Button("Verbindung testen") { Task { await model.test(name) } }
                    .disabled(info.keySource == "none" || !info.sdkAvailable)
                if info.keySource == "keychain" {
                    Button("Key löschen", role: .destructive) { Task { await model.run(["config", "delete-key", name]) } }
                }
            }
            if !info.sdkAvailable {
                Text("Python-SDK fehlt. Im Terminal einmalig ausführen: ./officemd setup-ai")
                    .font(.caption).foregroundStyle(.orange)
            }
        }
    }

    private func keyLabel(_ source: String) -> String {
        switch source {
        case "keychain": return "im Schlüsselbund gespeichert"
        case "env": return "aus Umgebungsvariable"
        case "file": return "in credentials.json"
        default: return "nicht hinterlegt"
        }
    }
}

struct SyncResultSheet: View {
    let results: [SyncResult]
    let close: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Ergebnis").font(.title3.weight(.semibold))
            List(results) { result in
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        if let after = result.after {
                            StatePill(state: after, label: label(after))
                        }
                        Text(result.fileName).font(.headline)
                    }
                    Text(result.message).font(.callout)
                    ForEach(result.problems ?? [], id: \.self) { Text("• " + $0).font(.caption).foregroundStyle(.secondary) }
                }
                .padding(.vertical, 4)
            }
            HStack { Spacer(); Button("Schließen", action: close).keyboardShortcut(.defaultAction) }
        }
        .padding()
        .frame(minWidth: 620, minHeight: 360)
    }

    private func label(_ state: String) -> String {
        ["current": "Aktuell", "stale": "Veraltet", "lost": "Verloren", "never": "Nie vorhanden",
         "unreadable": "Nicht lesbar"][state] ?? state
    }
}

/// Startargumente: Pfade, dazu für Tests `--select Dateiname` und `--snapshot bild.png`.
struct LaunchOptions {
    var paths: [String] = []
    var select: String?
    var snapshot: String?
    var settings = false

    static func parse(_ arguments: [String]) -> LaunchOptions {
        var options = LaunchOptions()
        var iterator = arguments.dropFirst().makeIterator()
        while let arg = iterator.next() {
            switch arg {
            case "--select": options.select = iterator.next()
            case "--snapshot": options.snapshot = iterator.next()
            case "--settings": options.settings = true
            default:
                // Xcode und LaunchServices hängen eigene Schalter an (-NSDocumentRevisionsDebugMode …).
                if !arg.hasPrefix("-") { options.paths.append(arg) }
            }
        }
        return options
    }
}

/// Fotografiert das eigene Fenster. Braucht keine Berechtigung zur Bildschirmaufnahme.
@MainActor
enum Snapshot {
    static func write(to path: String, settings: Bool = false) {
        let visible = NSApplication.shared.windows.filter(\.isVisible)
        let settingsWindow = visible.first { ($0.identifier?.rawValue ?? "").localizedCaseInsensitiveContains("settings") }
        let window = (settings ? settingsWindow : nil) ?? visible.first { $0 !== settingsWindow }
        guard let view = window?.contentView,
              let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return }
        view.cacheDisplay(in: view.bounds, to: rep)
        try? rep.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: path))
    }
}
