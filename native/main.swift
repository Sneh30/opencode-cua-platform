// opencode-operator — native macOS launcher for the OpenCode Computer-Use
// Platform.
//
// Two launch modes:
//
// DISTRIBUTED (the bundle was produced by native/bundle.sh): the bundle
// embeds the whole platform (Contents/Resources/platform), vendored
// runtimes (Contents/Resources/vendor: node, uv, opencode, a managed
// CPython) and the GUI-agent model (Contents/Resources/model). On first
// launch — or whenever the installed copy is missing, stale or broken —
// the app runs native/provision.sh, streaming its output into a
// provisioning status window. Provisioning copies the platform into
// ~/Library/Application Support/opencode-operator/platform, installs the
// vendored runtimes next to it and patches the shipped virtualenvs to the
// installed Python. No network, no Homebrew, no manual setup. The 5 GB
// GUI-agent model is referenced straight from the bundle (CUA_MODEL_DIR),
// so it stays contained in the application itself.
//
// DEV (plain `native/build.sh` output): behaves like before — walks up
// from the bundle to find the platform repo (or honors CUA_PLATFORM_ROOT)
// and uses the machine's own node/opencode.
//
// After provisioning the app boots the control server (node
// frontend/server/index.js), which spawns `opencode serve`, which attaches
// the CUA Gateway and Browser Adapter MCP servers, and opens the control
// UI in an embedded WKWebView. Toolbar: status indicator, START, STOP.
// STOP kills the whole stack instantly; quitting the app stops everything.

import AppKit
import WebKit

let CONTROL_URL = "http://127.0.0.1:8787"

// ---------------------------------------------------------------------------
// Layout
// ---------------------------------------------------------------------------

/// Filesystem layout for the distributed bundle + its writable install.
struct DistributionLayout {
    let platformSrc: URL      // <bundle>/Contents/Resources/platform (read-only)
    let vendorBin: URL        // <bundle>/Contents/Resources/vendor/bin
    let vendorPython: URL     // <bundle>/Contents/Resources/vendor/python (root dir of managed installs)
    let modelSrc: URL?        // <bundle>/Contents/Resources/models/UI-TARS-1.5-7B-4bit
    let installRoot: URL      // ~/Library/Application Support/opencode-operator
    let installPlatform: URL  // …/platform
    let runtimeBin: URL       // …/runtime/bin   (node, uv, opencode)
    let runtimePython: URL    // …/runtime/python (managed CPython installs)

    static func resolve() -> DistributionLayout? {
        guard let res = Bundle.main.resourceURL else { return nil }
        let fm = FileManager.default
        let platformSrc = res.appendingPathComponent("platform", isDirectory: true)
        guard fm.fileExists(atPath: platformSrc.appendingPathComponent("frontend/server/index.js").path) else {
            return nil
        }
        let support = fm.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        let installRoot = support.appendingPathComponent("opencode-operator", isDirectory: true)
        let modelRel = res.appendingPathComponent("models/UI-TARS-1.5-7B-4bit", isDirectory: true)
        return DistributionLayout(
            platformSrc: platformSrc,
            vendorBin: res.appendingPathComponent("vendor/bin", isDirectory: true),
            vendorPython: res.appendingPathComponent("vendor/python", isDirectory: true),
            modelSrc: fm.fileExists(atPath: modelRel.appendingPathComponent("config.json").path) ? modelRel : nil,
            installRoot: installRoot,
            installPlatform: installRoot.appendingPathComponent("platform", isDirectory: true),
            runtimeBin: installRoot.appendingPathComponent("runtime/bin", isDirectory: true),
            runtimePython: installRoot.appendingPathComponent("runtime/python", isDirectory: true)
        )
    }

    /// The venv symlinks are patched to this interpreter at provision time.
    /// The managed install directory is named `cpython-3.14.<patch>…`.
    func runtimePythonBin() -> URL {
        let fm = FileManager.default
        if let entries = try? fm.contentsOfDirectory(atPath: runtimePython.path) {
            let dirs = entries.filter { $0.hasPrefix("cpython-3.14") }.sorted()
            if let dir = dirs.last {
                return runtimePython.appendingPathComponent(dir).appendingPathComponent("bin/python3")
            }
        }
        return runtimePython.appendingPathComponent("cpython-3.14/bin/python3")
    }

