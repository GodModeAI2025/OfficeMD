import Foundation

/// Ein Eintrag aus `officemd check --json`.
struct FileReport: Decodable, Identifiable, Hashable {
    var id: String { file }
    let file: String
    let state: String
    let label: String
    let message: String
    let mode: String?
    let locked: Bool
    let warnings: [String]
    let evidence: Evidence?
    let possibleCauses: [String]?
    let sidecar: Sidecar?

    struct Evidence: Decodable, Hashable {
        let total: Int
        let counts: Counts
    }

    struct Counts: Decodable, Hashable {
        let verified: Int
        let notFound: Int
        let unverifiable: Int

        enum CodingKeys: String, CodingKey {
            case verified
            case notFound = "not_found"
            case unverifiable
        }
    }

    struct Sidecar: Decodable, Hashable {
        let exists: Bool
        let restorable: Bool
    }

    enum CodingKeys: String, CodingKey {
        case file, state, label, message, mode, locked, warnings, evidence, sidecar
        case possibleCauses = "possible_causes"
    }

    var fileName: String { (file as NSString).lastPathComponent }
}

enum CLIError: LocalizedError {
    case notFound(String)
    case failed(String)

    var errorDescription: String? {
        switch self {
        case .notFound(let path):
            return "officemd nicht gefunden (\(path)). Pfad in den Einstellungen setzen."
        case .failed(let message):
            return message
        }
    }
}

/// `officemd config show --json`, ohne Geheimnisse.
struct AIConfig: Decodable {
    let provider: String?
    let mode: String
    let depth: String
    let keyStore: String
    let python: String
    let providers: [String: ProviderInfo]

    struct ProviderInfo: Decodable {
        let model: String
        let effort: String
        let fallbacks: Bool?
        let keySource: String
        let sdkAvailable: Bool

        enum CodingKeys: String, CodingKey {
            case model, effort, fallbacks
            case keySource = "key_source"
            case sdkAvailable = "sdk_available"
        }
    }

    enum CodingKeys: String, CodingKey {
        case provider, mode, depth, python, providers
        case keyStore = "key_store"
    }
}

/// Ein Eintrag aus `officemd sync --json`.
struct SyncResult: Decodable, Identifiable {
    var id: String { file }
    let file: String
    let action: String
    let message: String
    let before: String?
    let after: String?
    let problems: [String]?

    var fileName: String { (file as NSString).lastPathComponent }
}
