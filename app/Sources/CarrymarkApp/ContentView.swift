import AppKit
import SwiftUI
import UniformTypeIdentifiers

struct ContentView: View {
    @StateObject private var store = Store()
    @State private var importing = false
    @State private var search = ""
    @Environment(\.openSettings) private var openSettings

    private var filtered: [FileReport] {
        let all = store.reports.sorted(by: Store.order)
        guard !search.isEmpty else { return all }
        return all.filter { $0.fileName.localizedCaseInsensitiveContains(search) }
    }

    private var sections: [(String, [FileReport])] {
        let groups = Dictionary(grouping: filtered) { $0.status.group }
        return [(0, "Braucht Aufmerksamkeit"), (1, "Aktuell"), (2, "Noch nicht eingebettet")]
            .compactMap { key, title in groups[key].map { (title, $0) } }
    }

    var body: some View {
        NavigationSplitView {
            sidebar
                .navigationSplitViewColumnWidth(min: 260, ideal: 300, max: 380)
        } detail: {
            detail
        }
        .searchable(text: $search, placement: .sidebar, prompt: "Dateien filtern")
        .toolbar { toolbar }
        .navigationTitle(store.selected?.fileName ?? "Carrymark")
        .navigationSubtitle(subtitle)
        .fileImporter(isPresented: $importing,
                      allowedContentTypes: [.folder] + Store.supportedExtensions.sorted().compactMap { UTType(filenameExtension: $0) },
                      allowsMultipleSelection: true) { result in
            if case .success(let urls) = result { store.add(urls) }
        }
        .dropDestination(for: URL.self) { urls, _ in
            store.add(urls)
            return true
        }
        .overlay(alignment: .bottom) { syncBanner }
        .onReceive(NotificationCenter.default.publisher(for: .carrymarkImport)) { _ in importing = true }
        .onAppear {
            if LaunchOptions.current.settings { openSettings() }
        }
        .alert("Das hat nicht geklappt",
               isPresented: Binding(get: { store.error != nil }, set: { if !$0 { store.error = nil } })) {
            Button("OK") { store.error = nil }
        } message: {
            Text(store.error ?? "")
        }
        .sheet(item: $store.markdown) { document in
            MarkdownSheet(document: document) { store.markdown = nil }
        }
        .sheet(isPresented: Binding(get: { store.syncResults != nil }, set: { if !$0 { store.syncResults = nil } })) {
            SyncResultSheet(results: store.syncResults ?? []) { store.syncResults = nil }
        }
    }

    private var subtitle: String {
        guard !store.reports.isEmpty else { return "" }
        let attention = store.reports.filter { $0.status.group == 0 }.count
        let total = store.reports.count
        return attention == 0 ? "\(total) Dateien, alles aktuell" : "\(total) Dateien, \(attention) brauchen Aufmerksamkeit"
    }

    // MARK: Seitenleiste

    private var sidebar: some View {
        List(selection: $store.selection) {
            ForEach(sections, id: \.0) { title, reports in
                Section(title) {
                    ForEach(reports) { report in
                        FileRow(report: report).tag(report.id)
                            .contextMenu { rowMenu(report) }
                    }
                }
            }
        }
        .listStyle(.sidebar)
        .safeAreaInset(edge: .bottom) {
            if !store.skipped.isEmpty { SkippedFooter(files: store.skipped) }
        }
        .overlay {
            if store.reports.isEmpty {
                ContentUnavailableView {
                    Label("Noch keine Dateien", systemImage: "doc.on.doc")
                } description: {
                    Text("Ziehe einen Ordner oder Office-Dateien hierher.")
                } actions: {
                    Button("Ordner hinzufügen …") { importing = true }
                        .buttonStyle(.borderedProminent)
                }
            } else if filtered.isEmpty {
                ContentUnavailableView.search(text: search)
            }
        }
    }

    @ViewBuilder
    private func rowMenu(_ report: FileReport) -> some View {
        if let root = store.roots.first(where: { $0.path == report.file }) {
            Button("Aus der Liste entfernen") { store.remove(root) }
            Divider()
        }
        Button("Aktualisieren") { Task { await store.sync([report.file], label: report.fileName) } }
            .disabled(report.status == .unreadable || report.status == .current)
        Button("Als .md sichern") { Task { await store.exportMarkdown(for: report) } }
            .disabled(report.status == .unreadable)
        if report.status == .current || report.status == .stale {
            Button("Markdown anzeigen") { Task { await store.showMarkdown(for: report) } }
        }
        Divider()
        Button("Im Finder zeigen") {
            reveal(report.file)
        }
    }

    // MARK: Detail

    @ViewBuilder
    private var detail: some View {
        if let report = store.selected {
            DetailView(report: report, store: store)
                .id(report.id)
        } else if !store.reports.isEmpty {
            ContentUnavailableView("Datei auswählen", systemImage: "sidebar.left",
                                   description: Text("Links eine Datei wählen, um Zustand und Belege zu sehen."))
        } else {
            WelcomeView { importing = true }
        }
    }

    // MARK: Symbolleiste

