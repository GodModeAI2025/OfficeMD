import AppKit
import SwiftUI

@main
struct CarrymarkApp: App {
    init() {
        // Als SwiftPM-Binary gestartet, braucht die App eine reguläre Aktivierung.
        NSApplication.shared.setActivationPolicy(.regular)
        if LaunchOptions.current.dark {
            NSApplication.shared.appearance = NSAppearance(named: .darkAqua)
        }
    }

    var body: some Scene {
        WindowGroup("Carrymark") {
            ContentView()
                .frame(minWidth: 900, minHeight: 560)
        }
        .windowToolbarStyle(.unified)
        .commands {
            CommandGroup(after: .newItem) {
                Button("Ordner hinzufügen …") {
                    NotificationCenter.default.post(name: .carrymarkImport, object: nil)
                }
                .keyboardShortcut("o")
            }
        }
        Settings {
            SettingsView()
        }
    }
}

extension Notification.Name {
    static let carrymarkImport = Notification.Name("carrymarkImport")
}

@MainActor
final class Store: ObservableObject {
    /// Aufgenommene Ordner und Dateien; bleiben über Neustarts erhalten.
    @Published var roots: [URL] = [] {
        didSet {
            if !LaunchOptions.current.isScripted {
                UserDefaults.standard.set(roots.map(\.path), forKey: "roots")
            }
        }
    }
    @Published var reports: [FileReport] = []
    @Published var selection: FileReport.ID?
    @Published var busy = false
    @Published var error: String?
    @Published var markdown: MarkdownDocument?
    @Published var syncStatus: String?
    @Published var syncResults: [SyncResult]?
    @Published var notice: String?
    @Published var skipped: [FileReport] = []

    var cli: CLI { CLI.current() }
    var selected: FileReport? { reports.first { $0.id == selection } }

    init() {
        // Pfade aus der Kommandozeile übernehmen: Carrymark ~/Dokumente
        let options = LaunchOptions.current
        let saved = options.paths.isEmpty && !options.isScripted
            ? UserDefaults.standard.stringArray(forKey: "roots") ?? [] : options.paths
        roots = saved.map { URL(fileURLWithPath: ($0 as NSString).expandingTildeInPath) }
            .filter { FileManager.default.fileExists(atPath: $0.path) && Self.isSupported($0) }
        guard !roots.isEmpty || options.snapshot != nil else { return }
        Task {
            await refresh()
            if let name = options.select {
                selection = reports.first { $0.fileName == name }?.id ?? selection
            }
            if options.markdown, let report = selected {
                await showMarkdown(for: report)
            }
            if let path = options.snapshot {
                try? await Task.sleep(for: .seconds(2.5))
                Snapshot.write(to: path, settings: options.settings, sheet: options.markdown)
                NSApplication.shared.terminate(nil)
            }
        }
    }

    /// Dateitypen, die Carrymark verarbeiten kann. Alles andere wird nicht aufgenommen.
    static let supportedExtensions: Set<String> = ["docx", "xlsx", "pptx", "pdf"]

    static func isSupported(_ url: URL) -> Bool {
        isDirectory(url.path) || supportedExtensions.contains(url.pathExtension.lowercased())
    }

    func add(_ urls: [URL]) {
        let accepted = urls.filter(Self.isSupported)
        let ignored = urls.count - accepted.count
        for url in accepted where !roots.contains(url) { roots.append(url) }
        if ignored > 0 {
            show(ignored == 1 ? "1 Datei ignoriert: nur Word, Excel, PowerPoint und PDF"
                              : "\(ignored) Dateien ignoriert: nur Word, Excel, PowerPoint und PDF")
        }
        guard !accepted.isEmpty else { return }
        Task { await refresh() }
    }

    func remove(_ root: URL) {
        roots.removeAll { $0 == root }
        reports.removeAll { $0.file == root.path }
    }

