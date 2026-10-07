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

    var cli: CLI {
        let stored = UserDefaults.standard.string(forKey: "cliPath") ?? ""
        return CLI(executable: stored.isEmpty ? CLI.guessExecutable() : stored)
    }

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
                try? await Task.sleep(for: .seconds(1.5))
                Snapshot.write(to: path)
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
                if report.mode == "graph", report.state == "stale" {
                    Text("Graph aktualisieren: neuen Graphen kompilieren lassen und mit `officemd update` einspielen.")
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
    @AppStorage("cliPath") private var cliPath = ""

    var body: some View {
        Form {
            TextField("Pfad zu officemd", text: $cliPath, prompt: Text(CLI.guessExecutable()))
            Text("Leer lassen, um officemd automatisch neben der App oder im PATH zu suchen.")
                .font(.caption).foregroundStyle(.secondary)
        }
        .padding()
        .frame(width: 520)
    }
}

/// Startargumente: Pfade, dazu für Tests `--select Dateiname` und `--snapshot bild.png`.
struct LaunchOptions {
    var paths: [String] = []
    var select: String?
    var snapshot: String?

    static func parse(_ arguments: [String]) -> LaunchOptions {
        var options = LaunchOptions()
        var iterator = arguments.dropFirst().makeIterator()
        while let arg = iterator.next() {
            switch arg {
            case "--select": options.select = iterator.next()
            case "--snapshot": options.snapshot = iterator.next()
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
    static func write(to path: String) {
        guard let view = NSApplication.shared.windows.first(where: { $0.isVisible })?.contentView,
              let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return }
        view.cacheDisplay(in: view.bounds, to: rep)
        try? rep.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: path))
    }
}
