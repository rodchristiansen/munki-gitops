#!/usr/bin/env swift
//
//  MunkiRepoSwitcher.swift
//
//  Sample Swift pre-flight that rewrites SoftwareRepoURL
//  depending on which on-prem HTTP repo responds.
//
//  • Universal (arm64 + x86_64) build:  swift build -c release --arch arm64 --arch x86_64
//

import Foundation

// ────────── Constants ──────────
let logFile                = "/Library/Managed Installs/Logs/ManagedSoftwareUpdate.log"
let managedInstallsPlist   = "/Library/Preferences/ManagedInstalls.plist"

// Default → falls back to cloud
let cloudRepo              = "https://munki.example.org/munki"

// Candidate on-prem mirrors (first reachable wins)
let onPremRepos            = [
    "http://mainserver.example.org/munki",
    "http://redundantserver.example.org/munki"
]

// Replace with your own catalogue names/regex if you want finer control
let restrictToCatalogs     = #"Curriculum|Faculty|Kiosk|Testing"#

// ────────── Logger ──────────
struct Log {
    private static let handle: FileHandle = {
        if !FileManager.default.fileExists(atPath: logFile) {
            FileManager.default.createFile(atPath: logFile, contents: nil)
        }
        let h = try! FileHandle(forWritingTo: URL(fileURLWithPath: logFile))
        h.seekToEndOfFile()
        return h
    }()
    static func msg(_ lvl: String, _ m: String) {
        let ts = ISO8601DateFormatter().string(from: .init())
        let line = "\(ts) - \(lvl) - \(m)\n"
        if let d = line.data(using: .utf8) {
            handle.write(d); FileHandle.standardOutput.write(d)
        }
    }
    static func info(_ m:String) { msg("INFO", m) }
    static func err (_ m:String) { msg("ERROR",m) }
}

// ────────── Shell helper ──────────
@discardableResult
func sh(_ cmd:String, _ args:String...) throws -> String {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: cmd)
    p.arguments = args
    let pipe = Pipe(); p.standardOutput = pipe; p.standardError = pipe
    try p.run(); p.waitUntilExit()
    return String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8)?
        .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
}

// ────────── Plist helpers ──────────
func loadPlist(_ path:String) -> [String:Any] {
    guard let d = FileManager.default.contents(atPath: path),
          let o = try? PropertyListSerialization.propertyList(from: d, format: nil) as? [String:Any] else { return [:] }
    return o
}
func savePlist(_ path: String, _ dict: [String:Any]) {
    guard let d = try? PropertyListSerialization.data(fromPropertyList: dict,
                                                      format: .xml,
                                                      options: 0) else { return }
    let tmp = path + ".tmp"
    FileManager.default.createFile(atPath: tmp, contents: d)
    try? FileManager.default.removeItem(atPath: path)
    try? FileManager.default.moveItem(atPath: tmp, toPath: path)
}

// ────────── Helpers ──────────
func machineType() -> String {
    if let out = try? sh("/usr/sbin/system_profiler", "SPPowerDataType"),
       out.contains("Battery Power") {
        return "laptop"
    }
    return "desktop"
}

// ────────── URLSession sync HEAD helper ──────────
extension URLSession {
    private final class Flag: @unchecked Sendable { var ok = false }

    func headOK(_ url: URL, timeout: TimeInterval = 3) -> Bool {
        var req = URLRequest(url: url); req.httpMethod = "HEAD"
        let sem = DispatchSemaphore(value: 0)
        let flag = Flag()

        let task = dataTask(with: req) { _, resp, _ in
            flag.ok = (resp as? HTTPURLResponse)?.statusCode == 200
            sem.signal()
        }
        task.resume()
        if sem.wait(timeout: .now() + timeout) == .timedOut { task.cancel() }
        return flag.ok
    }
}

// ────────── Main ──────────
guard geteuid() == 0 else { Log.err("Must run as root"); exit(1) }
Log.info("Repo switcher started")

var mi = loadPlist(managedInstallsPlist)

// Optional: only rewrite for specific catalog patterns
if let catalog = mi["Catalogs"] as? [String],
   catalog.joined(separator:",").range(of: restrictToCatalogs, options: .regularExpression) == nil {
    Log.info("Catalog \(catalog) not in scope – leaving repo unchanged"); exit(0)
}

// Optional: only desktops switch to on-prem
if machineType() == "laptop" {
    Log.info("Laptop detected – forcing cloud repo")
    mi["SoftwareRepoURL"] = cloudRepo; savePlist(managedInstallsPlist, mi); exit(0)
}

// Test on-prem mirrors
let session = URLSession(configuration: .ephemeral)
var selected = cloudRepo

for r in onPremRepos {
    if let u = URL(string: r), session.headOK(u) {
        selected = r; break
    }
}

mi["SoftwareRepoURL"] = selected
savePlist(managedInstallsPlist, mi)

Log.info("SoftwareRepoURL → \(selected)")
Log.info("Repo switcher completed")