    /// Prüft alle Wurzeln oder nur die genannten Dateien und führt das Ergebnis zusammen.
    func refresh(only files: [String]? = nil) async {
        guard !roots.isEmpty else { reports = []; return }
        busy = true
        defer { busy = false }
        do {
            let fresh = try await cli.check(files ?? roots.map(\.path))
            var merged = fresh
            if let files {
                let touched = Set(files)
                merged = (reports + skipped).filter { !touched.contains($0.file) } + fresh
            }
            // Jede Datei nur einmal, auch wenn sie einzeln und über ihren Ordner aufgenommen wurde.
            var seen = Set<String>()
            merged = merged.reversed().filter { seen.insert(($0.file as NSString).standardizingPath).inserted }.reversed()
            // Nur bewerten, was Carrymark verarbeiten kann; der Rest wird als übersprungen gemeldet.
            withAnimation(.snappy) {
                reports = merged.filter { $0.status != .unreadable }
                skipped = merged.filter { $0.status == .unreadable }
            }
            if selection == nil || !reports.contains(where: { $0.id == selection }) {
                selection = reports.sorted(by: Self.order).first?.id
            }
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    static func order(_ a: FileReport, _ b: FileReport) -> Bool {
        (a.status.group, a.fileName.localizedLowercase) < (b.status.group, b.fileName.localizedLowercase)
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

    /// Prüfen und bei Bedarf neu einbetten oder wiederherstellen.
    func sync(_ paths: [String], label: String) async {
        busy = true
        withAnimation(.snappy) { syncStatus = label }
        do {
            syncResults = try await cli.sync(paths)
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
        withAnimation(.snappy) { syncStatus = nil }
        busy = false
        // Einzelne Datei: nur sie neu prüfen statt den ganzen Ordner.
        await refresh(only: paths.count == 1 && !Self.isDirectory(paths[0]) ? paths : nil)
    }

    static func isDirectory(_ path: String) -> Bool {
        var isDirectory: ObjCBool = false
        return FileManager.default.fileExists(atPath: path, isDirectory: &isDirectory) && isDirectory.boolValue
    }

    /// Zwischenspeicher: das Markdown je Prüfstand einer Datei, damit Vorschau und Sheet
    /// nicht jeweils einen eigenen CLI-Prozess starten.
    private var markdownCache: [FileReport: String] = [:]

    /// Eingebettet: genau das, was in der Datei steckt. Sonst frisch umgewandelt.
    func markdownText(for report: FileReport) async throws -> String {
        if let cached = markdownCache[report] { return cached }
        let text: String
        if let backup = report.backupMarkdownPath, report.status == .lost {
            text = try String(contentsOfFile: backup, encoding: .utf8)
        } else {
            text = try await cli.action([report.embedded ? "render" : "convert", report.file])
        }
        markdownCache[report] = text
        return text
    }

    func exportMarkdown(for report: FileReport) async {
        do {
            _ = try await cli.action(["export", report.file])
            let target = URL(fileURLWithPath: report.file + ".md")
            show("\(target.lastPathComponent) gesichert")
            reveal(target.path)
        } catch {
            self.error = error.localizedDescription
        }
    }

    /// Alle Dateien als Bundle im Open Knowledge Format in einen gewählten Ordner.
    func exportBundle() async {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.prompt = "Exportieren"
        panel.message = "Zielordner für das OKF-Bundle wählen"
        guard panel.runModal() == .OK, let folder = panel.url else { return }
        busy = true
        do {
            _ = try await cli.action(["export", "--okf", "-o", folder.path] + roots.map(\.path))
            show("OKF-Bundle exportiert")
            reveal(folder.appendingPathComponent("index.md").path)
        } catch {
            self.error = error.localizedDescription
        }
        busy = false
    }

    func show(_ message: String) {
        withAnimation(.snappy) { notice = message }
        Task {
            try? await Task.sleep(for: .seconds(2.5))
            withAnimation(.snappy) { if notice == message { notice = nil } }
        }
    }

    func showMarkdown(for report: FileReport) async {
        do {
            let text = try await markdownText(for: report)
            markdown = MarkdownDocument(title: report.fileName, text: text, source: report.markdownSource)
        } catch {
            self.error = error.localizedDescription
        }
    }
}

/// Datei oder Ordner im Finder markieren.
func reveal(_ path: String) {
    NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
}

struct MarkdownDocument: Identifiable {
    let id = UUID()
    let title: String
    let text: String
    var source: MarkdownSource = .embedded
}

/// Startargumente: Pfade, dazu für Tests `--select Datei`, `--snapshot bild.png`,
/// `--settings`, `--markdown` und `--dark`.
struct LaunchOptions {
    static let current = parse(CommandLine.arguments)

    var paths: [String] = []
    var select: String?
    var snapshot: String?
    var settings = false
    var markdown = false
    var dark = false

    /// Snapshot-Läufe für Tests verändern die gespeicherte Dateiliste nicht.
    var isScripted: Bool { snapshot != nil }

    static func parse(_ arguments: [String]) -> LaunchOptions {
        var options = LaunchOptions()
        var iterator = arguments.dropFirst().makeIterator()
        while let arg = iterator.next() {
            switch arg {
            case "--select": options.select = iterator.next()
            case "--snapshot": options.snapshot = iterator.next()
            case "--settings": options.settings = true
            case "--markdown": options.markdown = true
            case "--dark": options.dark = true
            default:
                // Xcode und LaunchServices hängen eigene Schalter an (-NSDocumentRevisionsDebugMode …).
                if !arg.hasPrefix("-") { options.paths.append(arg) }
            }
        }
        return options
    }
}

/// Fotografiert das eigene Fenster samt Titelleiste. Braucht keine Berechtigung zur
/// Bildschirmaufnahme. Liquid-Glass-Effekte rendert der Compositor, sie fehlen im Bild.
@MainActor
enum Snapshot {
    static func write(to path: String, settings: Bool = false, sheet: Bool = false) {
        let visible = NSApplication.shared.windows.filter(\.isVisible)
        let settingsWindow = visible.first { ($0.identifier?.rawValue ?? "").localizedCaseInsensitiveContains("settings") }
        let main = visible.first { $0 !== settingsWindow && $0.sheetParent == nil }
        var window = (settings ? settingsWindow : nil) ?? main
        if sheet, let attached = main?.attachedSheet { window = attached }
        guard let view = window?.contentView?.superview ?? window?.contentView,
              let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return }
        view.cacheDisplay(in: view.bounds, to: rep)
        try? rep.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: path))
    }
}
