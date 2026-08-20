// CUA Control — native macOS launcher for the OpenCode Computer-Use Platform.
//
// Double-click to launch. On startup the app:
//   1. finds the platform repo root (walk up from the bundle, or
//      CUA_PLATFORM_ROOT)
//   2. boots the control server (node frontend/server/index.js) unless
//      one is already serving on :8787 — the control server spawns
//      `opencode serve`, which in turn attaches the CUA Gateway and
//      Browser Adapter MCP servers
//   3. opens the full CUA control UI (chat, live screen, logs) in an
//      embedded WKWebView — no browser tab, no URL
//
// Toolbar: status indicator, START and STOP. STOP kills opencode + the
// gateway screen-capture processes instantly (process-group SIGKILL via
// the control server) and halts live screen capture the moment it's
// clicked. START brings everything back. Quitting the app stops all
// services too.

import AppKit
import WebKit

let CONTROL_URL = "http://127.0.0.1:8787"

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    var window: NSWindow!
    var webView: WKWebView!
    var controlTask: Process?
    var repoRoot: URL?
    var statusTimer: Timer?
    var statusField: NSTextField!
    var stopButton: NSButton!
    var startButton: NSButton!
    var uiLoaded = false

    // MARK: - lifecycle

    func applicationDidFinishLaunching(_ notification: Notification) {
        setupWindow()

        guard let root = findRepoRoot() else {
            presentFatal("Could not find the platform repo (frontend/server/index.js).\n\nSet CUA_PLATFORM_ROOT or keep CUA.app inside the platform repo.")
            return
        }
        repoRoot = root
        startServices()
        loadUI()
        pollStatusLoop()
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        cleanupServices()
        return .terminateNow
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    // MARK: - window + webview

    private func setupWindow() {
        let rect = NSRect(x: 0, y: 0, width: 1320, height: 860)
        window = NSWindow(contentRect: rect, styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "CUA Control"
        window.minSize = NSSize(width: 1000, height: 640)

        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        webView = WKWebView(frame: rect, configuration: config)
        webView.navigationDelegate = self
        webView.autoresizingMask = [.width, .height]

        let toolbar = NSToolbar(identifier: "CUA.Toolbar")
        toolbar.delegate = self
        toolbar.displayMode = .iconAndLabel
        toolbar.allowsUserCustomization = false
        window.toolbar = toolbar

        let container = NSView(frame: rect)
        webView.frame = container.bounds
        container.addSubview(webView)
        window.contentView = container
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func loadUI() {
        guard !uiLoaded else { return }
        uiLoaded = true
        let url = URL(string: CONTROL_URL)!
        webView.load(URLRequest(url: url))
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        // Control server still booting — retry shortly.
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
            if self.uiLoaded {
                self.webView.load(URLRequest(url: URL(string: CONTROL_URL)!))
            }
        }
    }

    func webView(
        _ webView: WKWebView,
        decidePolicyFor navigationAction: WKNavigationAction,
        decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
    ) {
        // Let the UI's "Open System Settings" button reach the Settings app.
        if let url = navigationAction.request.url,
           url.scheme == "x-apple.systempreferences" {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    // MARK: - services

    private func findRepoRoot() -> URL? {
        if let override = ProcessInfo.processInfo.environment["CUA_PLATFORM_ROOT"], !override.isEmpty {
            let url = URL(fileURLWithPath: override)
            if hasRepoMarker(url) { return url }
        }
        // walk up from the .app bundle
        var dir = Bundle.main.bundleURL
        for _ in 0..<8 {
            if hasRepoMarker(dir) { return dir }
            dir = dir.deletingLastPathComponent()
        }
        return nil
    }

    private func hasRepoMarker(_ dir: URL) -> Bool {
        let marker = dir.appendingPathComponent("frontend").appendingPathComponent("server").appendingPathComponent("index.js")
        return FileManager.default.fileExists(atPath: marker.path)
    }

    private func nodeBinary() -> String {
        for candidate in [
            "/opt/homebrew/bin/node",
            "/usr/local/bin/node",
            "/usr/bin/node",
        ] {
            if FileManager.default.isExecutableFile(atPath: candidate) {
                return candidate
            }
        }
        return "/usr/bin/env node"
    }

    private func serviceEnv() -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        let home = NSHomeDirectory()
        env["PATH"] = "\(home)/.opencode/bin:\(home)/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        env["PORT"] = "8787"
        return env
    }

    func startServices() {
        status("starting…")
        // Adopt an already-running control server if one is up.
        checkStatus { healthy in
            if healthy {
                DispatchQueue.main.async {
                    self.status("running")
                    self.loadUI()
                }
                return
            }
            DispatchQueue.main.async { self.spawnControlServer() }
        }
    }

    private func spawnControlServer() {
        guard let root = repoRoot else { return }
        let task = Process()
        task.executableURL = URL(fileURLWithPath: nodeBinary())
        task.arguments = ["server/index.js"]
        task.currentDirectoryURL = root.appendingPathComponent("frontend")
        task.environment = serviceEnv()

        // Persist control-server + opencode logs into the repo's logs/
        // directory so everything is inspectable after the fact.
        let logsDir = root.appendingPathComponent("logs", isDirectory: true)
        try? FileManager.default.createDirectory(at: logsDir, withIntermediateDirectories: true)
        let logURL = logsDir.appendingPathComponent("control-server.out")
        if let handle = try? FileHandle(forWritingTo: logURL) {
            let pipeOut = Pipe()
            let pipeErr = Pipe()
            task.standardOutput = pipeOut
            task.standardError = pipeErr
            let start = "===== CUA app launched \(Date()) =====\n".data(using: .utf8) ?? Data()
            handle.seekToEndOfFile()
            handle.write(start)
            for pipe in [pipeOut, pipeErr] {
                pipe.fileHandleForReading.readabilityHandler = { fh in
                    let data = fh.availableData
                    if data.isEmpty { return }
                    let s = "\(String(data: data, encoding: .utf8) ?? "")\n".data(using: .utf8) ?? data
                    handle.seekToEndOfFile()
                    handle.write(s)
                }
            }
        }

        task.terminationHandler = { proc in
            DispatchQueue.main.async {
                self.status("control server exited (code \(proc.terminationStatus))")
            }
        }
        do {
            try task.run()
            controlTask = task
        } catch {
            status("failed to start control server: \(error.localizedDescription)")
        }
    }

    private func cleanupServices() {
        // Graceful: control server's SIGTERM stops opencode (which kills
        // the gateway/browser process group). Fall back to direct kills.
        if let task = controlTask, task.isRunning {
            task.terminate()
            let pid = task.processIdentifier
            DispatchQueue.global().asyncAfter(deadline: .now() + 2.5) {
                kill(pid, SIGKILL)
            }
        }
        pkillPattern("opencode serve")
        pkillPattern("cua_gateway")
        pkillPattern("browser-adapter")
    }

    private func pkillPattern(_ pattern: String) {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/pkill")
        task.arguments = ["-9", "-f", pattern]
        try? task.run()
    }

    // MARK: - toolbar actions

    @objc func stopTapped() {
        stopButton.isEnabled = false
        startButton.isEnabled = false
        status("stopping…")
        postControl("stop") { ok in
            DispatchQueue.main.async {
                if !ok {
                    // Belt and suspenders: kill opencode + gateway directly.
                    self.pkillPattern("opencode serve")
                    self.pkillPattern("cua_gateway")
                }
                self.status("stopped")
                self.startButton.isEnabled = true
            }
        }
    }

    @objc func startTapped() {
        stopButton.isEnabled = false
        startButton.isEnabled = false
        status("starting…")
        postControl("start") { ok in
            DispatchQueue.main.async {
                if ok {
                    self.status("running")
                    self.stopButton.isEnabled = true
                    self.loadUI()
                } else {
                    self.startServices()
                }
            }
        }
    }

    // MARK: - status + helpers

    private func checkStatus(_ done: @escaping (Bool) -> Void) {
        guard let url = URL(string: "\(CONTROL_URL)/api/status") else { return done(false) }
        var request = URLRequest(url: url)
        request.timeoutInterval = 3
        URLSession.shared.dataTask(with: request) { data, _, error in
            if let data = data,
               let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let healthy = obj["healthy"] as? Bool {
                done(healthy)
            } else {
                done(false)
            }
        }.resume()
    }

    private func pollStatusLoop() {
        statusTimer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { _ in
            self.checkStatus { healthy in
                DispatchQueue.main.async {
                    if healthy {
                        self.status("running")
                        self.stopButton.isEnabled = true
                        self.startButton.isEnabled = false
                        self.loadUI()
                    } else {
                        self.stopButton.isEnabled = false
                        self.startButton.isEnabled = true
                    }
                }
            }
        }
    }

    private func postControl(_ path: String, _ done: @escaping (Bool) -> Void) {
        guard let url = URL(string: "\(CONTROL_URL)/api/\(path)") else { return done(false) }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.httpBody = "{}".data(using: .utf8)
        request.timeoutInterval = 20
        URLSession.shared.dataTask(with: request) { data, _, error in
            if let data = data,
               let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                done((obj["ok"] as? Bool) ?? false)
            } else {
                done(false)
            }
        }.resume()
    }

    private func status(_ text: String) {
        statusField?.stringValue = text
    }

    private func presentFatal(_ message: String) {
        let alert = NSAlert()
        alert.messageText = "CUA Control"
        alert.informativeText = message
        alert.runModal()
        NSApp.terminate(nil)
    }
}

// MARK: - toolbar

extension AppDelegate: NSToolbarDelegate {
    func toolbarDefaultItemIdentifiers(_ toolbar: NSToolbar) -> [NSToolbarItem.Identifier] {
        [.flexibleSpace, .statusItem, .stopItem, .startItem]
    }

    func toolbarAllowedItemIdentifiers(_ toolbar: NSToolbar) -> [NSToolbarItem.Identifier] {
        [.flexibleSpace, .statusItem, .stopItem, .startItem]
    }

    func toolbar(_ toolbar: NSToolbar, itemForItemIdentifier itemIdentifier: NSToolbarItem.Identifier, willBeInsertedIntoToolbar flag: Bool) -> NSToolbarItem? {
        switch itemIdentifier {
        case .statusItem:
            let item = NSToolbarItem(itemIdentifier: itemIdentifier)
            let field = NSTextField(labelWithString: "starting…")
            field.textColor = .secondaryLabelColor
            item.view = field
            item.label = "Status"
            statusField = field
            return item
        case .stopItem:
            let item = NSToolbarItem(itemIdentifier: itemIdentifier)
            let button = NSButton(title: "⏹ STOP", target: self, action: #selector(stopTapped))
            button.bezelStyle = .rounded
            button.contentTintColor = .systemRed
            item.view = button
            item.label = "Stop"
            stopButton = button
            return item
        case .startItem:
            let item = NSToolbarItem(itemIdentifier: itemIdentifier)
            let button = NSButton(title: "▶ START", target: self, action: #selector(startTapped))
            button.bezelStyle = .rounded
            button.contentTintColor = .systemGreen
            item.view = button
            item.label = "Start"
            startButton = button
            return item
        default:
            return nil
        }
    }
}

extension NSToolbarItem.Identifier {
    static let statusItem = NSToolbarItem.Identifier("status")
    static let stopItem = NSToolbarItem.Identifier("stop")
    static let startItem = NSToolbarItem.Identifier("start")
}

// MARK: - app entry

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
