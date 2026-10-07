import AppKit
import SwiftUI
import UniformTypeIdentifiers

struct ContentView: View {
    @StateObject private var store = Store()
    @State private var importing = false
    @State private var search = ""
    @Environment(\.openSettings) private var openSettings

    @State private var filter: Filter = .all

    /// Filter-Chips oben in der Seitenleiste; ersetzen Abschnittsüberschriften.
    enum Filter: String, CaseIterable, Identifiable {
        case all, attention, current, new
        var id: String { rawValue }

        var title: String {
            switch self {
            case .all: return "Alle"
            case .attention: return "Offen"
            case .current: return "Aktuell"
            case .new: return "Neu"
            }
        }

        func matches(_ report: FileReport) -> Bool {
            switch self {
            case .all: return true
            case .attention: return report.status.group == 0
            case .current: return report.status == .current
            case .new: return report.status == .never
            }
        }
    }

    private var filtered: [FileReport] {
        store.reports.sorted(by: Store.order)
            .filter(filter.matches)
            .filter { search.isEmpty || $0.fileName.localizedCaseInsensitiveContains(search) }
    }

    var body: some View {
        NavigationSplitView {
            sidebar
                .navigationSplitViewColumnWidth(min: 300, ideal: 340, max: 420)
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
            Section {
            ForEach(filtered) { report in
                FileRow(report: report)
                    .tag(report.id)
                    .listRowSeparator(.hidden)
                    .listRowInsets(EdgeInsets(top: 4, leading: 8, bottom: 4, trailing: 8))
                    .contextMenu { rowMenu(report) }
                    .swipeActions(edge: .leading, allowsFullSwipe: true) {
                        if report.status != .current && report.status != .unreadable {
                            Button { sync(report) } label: {
                                Label("Aktualisieren", systemImage: "arrow.triangle.2.circlepath")
                            }
                            .tint(.accentColor)
                        }
                    }
                    .swipeActions(edge: .trailing) {
                        if let root = store.roots.first(where: { $0.path == report.file }) {
                            Button(role: .destructive) { store.remove(root) } label: {
                                Label("Entfernen", systemImage: "trash")
                            }
                        }
                        Button { Task { await store.exportMarkdown(for: report) } } label: {
                            Label("Als .md", systemImage: "square.and.arrow.up")
                        }
                        .tint(.indigo)
                    }
            }
            } header: {
                if !store.reports.isEmpty { filterChips }
            }
        }
        .listStyle(.sidebar)
        .environment(\.defaultMinListRowHeight, 64)
        .safeAreaInset(edge: .bottom, spacing: 0) {
            VStack(spacing: 6) {
                Button { importing = true } label: {
                    Label("Hinzufügen", systemImage: "plus")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .buttonBorderShape(.capsule)
                .controlSize(.large)
                if !store.skipped.isEmpty { SkippedFooter(files: store.skipped) }
            }
            .padding(12)
        }
        .overlay {
            if store.reports.isEmpty {
                ContentUnavailableView {
                    Label("Noch keine Dateien", systemImage: "tray")
                } description: {
                    Text("Ziehe Dateien oder Ordner ins Fenster.")
                }
            } else if filtered.isEmpty {
                if search.isEmpty {
                    ContentUnavailableView("Nichts in „\(filter.title)“", systemImage: "line.3.horizontal.decrease.circle")
                } else {
                    ContentUnavailableView.search(text: search)
                }
            }
        }
    }

    private var filterChips: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                ForEach(Filter.allCases) { item in
                    let count = store.reports.filter(item.matches).count
                    Button { withAnimation(.snappy) { filter = item } } label: {
                        HStack(spacing: 6) {
                            Text(item.title)
                            Text("\(count)")
                                .monospacedDigit()
                                .foregroundStyle(filter == item ? .white.opacity(0.85) : .secondary)
                        }
                        .font(.callout.weight(.medium))
                        .padding(.horizontal, 11)
                        .frame(minHeight: 34)
                        .foregroundStyle(filter == item ? .white : .primary)
                        .background(filter == item ? AnyShapeStyle(Color.accentColor) : AnyShapeStyle(.quaternary),
                                    in: Capsule())
                        .contentShape(Capsule())
                    }
                    .buttonStyle(.plain)
                    .accessibilityAddTraits(filter == item ? .isSelected : [])
                }
            }
            .padding(.vertical, 6)
        }
        .textCase(nil)
    }

    private func sync(_ report: FileReport) {
        Task { await store.sync([report.file], label: report.fileName) }
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
        ToolbarItem(placement: .primaryAction) {
            Menu {
                Button { importing = true } label: { Label("Hinzufügen …", systemImage: "plus") }
                Button { Task { await store.refresh() } } label: { Label("Neu prüfen", systemImage: "arrow.clockwise") }
                    .disabled(store.roots.isEmpty || store.busy)
                Button { Task { await store.exportBundle() } } label: {
                    Label("Als OKF-Bundle exportieren …", systemImage: "square.and.arrow.up.on.square")
                }
                .disabled(store.roots.isEmpty || store.busy)
                Divider()
                Button { openSettings() } label: { Label("Einstellungen …", systemImage: "gearshape") }
            } label: {
                Label("Mehr", systemImage: "ellipsis.circle")
            }
            .help("Weitere Aktionen")
        }
        ToolbarItem(placement: .primaryAction) {
            Button {
                Task { await store.sync(store.roots.map(\.path), label: "Alle Dateien") }
            } label: {
                Label("Alle aktualisieren", systemImage: "arrow.triangle.2.circlepath")
                    .labelStyle(.titleAndIcon)
            }
            .buttonStyle(.borderedProminent)
            .buttonBorderShape(.capsule)
            .help("Alle Dateien prüfen und bei Bedarf Markdown einbetten oder wiederherstellen")
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
                Text("Diese Dateien haben ein unterstütztes Format, Carrymark kann sie aber nicht verarbeiten, zum Beispiel weil sie verschlüsselt, signiert, makrofähig oder beschädigt sind.")
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
        HStack(spacing: 12) {
            DocIcon(kind: report.kind, size: 40)
            VStack(alignment: .leading, spacing: 5) {
                Text(report.fileName)
                    .font(.body.weight(.semibold))
                    .lineLimit(1)
                    .truncationMode(.middle)
                HStack(spacing: 6) {
                    StateBadge(state: report.status, compact: true, short: true)
                    if let chars = report.characters, report.status != .unreadable {
                        Text("\(chars.formatted()) Zeichen")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    if report.locked {
                        Image(systemName: "lock.fill")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .help("In Office geöffnet")
                    }
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 8)
        .padding(.horizontal, 4)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }
}

struct WelcomeView: View {
    let add: () -> Void

    var body: some View {
        VStack(spacing: 24) {
            VStack(spacing: 8) {
                Text("Markdown, das in der Datei bleibt").font(.largeTitle.weight(.bold))
                Text("Carrymark wandelt Word, Excel, PowerPoint und PDF mit microsoft/markitdown in Markdown um, legt es direkt in die Datei und sagt dir, wann es nicht mehr zum Inhalt passt.")
                    .font(.title3)
                    .multilineTextAlignment(.center)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: 520)
            }
            Button(action: add) {
                VStack(spacing: 14) {
                    Image(systemName: "square.and.arrow.down.on.square")
                        .font(.system(size: 44, weight: .light))
                        .foregroundStyle(.tint)
                    Text("Dateien oder Ordner hier ablegen").font(.title3.weight(.semibold))
                    Text("oder tippen, um auszuwählen").foregroundStyle(.secondary)
                }
                .frame(maxWidth: 520, minHeight: 220)
                .background(Color.accentColor.opacity(0.06), in: RoundedRectangle(cornerRadius: 24, style: .continuous))
                .overlay {
                    RoundedRectangle(cornerRadius: 24, style: .continuous)
                        .strokeBorder(Color.accentColor.opacity(0.45), style: StrokeStyle(lineWidth: 2, dash: [8, 6]))
                }
                .contentShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
            }
            .buttonStyle(.plain)
            Text("Word · Excel · PowerPoint · PDF").font(.callout).foregroundStyle(.tertiary)
        }
        .padding(40)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