    /// True when the installed copy looks complete, current and healthy.
    func isProvisioned() -> Bool {
        let fm = FileManager.default
        guard fm.fileExists(atPath: installPlatform.appendingPathComponent("frontend/server/index.js").path),
              fm.fileExists(atPath: installPlatform.appendingPathComponent(".provisioned").path) else {
            return false
        }
        // Version drift between the bundle and the installed copy → refresh.
        let srcVersion = try? String(contentsOf: platformSrc.appendingPathComponent("VERSION"), encoding: .utf8)
        let dstVersion = try? String(contentsOf: installPlatform.appendingPathComponent("VERSION"), encoding: .utf8)
        if srcVersion != dstVersion { return false }
        // The venv interpreter symlink must point at the installed runtime.
        let link = installPlatform.appendingPathComponent("gateway/.venv/bin/python3")
        guard let target = try? fm.destinationOfSymbolicLink(atPath: link.path) else { return false }
        return URL(fileURLWithPath: target, relativeTo: link.deletingLastPathComponent())
            .standardizedFileURL.path == runtimePythonBin().standardizedFileURL.path
    }
}

// ---------------------------------------------------------------------------
// App delegate
// ---------------------------------------------------------------------------

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    var window: NSWindow!
    var webView: WKWebView!
    var container: NSView!
    var provisionView: NSView!
    var provisionLog: NSTextView!
    var provisionSpinner: NSProgressIndicator!
    var provisionStatus: NSTextField!
    var retryButton: NSButton!

    var controlTask: Process?
    var provisionTask: Process?
    var repoRoot: URL?                 // dev mode
    var dist: DistributionLayout?      // distributed mode
    var statusTimer: Timer?
    var statusField: NSTextField!
    var stopButton: NSButton!
    var startButton: NSButton!
    var uiLoaded = false
    var provisioning = false

    // MARK: - lifecycle

    func applicationDidFinishLaunching(_ notification: Notification) {
        setupWindow()

        dist = DistributionLayout.resolve()
        if dist == nil {
            guard let root = findRepoRoot() else {
                presentFatal("Could not find the platform.\n\nA distributed opencode-operator.app embeds it automatically; a dev build (native/build.sh) must live inside the platform repo, or CUA_PLATFORM_ROOT must point at one.")
                return
            }
            repoRoot = root
            startServices()
            loadUI()
            pollStatusLoop()
            return
        }

        if dist!.isProvisioned() {
            beginServing()
        } else {
            runProvisioning()
        }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        provisionTask?.terminate()
        cleanupServices()
        return .terminateNow
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    // MARK: - window + webview + provisioning view

    private func setupWindow() {
        let rect = NSRect(x: 0, y: 0, width: 1320, height: 860)
        window = NSWindow(contentRect: rect, styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "opencode-operator"
        window.minSize = NSSize(width: 1000, height: 640)

        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        webView = WKWebView(frame: rect, configuration: config)
        webView.navigationDelegate = self
        webView.autoresizingMask = [.width, .height]

        let toolbar = NSToolbar(identifier: "Operator.Toolbar")
        toolbar.delegate = self
        toolbar.displayMode = .iconAndLabel
        toolbar.allowsUserCustomization = false
        window.toolbar = toolbar

        container = NSView(frame: rect)
        webView.frame = container.bounds
        webView.isHidden = true
        container.addSubview(webView)
        container.addSubview(buildProvisionView(bounds: container.bounds))
        window.contentView = container
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func buildProvisionView(bounds: NSRect) -> NSView {
        let view = NSView(frame: bounds)
        view.autoresizingMask = [.width, .height]

        let icon = NSImageView(frame: NSRect(x: 0, y: 0, width: 96, height: 96))
        icon.image = NSApp.applicationIconImage
        icon.frame.origin = NSPoint(x: (bounds.width - 96) / 2, y: bounds.height - 140)
        icon.autoresizingMask = [.minXMargin, .maxXMargin]

        let title = NSTextField(labelWithString: "opencode-operator")
        title.font = .systemFont(ofSize: 22, weight: .semibold)
        title.sizeToFit()
        title.frame.origin = NSPoint(x: (bounds.width - title.frame.width) / 2, y: bounds.height - 170)
        title.autoresizingMask = [.minXMargin, .maxXMargin]

        provisionStatus = NSTextField(wrappingLabelWithString: "Preparing first launch…")
        provisionStatus.font = .systemFont(ofSize: 13)
        provisionStatus.textColor = .secondaryLabelColor
        provisionStatus.alignment = .center
        provisionStatus.frame = NSRect(x: 40, y: bounds.height - 210, width: bounds.width - 80, height: 34)
        provisionStatus.autoresizingMask = [.width, .minXMargin, .maxXMargin]

        let logScroll = NSScrollView(frame: NSRect(x: 60, y: 90, width: bounds.width - 120, height: bounds.height - 330))
        logScroll.autoresizingMask = [.width, .height]
        logScroll.hasVerticalScroller = true
        logScroll.borderType = .bezelBorder
        provisionLog = NSTextView(frame: NSRect(x: 0, y: 0, width: logScroll.contentSize.width, height: logScroll.contentSize.height))
        provisionLog.isEditable = false
        provisionLog.font = NSFont.monospacedSystemFont(ofSize: 11, weight: .regular)
        provisionLog.textContainer?.widthTracksTextView = true
        logScroll.documentView = provisionLog

        provisionSpinner = NSProgressIndicator(frame: NSRect(x: (bounds.width - 24) / 2, y: 52, width: 24, height: 24))
        provisionSpinner.style = .spinning
        provisionSpinner.autoresizingMask = [.minXMargin, .maxXMargin]

        retryButton = NSButton(title: "Retry", target: self, action: #selector(retryProvisionTapped))
        retryButton.bezelStyle = .rounded
        retryButton.frame = NSRect(x: (bounds.width - 80) / 2, y: 44, width: 80, height: 32)
        retryButton.isHidden = true
        retryButton.autoresizingMask = [.minXMargin, .maxXMargin]

        view.addSubview(icon)
        view.addSubview(title)
        view.addSubview(provisionStatus)
        view.addSubview(logScroll)
        view.addSubview(provisionSpinner)
        view.addSubview(retryButton)
        provisionView = view
        return view
    }

    private func appendProvisionLine(_ line: String) {
        let text = line + "\n"
        provisionLog.textStorage?.append(NSAttributedString(string: text, attributes: [
            .font: NSFont.monospacedSystemFont(ofSize: 11, weight: .regular),
            .foregroundColor: NSColor.labelColor,
        ]))
        provisionLog.scrollRangeToVisible(NSRange(location: (provisionLog.string as NSString).length, length: 0))
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
            if self.uiLoaded && !self.provisioning {
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

    // MARK: - provisioning flow

    @objc func retryProvisionTapped() {
        retryButton.isHidden = true
        provisionSpinner.startAnimation(nil)
        runProvisioning()
    }

    private func runProvisioning() {
        guard let dist = dist else { return }
        provisioning = true
        stopButton.isEnabled = false
        startButton.isEnabled = false
        webView.isHidden = true
        provisionView.isHidden = false
        provisionStatus.stringValue = "Installing the platform — this happens once and needs no internet…"
        provisionLog.textStorage?.setAttributedString(NSAttributedString(string: ""))
        provisionSpinner.startAnimation(nil)

        let fm = FileManager.default
        try? fm.createDirectory(at: dist.installRoot, withIntermediateDirectories: true)

        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/bin/bash")
        task.arguments = [Bundle.main.resourceURL!.appendingPathComponent("provision.sh").path]
        task.currentDirectoryURL = dist.installRoot
        task.environment = [
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": NSHomeDirectory(),
            "CUA_PLATFORM_SRC": dist.platformSrc.path,
            "CUA_INSTALL_DIR": dist.installRoot.path,
            "CUA_BIN_DIR": dist.vendorBin.path,
            "CUA_VENDOR_PYTHON": dist.vendorPython.path,
            "CUA_RUNTIME_BIN": dist.runtimeBin.path,
            "CUA_RUNTIME_PYTHON": dist.runtimePython.path,
            "CUA_MODEL_SRC": dist.modelSrc?.path ?? "",
        ]
        let pipe = Pipe()
        task.standardOutput = pipe
        task.standardError = pipe
        pipe.fileHandleForReading.readabilityHandler = { fh in
            let data = fh.availableData
            guard !data.isEmpty, let s = String(data: data, encoding: .utf8) else { return }
            DispatchQueue.main.async {
                for line in s.split(separator: "\n") where !line.isEmpty {
                    self.appendProvisionLine(String(line))
                }
            }
        }
        task.terminationHandler = { proc in
            DispatchQueue.main.async {
                self.provisioning = false
                self.provisionSpinner.stopAnimation(nil)
                if proc.terminationStatus == 0 && self.dist?.isProvisioned() == true {
                    self.beginServing()
                } else {
                    self.provisionStatus.stringValue = "Setup failed (\(proc.terminationStatus)). See the log above — fix the issue and press Retry."
                    self.retryButton.isHidden = false
                }
            }
        }
        do {
            try task.run()
            provisionTask = task
        } catch {
            provisioning = false
            provisionSpinner.stopAnimation(nil)
            provisionStatus.stringValue = "Could not run the installer: \(error.localizedDescription)"
            retryButton.isHidden = false
        }
    }

    /// Provisioning done (or not needed): flip to the control UI and boot
    /// the service stack against the installed platform.
    private func beginServing() {
        provisionView.isHidden = true
        webView.isHidden = false
        startServices()
        loadUI()
        pollStatusLoop()
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
        // Distributed mode: the node runtime installed next to the platform.
        if let dist = dist {
            let vendored = dist.runtimeBin.appendingPathComponent("node").path
            if FileManager.default.isExecutableFile(atPath: vendored) { return vendored }
        }
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

    private func platformRoot() -> URL? {
        if let dist = dist { return dist.installPlatform }
        return repoRoot
    }

    private func serviceEnv() -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        let home = NSHomeDirectory()
        var path = "\(home)/.opencode/bin:\(home)/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        if let dist = dist {
            // Vendored runtimes win: the app must not depend on whatever
            // happens to be installed on this Mac.
            path = "\(dist.runtimeBin.path):\(dist.vendorBin.path):\(path)"
            env["OPENCODE_BIN"] = dist.runtimeBin.appendingPathComponent("opencode").path
            if let model = dist.modelSrc {
                // The GUI-agent model stays inside the application bundle;
                // the gateway loads it read-only from there.
                env["CUA_MODEL_DIR"] = model.path
            }
        }
        env["PATH"] = path
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
        guard let root = platformRoot() else { return }
        let task = Process()
        task.executableURL = URL(fileURLWithPath: nodeBinary())
        task.arguments = ["server/index.js"]
        task.currentDirectoryURL = root.appendingPathComponent("frontend")
        task.environment = serviceEnv()

        // Persist control-server + opencode logs into the platform's logs/
        // directory so everything is inspectable after the fact.
        let logsDir = root.appendingPathComponent("logs", isDirectory: true)
        try? FileManager.default.createDirectory(at: logsDir, withIntermediateDirectories: true)
        let logURL = logsDir.appendingPathComponent("control-server.out")
        if let handle = try? FileHandle(forWritingTo: logURL) {
            let pipeOut = Pipe()
            let pipeErr = Pipe()
            task.standardOutput = pipeOut
            task.standardError = pipeErr
            let start = "===== opencode-operator launched \(Date()) =====\n".data(using: .utf8) ?? Data()
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
        pkillPattern("cua-gateway")
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
                    self.pkillPattern("cua-gateway")
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
            guard !self.provisioning else { return }
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
        alert.messageText = "opencode-operator"
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
