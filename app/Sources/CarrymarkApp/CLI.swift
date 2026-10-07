import Foundation

/// Ruft das Python-CLI `carrymark` auf. Die App enthält keine eigene Logik für Office-Pakete,
/// damit Verhalten und Tests an einer Stelle bleiben.
struct CLI {
    let executable: String

    /// CLI laut Einstellungen, sonst automatisch gesucht.
    static func current() -> CLI {
        let stored = UserDefaults.standard.string(forKey: "cliPath") ?? ""
        return CLI(executable: stored.isEmpty ? guessExecutable() : stored)
    }

    /// Sucht `carrymark`: zuerst im App-Bundle (eingebettetes Python), dann neben dem
    /// Repository vom App-Binary aufwärts, dann im PATH.
    static func guessExecutable() -> String {
        if let bundled = Bundle.main.resourceURL?.appendingPathComponent("bin/carrymark"),
           FileManager.default.isExecutableFile(atPath: bundled.path) {
            return bundled.path
        }
        var url = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath()
        for _ in 0..<8 {
            url.deleteLastPathComponent()
            let candidate = url.appendingPathComponent("carrymark")
            var isDir: ObjCBool = false
            if FileManager.default.fileExists(atPath: candidate.path, isDirectory: &isDir), !isDir.boolValue {
                return candidate.path
            }
        }
        for dir in ["/opt/homebrew/bin", "/usr/local/bin", NSHomeDirectory() + "/.local/bin"] {
            let candidate = dir + "/carrymark"
            if FileManager.default.isExecutableFile(atPath: candidate) { return candidate }
        }
        return "carrymark"
    }

    func run(_ arguments: [String]) async throws -> (status: Int32, stdout: String, stderr: String) {
        guard executable.contains("/") == false || FileManager.default.fileExists(atPath: executable) else {
            throw CLIError.notFound(executable)
        }
        let executable = self.executable
        return try await Task.detached(priority: .userInitiated) {
            try Self.runBlocking(executable, arguments)
        }.value
    }

    /// Synchron, läuft im Hintergrund-Task. Nicht vom Main-Thread aufrufen.
    private static func runBlocking(_ executable: String,
                                    _ arguments: [String]) throws -> (status: Int32, stdout: String, stderr: String) {
        let process = Process()
        if executable.contains("/") {
            process.executableURL = URL(fileURLWithPath: executable)
            process.arguments = arguments
        } else {
            process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
            process.arguments = [executable] + arguments
        }
        let out = Pipe(), err = Pipe()
        process.standardOutput = out
        process.standardError = err
        process.standardInput = FileHandle.nullDevice
        try process.run()
        // Beide Pipes lesen, während der Prozess läuft. Wer erst nach dem Ende liest,
        // blockiert das CLI, sobald die Ausgabe größer als der Pipe-Puffer (64 KB) ist.
        var stderrData = Data()
        let group = DispatchGroup()
        group.enter()
        DispatchQueue.global().async {
            stderrData = err.fileHandleForReading.readDataToEndOfFile()
            group.leave()
        }
        let stdoutData = out.fileHandleForReading.readDataToEndOfFile()
        group.wait()
        process.waitUntilExit()
        return (process.terminationStatus,
                String(decoding: stdoutData, as: UTF8.self),
                String(decoding: stderrData, as: UTF8.self))
    }

    /// Ruft einen `--json`-Befehl auf und dekodiert die Ausgabe (Liste oder einzelnes Objekt).
    private func decodeList<T: Decodable>(_ arguments: [String]) async throws -> [T] {
        let result = try await run(arguments)
        let data = Data(result.stdout.utf8)
        guard !data.isEmpty else {
            throw CLIError.failed(result.stderr.isEmpty ? "Keine Ausgabe von carrymark" : result.stderr)
        }
        let decoder = JSONDecoder()
        if let list = try? decoder.decode([T].self, from: data) { return list }
        return [try decoder.decode(T.self, from: data)]
    }

    func check(_ paths: [String]) async throws -> [FileReport] {
        try await decodeList(["check", "--json"] + paths)
    }

    /// Prüft und bettet bei Bedarf neu ein oder stellt wieder her. Läuft lokal.
    func sync(_ paths: [String]) async throws -> [SyncResult] {
        try await decodeList(["sync", "--json"] + paths)
    }

    /// Führt einen Befehl aus und liefert dessen Ausgabe.
    func action(_ arguments: [String]) async throws -> String {
        let result = try await run(arguments)
        if result.status != 0 {
            throw CLIError.failed(result.stderr.isEmpty ? result.stdout : result.stderr)
        }
        return result.stdout
    }
}
