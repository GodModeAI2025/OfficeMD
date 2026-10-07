import AppKit
import SwiftUI

struct DetailView: View {
    let report: FileReport
    @ObservedObject var store: Store
    @State private var preview: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                header
                statusCard
                if !report.warnings.isEmpty || !(report.possibleCauses ?? []).isEmpty { notesCard }
                if let preview { previewCard(preview) }
                detailsCard
            }
            .padding(28)
            .frame(maxWidth: 760, alignment: .leading)
            .frame(maxWidth: .infinity)
        }
        .scrollContentBackground(.hidden)
        .background(.background)
        .task(id: report) {
            let text = report.status == .unreadable ? nil : try? await store.markdownText(for: report)
            preview = text.map(stripFrontmatter)
        }
    }

    // MARK: Kopf

    private var header: some View {
        HStack(alignment: .center, spacing: 16) {
            DocIcon(kind: report.kind, size: 56)
            VStack(alignment: .leading, spacing: 4) {
                Text(report.fileName)
                    .font(.title2.weight(.semibold))
                    .lineLimit(2)
                    .textSelection(.enabled)
                Text(report.folder)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            Spacer()
            StateBadge(state: report.status)
        }
    }

    // MARK: Zustand und Hauptaktion

    private var statusCard: some View {
        Card {
            HStack(alignment: .top, spacing: 14) {
                Image(systemName: report.status.symbol)
                    .font(.system(size: 28, weight: .medium))
                    .foregroundStyle(report.status.color)
                    .frame(width: 36)
                VStack(alignment: .leading, spacing: 6) {
                    Text(headline).font(.headline)
                    Text(explanation)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            actions
        }
    }

    private var headline: String {
        switch report.status {
        case .current: return "Das eingebettete Markdown ist aktuell"
        case .stale: return "Der Inhalt hat sich geändert"
        case .lost: return "Das eingebettete Markdown ist verloren gegangen"
        case .never: return "Noch kein Markdown eingebettet"
        case .unreadable: return "Diese Datei kann Carrymark nicht lesen"
        }
    }

    private var explanation: String {
        switch report.status {
        case .current:
            return "Es entspricht genau dem, was markitdown heute aus der Datei erzeugt. Nichts zu tun."
        case .stale:
            return "Seit dem Einbetten wurde die Datei bearbeitet. „Aktualisieren“ erzeugt das Markdown neu und ersetzt das alte."
        case .lost:
            return (report.sidecar?.restorable ?? false)
                ? "Ein Programm hat den Markdown-Part beim Speichern entfernt. Im Sidecar-Ordner liegt eine Sicherung, die du zurückschreiben kannst."
                : "Ein Programm hat den Markdown-Part beim Speichern entfernt. Eine Sicherung gibt es nicht, das Markdown wird neu erzeugt."
        case .never:
            return "markitdown wandelt die Datei lokal in Markdown um, Carrymark legt es in die Datei. Nichts verlässt diesen Mac."
        case .unreadable:
            return report.message
        }
    }

    @ViewBuilder
    private var actions: some View {
        HStack(spacing: 10) {
            switch report.status {
            case .never:
                Button { sync() } label: { Label("Einbetten", systemImage: "square.and.arrow.down.on.square") }
                    .buttonStyle(.borderedProminent)
            case .stale:
                Button { sync() } label: { Label("Aktualisieren", systemImage: "arrow.triangle.2.circlepath") }
                    .buttonStyle(.borderedProminent)
            case .lost:
                Button { sync() } label: {
                    Label((report.sidecar?.restorable ?? false) ? "Wiederherstellen" : "Neu einbetten",
                          systemImage: "arrow.uturn.backward")
                }
                .buttonStyle(.borderedProminent)
            case .current, .unreadable:
                EmptyView()
            }
            if report.status != .unreadable {
                Button { Task { await store.exportMarkdown(for: report) } } label: {
                    Label("Als .md sichern", systemImage: "square.and.arrow.up")
                }
                .help("Markdown neben der Datei als .md ablegen")
            }
            Button {
                reveal(report.file)
            } label: {
                Label("Im Finder zeigen", systemImage: "folder")
            }
        }
        .controlSize(.large)
        .disabled(store.busy || report.locked)
        .padding(.leading, 50)
        if report.locked {
            Label("Die Datei ist in Office geöffnet. Bitte dort schließen, dann kann Carrymark schreiben.",
                  systemImage: "lock.fill")
                .font(.callout)
                .foregroundStyle(.secondary)
                .padding(.leading, 50)
        }
    }

    private func sync() {
        Task { await store.sync([report.file], label: report.fileName) }
    }

    // MARK: Vorschau

    private func previewCard(_ text: String) -> some View {
        Card(title: report.embedded ? "Eingebettetes Markdown" : "Markdown-Vorschau",
             symbol: "text.alignleft") {
            let lines = text.components(separatedBy: "\n")
            MarkdownPreview(text: lines.prefix(40).joined(separator: "\n"))
                .frame(maxHeight: 360, alignment: .top)
                .clipped()
                .mask(alignment: .top) {
                    LinearGradient(stops: [.init(color: .black, location: 0.82), .init(color: .clear, location: 1)],
                                   startPoint: .top, endPoint: .bottom)
                }
            HStack {
                Text(lines.count > 40 ? "\(lines.count) Zeilen, Auszug" : "\(lines.count) Zeilen")
                    .font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("Vollständig anzeigen") { Task { await store.showMarkdown(for: report) } }
            }
        }
    }

    // MARK: Hinweise

    private var notesCard: some View {
        Card(title: "Hinweise", symbol: "exclamationmark.bubble") {
            VStack(alignment: .leading, spacing: 8) {
                ForEach(report.warnings, id: \.self) { warning in
                    Label(warning, systemImage: "exclamationmark.triangle.fill")
                        .symbolRenderingMode(.multicolor)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let causes = report.possibleCauses, !causes.isEmpty {
                    Text("Mögliche Ursachen").font(.callout.weight(.medium)).padding(.top, 4)
                    ForEach(causes, id: \.self) { cause in
                        Label(cause, systemImage: "circle.fill")
                            .labelStyle(BulletLabelStyle())
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
    }

    // MARK: Details

    private var detailsCard: some View {
        Card(title: "Details", symbol: "info.circle") {
            Grid(alignment: .leadingFirstTextBaseline, horizontalSpacing: 18, verticalSpacing: 8) {
                row("Typ", report.kind.name)
                if let chars = report.characters {
                    row("Markdown", "\(chars.formatted()) Zeichen")
                }
                if let converter = report.converter { row("Konverter", converter) }
                if let date = report.embeddedDate { row("Eingebettet am", date) }
                if let saved = report.savedBy { row("Zuletzt gespeichert mit", saved) }
                if let guid = report.part?.guid { row("Part-GUID", guid, mono: true) }
                if let sidecar = report.sidecar, sidecar.exists, let path = sidecar.path {
                    GridRow {
                        Text("Sicherung").foregroundStyle(.secondary)
                        Button((path as NSString).lastPathComponent) {
                            reveal(path)
                        }
                        .buttonStyle(.link)
                    }
                }
            }
            .font(.callout)
        }
    }

    private func row(_ label: String, _ value: String, mono: Bool = false) -> some View {
        GridRow {
            Text(label).foregroundStyle(.secondary)
            Text(value)
                .font(mono ? .callout.monospaced() : .callout)
                .textSelection(.enabled)
        }
    }
}

struct BulletLabelStyle: LabelStyle {
    func makeBody(configuration: Configuration) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            configuration.icon.font(.system(size: 5)).alignmentGuide(.firstTextBaseline) { $0[VerticalAlignment.center] + 3 }
            configuration.title
        }
    }
}

/// Markdown ohne die OKF-Frontmatter, für die Vorschau.
func stripFrontmatter(_ text: String) -> String {
    guard text.hasPrefix("---\n"),
          let end = text.range(of: "\n---\n", range: text.index(text.startIndex, offsetBy: 4)..<text.endIndex)
    else { return text }
    return String(text[end.upperBound...]).trimmingCharacters(in: .newlines)
}