    @ToolbarContentBuilder
    private var toolbar: some ToolbarContent {
        ToolbarItemGroup {
            Button { importing = true } label: { Label("Hinzufügen", systemImage: "folder.badge.plus") }
                .help("Ordner oder Dateien hinzufügen (⌘O)")
            Button { Task { await store.refresh() } } label: { Label("Neu prüfen", systemImage: "arrow.clockwise") }
                .help("Alle Dateien neu prüfen")
                .disabled(store.roots.isEmpty || store.busy)
            Button { Task { await store.exportBundle() } } label: {
                Label("OKF-Bundle exportieren", systemImage: "square.and.arrow.up.on.square")
            }
            .help("Alle Dateien als Markdown-Bundle im Open Knowledge Format exportieren")
            .disabled(store.roots.isEmpty || store.busy)
        }
        ToolbarItem {
            Button { openSettings() } label: { Label("Einstellungen", systemImage: "gearshape") }
                .help("Einstellungen (⌘,)")
        }
        ToolbarItem(placement: .primaryAction) {
            Button {
                Task { await store.sync(store.roots.map(\.path), label: "Alle Dateien") }
            } label: {
                Label("Alle aktualisieren", systemImage: "wand.and.stars")
            }
            .help("Alle Dateien prüfen und bei Bedarf Markdown neu einbetten")
            .disabled(store.roots.isEmpty || store.busy)
        }
    }

    // MARK: Fortschritt

    @ViewBuilder
    private var syncBanner: some View {
        if let label = store.syncStatus {
            HStack(spacing: 12) {
                ProgressView().controlSize(.small)
                VStack(alignment: .leading, spacing: 1) {
                    Text("\(label) wird geprüft und aktualisiert").font(.callout.weight(.medium))
                    Text("markitdown wandelt lokal um.").font(.caption).foregroundStyle(.secondary)
                }
            }
            .floatingBanner()
        } else if let notice = store.notice {
            Label(notice, systemImage: "checkmark.circle.fill")
                .font(.callout.weight(.medium))
                .symbolRenderingMode(.multicolor)
                .floatingBanner()
        }
    }
}

extension View {
    /// Schwebende Meldung am unteren Fensterrand (Liquid Glass ab macOS 26).
    func floatingBanner() -> some View {
        padding(.horizontal, 18)
            .padding(.vertical, 11)
            .floatingGlass(in: Capsule())
            .padding(.bottom, 20)
            .transition(.move(edge: .bottom).combined(with: .opacity))
    }
}

/// Dezenter Hinweis unten in der Seitenleiste: unterstützte Dateien, die sich nicht verarbeiten lassen.
struct SkippedFooter: View {
    let files: [FileReport]
    @State private var showing = false

    var body: some View {
        Button { showing.toggle() } label: {
            Label(files.count == 1 ? "1 Datei übersprungen" : "\(files.count) Dateien übersprungen",
                  systemImage: "eye.slash")
                .font(.caption)
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .popover(isPresented: $showing, arrowEdge: .trailing) {
            VStack(alignment: .leading, spacing: 10) {
                Text("Nicht verarbeitbar").font(.headline)
                Text("Diese Dateien haben ein unterstütztes Format, Carrymark kann sie aber nicht lesen, zum Beispiel weil sie verschlüsselt, makrofähig oder beschädigt sind.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                ForEach(files) { file in
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        DocIcon(kind: file.kind, size: 18)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(file.fileName).font(.callout.weight(.medium))
                            Text(file.message).font(.caption).foregroundStyle(.secondary).lineLimit(3)
                        }
                    }
                }
            }
            .padding(16)
            .frame(width: 340)
        }
    }
}

struct FileRow: View {
    let report: FileReport

    var body: some View {
        HStack(spacing: 10) {
            DocIcon(kind: report.kind, size: 28)
            VStack(alignment: .leading, spacing: 2) {
                Text(report.fileName)
                    .lineLimit(1)
                    .truncationMode(.middle)
                Text(subtitle)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 4)
            if report.locked {
                Image(systemName: "lock.fill")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .help("In Office geöffnet")
            }
            Image(systemName: report.status.symbol)
                .foregroundStyle(report.status.color)
                .help(report.status.label)
                .accessibilityLabel(report.status.label)
        }
        .padding(.vertical, 3)
    }

    private var subtitle: String {
        if let chars = report.characters, report.status != .unreadable {
            return "\(report.status.label) · \(chars.formatted()) Zeichen"
        }
        return report.status.label
    }
}

struct WelcomeView: View {
    let add: () -> Void

    var body: some View {
        VStack(spacing: 18) {
            Image(systemName: "doc.richtext")
                .font(.system(size: 52, weight: .light))
                .foregroundStyle(.tint)
            VStack(spacing: 6) {
                Text("Markdown, das in der Datei bleibt").font(.title2.weight(.semibold))
                Text("Carrymark wandelt Word, Excel und PowerPoint mit microsoft/markitdown in Markdown um, legt es direkt in die Datei und sagt dir, wann es nicht mehr zum Inhalt passt.")
                    .multilineTextAlignment(.center)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: 440)
            }
            Button("Ordner hinzufügen …", action: add)
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
        }
        .padding(40)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
