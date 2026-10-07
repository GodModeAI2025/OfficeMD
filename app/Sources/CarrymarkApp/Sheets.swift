import AppKit
import SwiftUI

// MARK: Markdown

struct MarkdownSheet: View {
    let document: MarkdownDocument
    let close: () -> Void
    @State private var mode = 0

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(document.embedded ? "Eingebettetes Markdown" : "Markdown-Vorschau").font(.headline)
                    Text(document.title).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Picker("Ansicht", selection: $mode) {
                    Text("Vorschau").tag(0)
                    Text("Quelltext").tag(1)
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .frame(width: 200)
            }
            .padding(16)
            Divider()
            ScrollView {
                Group {
                    if mode == 0 {
                        MarkdownPreview(text: document.text)
                    } else {
                        Text(document.text)
                            .font(.system(.callout, design: .monospaced))
                            .textSelection(.enabled)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(20)
            }
            Divider()
            HStack {
                Button {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(document.text, forType: .string)
                } label: { Label("Kopieren", systemImage: "doc.on.doc") }
                Spacer()
                Button("Fertig", action: close)
                    .keyboardShortcut(.defaultAction)
                    .buttonStyle(.borderedProminent)
            }
            .padding(16)
        }
        .frame(minWidth: 720, idealWidth: 780, minHeight: 560, idealHeight: 680)
    }
}

/// Schlanke Vorschau für das Distiller-Markdown: Frontmatter als Metadaten, Überschriften,
/// Listen und Absätze. Inline-Formatierung über AttributedString.
struct MarkdownPreview: View {
    let text: String

    private var parts: (meta: [(String, String)], body: [String]) {
        var lines = text.components(separatedBy: "\n")
        var meta: [(String, String)] = []
        if lines.first == "---", let end = lines.dropFirst().firstIndex(of: "---") {
            for line in lines[1..<end] where !line.hasPrefix(" ") && line.contains(":") {
                let pieces = line.split(separator: ":", maxSplits: 1).map { String($0).trimmingCharacters(in: .whitespaces) }
                if pieces.count == 2, !pieces[1].isEmpty {
                    meta.append((pieces[0], pieces[1].trimmingCharacters(in: CharacterSet(charactersIn: "\""))))
                }
            }
            lines = Array(lines[(end + 1)...])
        }
        return (meta, lines)
    }

    var body: some View {
        let parsed = parts
        VStack(alignment: .leading, spacing: 8) {
            if !parsed.meta.isEmpty {
                Grid(alignment: .leadingFirstTextBaseline, horizontalSpacing: 14, verticalSpacing: 4) {
                    ForEach(Array(parsed.meta.prefix(12).enumerated()), id: \.offset) { _, item in
                        GridRow {
                            Text(item.0).foregroundStyle(.secondary)
                            Text(item.1).textSelection(.enabled)
                        }
                    }
                }
                .font(.caption)
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.background.secondary, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                .padding(.bottom, 8)
            }
            ForEach(Array(blocks(parsed.body).enumerated()), id: \.offset) { _, block in
                switch block {
                case .line(let line): lineView(line)
                case .table(let rows): tableView(rows)
                }
            }
        }
        .textSelection(.enabled)
    }

    private enum Block {
        case line(String)
        case table([[String]])
    }

    /// Fasst aufeinanderfolgende Tabellenzeilen (``| a | b |``) zu einer Tabelle zusammen.
    private func blocks(_ lines: [String]) -> [Block] {
        var result: [Block] = []
        var rows: [[String]] = []
        func flush() {
            if !rows.isEmpty { result.append(.table(rows)); rows = [] }
        }
        for line in lines {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.hasPrefix("|") && trimmed.hasSuffix("|") && trimmed.count > 1 {
                let cells = trimmed.dropFirst().dropLast().components(separatedBy: "|")
                    .map { $0.trimmingCharacters(in: .whitespaces) }
                if cells.allSatisfy({ $0.allSatisfy { "-: ".contains($0) } && !$0.isEmpty }) { continue }
                rows.append(cells)
            } else {
                flush()
                result.append(.line(line))
            }
        }
        flush()
        return result
    }

    private func tableView(_ rows: [[String]]) -> some View {
        let width = rows.map(\.count).max() ?? 0
        return Grid(alignment: .leading, horizontalSpacing: 0, verticalSpacing: 0) {
            ForEach(Array(rows.enumerated()), id: \.offset) { index, row in
                GridRow {
                    ForEach(0..<width, id: \.self) { column in
                        Text(inline(column < row.count ? row[column] : ""))
                            .font(index == 0 ? .callout.weight(.semibold) : .callout)
                            .padding(.horizontal, 10)
                            .padding(.vertical, 6)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(index == 0 ? Color.secondary.opacity(0.12)
                                        : (index.isMultiple(of: 2) ? Color.secondary.opacity(0.05) : .clear))
                    }
                }
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous).strokeBorder(.separator, lineWidth: 0.5))
        .padding(.vertical, 4)
    }

