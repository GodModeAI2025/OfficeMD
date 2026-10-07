import Foundation

/// Ruft das Python-CLI `officemd` auf. Die App enthält keine eigene Logik für Office-Pakete,
/// damit Verhalten und Tests an einer Stelle bleiben.
struct CLI {
    let executable: String

    /// CLI laut Einstellungen, sonst automatisch gesucht.
    static func current() -> CLI {
        let stored = UserDefaults.standard.string(forKey: "cliPath") ?? ""
        return CLI(executable: stored.isEmpty ? guessExecutable() : stored)
    }

    /// Sucht `officemd`: zuerst im App-Bundle (eingebettetes Python), dann neben dem
    /// Repository vom App-Binary aufwärts, dann im PATH.
    static func guessExecutable() -> String {
        if let bundled = Bundle.main.resourceURL?.appendingPathComponent("bin/officemd"),
           FileManager.default.isExecutableFile(atPath: bundled.path) {
            return bundled.path
        }
        var url = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath()
        for _ in 0..<8 {
            url.deleteLastPathComponent()
            let candidate = url.appendingPathComponent("officemd")
            var isDir: ObjCBool = false
            if FileManager.default.fileExists(atPath: candidate.path, isDirectory: &isDir), !isDir.boolValue {
                return candidate.path
            }
        }
        for dir in ["/opt/homebrew/bin", "/usr/local/bin", NSHomeDirectory() + "/.local/bin"] {
            let candidate = dir + "/officemd"
            if FileManager.default.isExecutableFile(atPath: candidate) { return candidate }
        }
        return "officemd"
    }

    func run(_ arguments: [String], stdin: String? = nil) async throws -> (status: Int32, stdout: String, stderr: String) {
        guard executable.contains("/") == false || FileManager.default.fileExists(atPath: executable) else {
            throw CLIError.notFound(executable)
        }
        return try await withCheckedThrowingContinuation { continuation in
            let process = Process()
            if executable.contains("/") {
                process.executableURL = URL(fileURLWithPath: executable)
                process.arguments = arguments
            } else {
                process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
                process.arguments = [executable] + arguments
            }
            let out = Pipe()
            let err = Pipe()
            process.standardOutput = out
            process.standardError = err
            let input = Pipe()
            process.standardInput = input
            process.terminationHandler = { proc in
                let stdout = String(data: out.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
                let stderr = String(data: err.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
                continuation.resume(returning: (proc.terminationStatus, stdout, stderr))
            }
            do {
                try process.run()
                // Geheimnisse (API-Keys) gehen nur über stdin, nie als Argument.
                if let stdin, let data = stdin.data(using: .utf8) {
                    input.fileHandleForWriting.write(data)
                }
                try? input.fileHandleForWriting.close()
            } catch {
                continuation.resume(throwing: error)
            }
        }
    }

    func check(_ paths: [String]) async throws -> [FileReport] {
        let result = try await run(["check", "--json"] + paths)
        guard let data = result.stdout.data(using: .utf8), !result.stdout.isEmpty else {
            throw CLIError.failed(result.stderr.isEmpty ? "Keine Ausgabe von officemd" : result.stderr)
        }
        let decoder = JSONDecoder()
        if let list = try? decoder.decode([FileReport].self, from: data) { return list }
        return [try decoder.decode(FileReport.self, from: data)]
    }

    func config() async throws -> AIConfig {
        let result = try await run(["config", "show", "--json"])
        guard result.status == 0, let data = result.stdout.data(using: .utf8) else {
            throw CLIError.failed(result.stderr.isEmpty ? "Konfiguration nicht lesbar" : result.stderr)
        }
        return try JSONDecoder().decode(AIConfig.self, from: data)
    }

    /// Prüft und aktualisiert bei Bedarf; kann mit KI mehrere Minuten dauern.
    func sync(_ paths: [String], raw: Bool = false) async throws -> [SyncResult] {
        let result = try await run(["sync", "--json"] + (raw ? ["--raw"] : []) + paths)
        guard let data = result.stdout.data(using: .utf8), !result.stdout.isEmpty else {
            throw CLIError.failed(result.stderr.isEmpty ? "Keine Ausgabe von officemd sync" : result.stderr)
        }
        return try JSONDecoder().decode([SyncResult].self, from: data)
    }

    /// Führt einen schreibenden Befehl aus und liefert dessen Meldung.
    func action(_ arguments: [String], stdin: String? = nil) async throws -> String {
        let result = try await run(arguments, stdin: stdin)
        if result.status != 0 {
            throw CLIError.failed(result.stderr.isEmpty ? result.stdout : result.stderr)
        }
        return result.stdout
    }
}
