import SwiftUI

/// Dokumenttyp mit Symbol und Farbe, angelehnt an die Office-Farben.
enum DocKind {
    case word, excel, powerpoint, pdf, other

    init(path: String) {
        switch (path as NSString).pathExtension.lowercased() {
        case "docx": self = .word
        case "xlsx": self = .excel
        case "pptx": self = .powerpoint
        case "pdf": self = .pdf
        default: self = .other
        }
    }

    var symbol: String {
        switch self {
        case .word: return "doc.text.fill"
        case .excel: return "tablecells.fill"
        case .powerpoint: return "play.rectangle.fill"
        case .pdf: return "doc.richtext.fill"
        case .other: return "doc.fill"
        }
    }

    var tint: Color {
        switch self {
        case .word: return Color(red: 0.16, green: 0.40, blue: 0.80)
        case .excel: return Color(red: 0.10, green: 0.55, blue: 0.33)
        case .powerpoint: return Color(red: 0.85, green: 0.35, blue: 0.20)
        case .pdf: return Color(red: 0.78, green: 0.16, blue: 0.20)
        case .other: return .gray
        }
    }

    var name: String {
        switch self {
        case .word: return "Word"
        case .excel: return "Excel"
        case .powerpoint: return "PowerPoint"
        case .pdf: return "PDF"
        case .other: return "Datei"
        }
    }
}

/// Zustand einer Datei mit semantischer Farbe und SF Symbol.
enum DocState: String, CaseIterable {
    case current, stale, lost, never, unreadable

    var label: String {
        switch self {
        case .current: return "Aktuell"
        case .stale: return "Veraltet"
        case .lost: return "Verloren"
        case .never: return "Nicht eingebettet"
        case .unreadable: return "Nicht lesbar"
        }
    }

    var symbol: String {
        switch self {
        case .current: return "checkmark.seal.fill"
        case .stale: return "exclamationmark.arrow.triangle.2.circlepath"
        case .lost: return "bandage.fill"
        case .never: return "circle.dashed"
        case .unreadable: return "xmark.octagon.fill"
        }
    }

    var color: Color {
        switch self {
        case .current: return .green
        case .stale: return .orange
        case .lost: return .red
        case .never: return .secondary
        case .unreadable: return .red
        }
    }

    /// Reihenfolge in der Seitenleiste: was Aufmerksamkeit braucht, zuerst.
    var group: Int {
        switch self {
        case .stale, .lost, .unreadable: return 0
        case .current: return 1
        case .never: return 2
        }
    }
}

struct DocIcon: View {
    let kind: DocKind
    var size: CGFloat = 28

    var body: some View {
        RoundedRectangle(cornerRadius: size * 0.26, style: .continuous)
            .fill(kind.tint.gradient)
            .frame(width: size, height: size)
            .overlay {
                Image(systemName: kind.symbol)
                    .font(.system(size: size * 0.48, weight: .semibold))
                    .foregroundStyle(.white)
            }
            .accessibilityLabel(kind.name)
    }
}

struct StateBadge: View {
    let state: DocState
    var compact = false
    var short = false
    /// In einer ausgewählten Listenzeile (blauer Grund) wird die Plakette weiß, sonst ist Rot auf Blau kaum lesbar.
    @Environment(\.backgroundProminence) private var prominence

    var body: some View {
        let onSelection = prominence == .increased
        Label(short && state == .never ? "Neu" : state.label, systemImage: state.symbol)
            .labelStyle(.titleAndIcon)
            .font(compact ? .caption.weight(.medium) : .callout.weight(.semibold))
            .padding(.horizontal, compact ? 7 : 10)
            .padding(.vertical, compact ? 2 : 4)
            .foregroundStyle(onSelection ? Color.white : state.color)
            .background((onSelection ? Color.white.opacity(0.22) : state.color.opacity(0.13)), in: Capsule())
    }
}

/// Karte im Inhaltsbereich: ruhiger Hintergrund, keine Schatten (HIG: nur funktionale Effekte).
struct Card<Content: View>: View {
    var title: String?
    var symbol: String?
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let title {
                Label(title, systemImage: symbol ?? "")
                    .labelStyle(.titleAndIcon)
                    .font(.headline)
                    .foregroundStyle(.primary)
            }
            content
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .strokeBorder(.separator.opacity(0.6), lineWidth: 0.5)
        }
    }
}

struct MetricTile: View {
    let value: String
    let label: String
    let color: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(value)
                .font(.system(.title, design: .rounded).weight(.semibold))
                .foregroundStyle(color)
                .contentTransition(.numericText())
            Text(label).font(.caption).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(color.opacity(0.08), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
    }
}

extension View {
    /// Liquid Glass für schwebende Navigationselemente ab macOS 26, sonst Material.
    @ViewBuilder
    func floatingGlass<S: Shape>(in shape: S) -> some View {
        #if compiler(>=6.2)  // Xcode 26 und neuer kennen glassEffect
        if #available(macOS 26.0, *) {
            self.glassEffect(.regular, in: shape)
        } else {
            self.background(.regularMaterial, in: shape)
        }
        #else
        self.background(.regularMaterial, in: shape)
        #endif
    }
}
