import Foundation

/// Ein Eintrag aus `carrymark check --json`.
struct FileReport: Decodable, Identifiable, Hashable {
    var id: String { file }
    let file: String
    let state: String
    let message: String
    let embedded: Bool
    let locked: Bool
    let warnings: [String]
    let possibleCauses: [String]?
    let sidecar: Sidecar?
    let part: Part?
    let characters: Int?
    let converter: String?
    let savedBy: String?

    struct Part: Decodable, Hashable {
        let embeddedAt: String?
        let guid: String?

        enum CodingKeys: String, CodingKey {
            case guid
            case embeddedAt = "embedded_at"
        }
    }

    struct Sidecar: Decodable, Hashable {
        let exists: Bool
        let restorable: Bool
        let path: String?
    }

    enum CodingKeys: String, CodingKey {
        case file, state, message, embedded, locked, warnings, sidecar, part, characters, converter
        case possibleCauses = "possible_causes"
        case savedBy = "saved_by"
    }

    var fileName: String { (file as NSString).lastPathComponent }
    var folder: String { ((file as NSString).deletingLastPathComponent as NSString).abbreviatingWithTildeInPath }
    var kind: DocKind { DocKind(path: file) }
    var status: DocState { DocState(rawValue: state) ?? .unreadable }

    /// Gesicherte Fassung im Sidecar-Ordner, falls vorhanden.
    var backupMarkdownPath: String? {
        guard let sidecar, sidecar.restorable, let path = sidecar.path else { return nil }
        return (path as NSString).appendingPathComponent("markdown.md")
    }

    /// Woher das gezeigte Markdown stammt; entscheidet über die Beschriftung.
    var markdownSource: MarkdownSource {
        if embedded { return .embedded }
        if status == .lost, backupMarkdownPath != nil { return .backup }
        return .preview
    }

    /// Datum der Einbettung, lokal formatiert.
    var embeddedDate: String? {
        guard let raw = part?.embeddedAt, let date = try? Date(raw, strategy: .iso8601) else { return nil }
        return date.formatted(date: .abbreviated, time: .shortened)
    }
}

/// Ein Eintrag aus `carrymark sync --json`.
struct SyncResult: Decodable, Identifiable {
    var id: String { file }
    let file: String
    let action: String
    let message: String
    let after: String?

    var fileName: String { (file as NSString).lastPathComponent }
}

enum CLIError: LocalizedError {
    case notFound(String)
    case failed(String)

    var errorDescription: String? {
        switch self {
        case .notFound(let path):
            return "carrymark nicht gefunden (\(path)). Pfad in den Einstellungen setzen."
        case .failed(let message):
            return message
        }
    }
}

enum MarkdownSource {
    case embedded, backup, preview

    var title: String {
        switch self {
        case .embedded: return "Eingebettetes Markdown"
        case .backup: return "Sicherung, nicht in der Datei"
        case .preview: return "Vorschau, noch nicht in der Datei"
        }
    }

    var note: String? {
        switch self {
        case .embedded: return nil
        case .backup: return "Diese Fassung liegt im Sidecar-Ordner. „Wiederherstellen“ schreibt sie zurück in die Datei."
        case .preview: return "So würde markitdown den aktuellen Inhalt umwandeln. In der Datei steckt dieses Markdown noch nicht."
        }
    }
}