    @ViewBuilder
    private func lineView(_ raw: String) -> some View {
        let line = raw.replacingOccurrences(of: "[[", with: "").replacingOccurrences(of: "]]", with: "")
        if let slide = slideNumber(line) {
            Text("Folie \(slide)")
                .font(.headline)
                .foregroundStyle(.secondary)
                .padding(.top, 10)
        } else if line.hasPrefix("<!--") {
            EmptyView()
        } else if line.hasPrefix("# ") {
            Text(inline(String(line.dropFirst(2)))).font(.title2.weight(.semibold)).padding(.top, 10)
        } else if line.hasPrefix("## ") {
            Text(inline(String(line.dropFirst(3)))).font(.title3.weight(.semibold)).padding(.top, 8)
        } else if line.hasPrefix("### ") {
            Text(inline(String(line.dropFirst(4)))).font(.headline).padding(.top, 4)

        } else if let range = line.range(of: #"^\s*[-*] "#, options: .regularExpression) {
            let indent = line.distance(from: line.startIndex, to: range.upperBound) - 2
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text("•").foregroundStyle(.secondary)
                Text(inline(String(line[range.upperBound...])))
            }
            .padding(.leading, CGFloat(indent) * 8)
        } else if line.hasPrefix("```") || line.trimmingCharacters(in: .whitespaces).isEmpty {
            Spacer().frame(height: 2)
        } else if line.hasPrefix(">") {
            Text(inline(String(line.dropFirst()).trimmingCharacters(in: .whitespaces)))
                .foregroundStyle(.secondary)
                .padding(.leading, 10)
                .overlay(alignment: .leading) { Rectangle().fill(.tint).frame(width: 3) }
        } else {
            Text(inline(line))
        }
    }

    /// markitdown markiert Folien als ``<!-- Slide number: 3 -->``.
    private func slideNumber(_ line: String) -> String? {
        guard line.hasPrefix("<!--"),
              let range = line.range(of: #"Slide number:\s*\d+"#, options: .regularExpression) else { return nil }
        return line[range].components(separatedBy: CharacterSet.decimalDigits.inverted).joined()
    }

    private func inline(_ value: String) -> AttributedString {
        (try? AttributedString(markdown: value, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)))
            ?? AttributedString(value)
    }
}

// MARK: Ergebnis von „Aktualisieren“

struct SyncResultSheet: View {
    let results: [SyncResult]
    let close: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 12) {
                Image(systemName: failed == 0 ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                    .font(.system(size: 28))
                    .foregroundStyle(failed == 0 ? .green : .orange)
                VStack(alignment: .leading, spacing: 2) {
                    Text(failed == 0 ? "Fertig" : "Fertig, mit Problemen").font(.title3.weight(.semibold))
                    Text(summary).font(.callout).foregroundStyle(.secondary)
                }
            }
            .padding(20)
            Divider()
            List(results) { result in
                HStack(alignment: .top, spacing: 12) {
                    DocIcon(kind: DocKind(path: result.file), size: 30)
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text(result.fileName).font(.headline)
                            Spacer()
                            if let after = result.after, let state = DocState(rawValue: after) {
                                StateBadge(state: state, compact: true)
                            }
                        }
                        Text(result.message).font(.callout).foregroundStyle(.secondary)
                        ForEach(result.problems ?? [], id: \.self) { problem in
                            Label(problem, systemImage: "exclamationmark.circle")
                                .font(.caption)
                                .foregroundStyle(.orange)
                        }
                    }
                }
                .padding(.vertical, 6)
            }
            .listStyle(.inset)
            Divider()
            HStack {
                Spacer()
                Button("Fertig", action: close)
                    .keyboardShortcut(.defaultAction)
                    .buttonStyle(.borderedProminent)
            }
            .padding(16)
        }
        .frame(minWidth: 640, minHeight: 420)
    }

    private var failed: Int { results.filter { $0.action == "error" }.count }

    private var summary: String {
        let changed = results.filter { ["embed", "reembed", "restore"].contains($0.action) }.count
        let unchanged = results.filter { $0.action == "none" }.count
        var parts = ["\(changed) aktualisiert", "\(unchanged) schon aktuell"]
        if failed > 0 { parts.append("\(failed) fehlgeschlagen") }
        return parts.joined(separator: ", ")
    }
}
