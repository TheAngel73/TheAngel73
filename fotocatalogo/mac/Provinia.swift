// Provinia.app (ex FotoCatalogo) — finestra nativa per macOS.
//
// Avvia il motore Python (server.py) in background, mostra l'interfaccia in una
// finestra WebKit e fornisce la barra dei menu di macOS. Alla chiusura dell'app
// il motore viene fermato. Si compila con mac/crea_app.sh.

import Cocoa
import WebKit

let port = 8765
let baseURL = URL(string: "http://localhost:\(port)/")!
let logPath = (NSHomeDirectory() as NSString).appendingPathComponent("Library/Logs/Provinia.log")

func jsString(_ value: String?) -> String {
    guard let value = value,
          let data = try? JSONSerialization.data(withJSONObject: [value]),
          let s = String(data: data, encoding: .utf8) else { return "null" }
    return String(s.dropFirst().dropLast())   // ["…"] -> "…"
}

// MARK: trascinare le foto = trascinare i file veri
//
// Nell'app la pagina non usa il trascinamento di WebKit per le foto: all'inizio del
// trascinamento manda qui gli id ("drag"), noi chiediamo al motore i percorsi e avviamo
// un trascinamento nativo di macOS con i file. Così:
// - fuori dall'app (Finder, scrivania, Mail…) si può solo COPIARE: gli originali restano;
// - dentro la finestra la pagina lo riceve come "Files" e, sapendo di averlo iniziato lei,
//   lo tratta come le sue foto (sposta su una cartella, aggiunge a una raccolta).

// Sorgente del trascinamento: un oggetto a parte, per non interferire con i
// trascinamenti che WebKit gestisce da sé (es. le raccolte nella barra laterale).
final class FileDragSource: NSObject, NSDraggingSource {
    weak var view: FCWebView?

    func draggingSession(_ session: NSDraggingSession,
                         sourceOperationMaskFor context: NSDraggingContext) -> NSDragOperation {
        context == .outsideApplication ? .copy : [.copy, .move, .generic]
    }

    func draggingSession(_ session: NSDraggingSession, endedAt screenPoint: NSPoint, operation: NSDragOperation) {
        // WebKit non ha visto il rilascio del tasto (lo ha gestito il trascinamento):
        // glielo diamo, altrimenti crederebbe il tasto ancora premuto
        if let v = view, let win = v.window, let up = NSEvent.mouseEvent(with: .leftMouseUp,
                location: win.mouseLocationOutsideOfEventStream, modifierFlags: [],
                timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: win.windowNumber,
                context: nil, eventNumber: 0, clickCount: 1, pressure: 0) {
            v.mouseUp(with: up)
        }
        view?.onDragEnd?()
    }
}

final class FCWebView: WKWebView {
    // il primo clic su una finestra non attiva agisce subito (non serve cliccare due volte)
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    var onDragEnd: (() -> Void)?
    let dragSource = FileDragSource()

    func startFileDrag(paths: [String]) {
        guard let win = window, !paths.isEmpty else { onDragEnd?(); return }
        // il tasto è stato già rilasciato mentre aspettavamo i percorsi: niente da fare
        if NSEvent.pressedMouseButtons & 1 == 0 { onDragEnd?(); return }
        let loc = win.mouseLocationOutsideOfEventStream
        var event = NSApp.currentEvent
        if event == nil || ![.leftMouseDragged, .leftMouseDown].contains(event!.type) {
            event = NSEvent.mouseEvent(with: .leftMouseDragged, location: loc, modifierFlags: [],
                                       timestamp: ProcessInfo.processInfo.systemUptime,
                                       windowNumber: win.windowNumber, context: nil,
                                       eventNumber: 0, clickCount: 1, pressure: 1)
        }
        guard let ev = event else { onDragEnd?(); return }
        let p = convert(loc, from: nil)
        var items: [NSDraggingItem] = []
        for (i, path) in paths.enumerated() {
            let item = NSDraggingItem(pasteboardWriter: URL(fileURLWithPath: path) as NSURL)
            let off = CGFloat(min(i, 4)) * 4
            let frame = NSRect(x: p.x - 32 + off, y: p.y - 32 - off, width: 64, height: 64)
            if i < 5 {
                let icon = NSWorkspace.shared.icon(forFile: path)
                icon.size = NSSize(width: 64, height: 64)
                item.setDraggingFrame(frame, contents: icon)
            } else {
                item.setDraggingFrame(frame, contents: nil)
            }
            items.append(item)
        }
        dragSource.view = self
        let session = beginDraggingSession(with: items, event: ev, source: dragSource)
        session.animatesToStartingPositionsOnCancelOrFail = true
        session.draggingFormation = .pile
    }

    func requestDrag(ids: [Int]) {
        fetchPaths(ids: ids) { paths in self.startFileDrag(paths: paths) }
    }
}

/// Percorsi dei file esistenti per queste foto, chiesti al motore (risposta sul thread principale).
func fetchPaths(ids: [Int], _ done: @escaping ([String]) -> Void) {
    var req = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/api/paths")!)
    req.httpMethod = "POST"
    req.setValue("application/json", forHTTPHeaderField: "Content-Type")
    req.httpBody = try? JSONSerialization.data(withJSONObject: ["ids": ids])
    req.timeoutInterval = 10
    URLSession.shared.dataTask(with: req) { data, _, _ in
        var paths: [String] = []
        if let data = data, let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            paths = obj["paths"] as? [String] ?? []
        }
        DispatchQueue.main.async { done(paths) }
    }.resume()
}

// MARK: ⌘C — copiare le foto negli appunti
//
// Negli appunti vanno i file (come ⌘C nel Finder): si incollano nel Finder, in Mail,
// Messaggi, WhatsApp… Con una sola foto si offre anche l'immagine, a piena risoluzione,
// per le app che incollano immagini (es. Photoshop): viene preparata solo se un'app la
// chiede davvero, perché per un RAW richiede qualche secondo.
final class ClipboardImageProvider: NSObject, NSPasteboardItemDataProvider {
    static let jpeg = NSPasteboard.PasteboardType("public.jpeg")
    let photoId: Int
    init(photoId: Int) { self.photoId = photoId }

    func pasteboard(_ pasteboard: NSPasteboard?, item: NSPasteboardItem,
                    provideDataForType type: NSPasteboard.PasteboardType) {
        var jpegData: Data?
        let sem = DispatchSemaphore(value: 0)
        var req = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/full/\(photoId)")!)
        req.timeoutInterval = 120
        URLSession.shared.dataTask(with: req) { data, resp, _ in
            if (resp as? HTTPURLResponse)?.statusCode == 200 { jpegData = data }
            sem.signal()
        }.resume()
        _ = sem.wait(timeout: .now() + 125)
        guard let data = jpegData else { return }
        if type == ClipboardImageProvider.jpeg {
            if data.starts(with: [0xFF, 0xD8, 0xFF]) { item.setData(data, forType: type) }
            else if let rep = NSBitmapImageRep(data: data),
                    let j = rep.representation(using: .jpeg, properties: [.compressionFactor: 0.95]) {
                item.setData(j, forType: type)
            }
        } else if type == .tiff, let rep = NSBitmapImageRep(data: data), let t = rep.tiffRepresentation {
            item.setData(t, forType: .tiff)
        }
    }

    func pasteboardFinishedWithDataProvider(_ pasteboard: NSPasteboard) {}
}

let videoExtensions: Set<String> = ["mov", "mp4", "m4v", "avi", "mkv", "mts", "m2ts", "3gp", "mpg", "mpeg", "wmv", "webm"]

// MARK: schermata di avvio e «Informazioni su Provinia»
//
// L'immagine (Resources/about.png, 1536×1024 @2x, bordi trasparenti con la finestra
// già disegnata) si mostra in una finestra senza bordi. All'avvio resta finché
// l'interfaccia è pronta (almeno 1,5 s); un clic o un tasto la chiudono.
final class SplashWindow: NSWindow {
    override var canBecomeKey: Bool { true }
}

final class Splash: NSObject {
    var window: NSWindow?
    weak var parent: NSWindow?
    var monitor: Any?
    var shownAt = Date()

    var isShown: Bool { window != nil }

    func show(over parent: NSWindow?, about: Bool) {
        if let w = window { w.makeKeyAndOrderFront(nil); return }
        self.parent = parent
        guard let url = Bundle.main.url(forResource: "about", withExtension: "png"),
              let img = NSImage(contentsOf: url) else { return }
        let size = NSSize(width: 768, height: 512)
        img.size = size
        let w = SplashWindow(contentRect: NSRect(origin: .zero, size: size), styleMask: [.borderless],
                             backing: .buffered, defer: false)
        w.isOpaque = false
        w.backgroundColor = .clear
        w.hasShadow = true
        w.isReleasedWhenClosed = false
        w.level = .floating
        let iv = NSImageView(frame: NSRect(origin: .zero, size: size))
        iv.image = img
        iv.imageScaling = .scaleProportionallyUpOrDown
        let content = NSView(frame: iv.frame)
        content.addSubview(iv)
        if about {
            let v = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? ""
            let label = NSTextField(labelWithString: "Versione \(v)")
            label.font = NSFont.systemFont(ofSize: 10)
            label.textColor = NSColor(calibratedRed: 0.45, green: 0.41, blue: 0.37, alpha: 1)
            label.frame = NSRect(x: 43, y: 58, width: 300, height: 14)
            content.addSubview(label)
        }
        w.contentView = content
        if let p = parent, p.isVisible {
            w.setFrameOrigin(NSPoint(x: p.frame.midX - size.width / 2, y: p.frame.midY - size.height / 2))
        } else {
            w.center()
        }
        w.alphaValue = 0
        w.makeKeyAndOrderFront(nil)
        w.invalidateShadow()
        NSAnimationContext.runAnimationGroup { $0.duration = 0.2; w.animator().alphaValue = 1 }
        window = w
        shownAt = Date()
        monitor = NSEvent.addLocalMonitorForEvents(matching: [.leftMouseDown, .keyDown]) { [weak self] e in
            guard let self = self, self.window != nil else { return e }
            let onSplash = e.window === self.window
            self.close()
            return onSplash ? nil : e
        }
    }

    /// Chiude con dissolvenza, ma non prima di `minimum` secondi dalla comparsa.
    func close(minimum: TimeInterval = 0) {
        guard let w = window else { return }
        let wait = minimum - Date().timeIntervalSince(shownAt)
        if wait > 0 {
            DispatchQueue.main.asyncAfter(deadline: .now() + wait) { [weak self] in self?.close() }
            return
        }
        window = nil
        if let m = monitor { NSEvent.removeMonitor(m); monitor = nil }
        NSAnimationContext.runAnimationGroup({ $0.duration = 0.3; w.animator().alphaValue = 0 },
                                             completionHandler: { [weak self] in
                                                 w.orderOut(nil)
                                                 // la finestra principale torna a ricevere clic e tasti
                                                 self?.parent?.makeKeyAndOrderFront(nil)
                                             })
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate,
                         WKScriptMessageHandler, WKNavigationDelegate, WKUIDelegate {
    var window: NSWindow!
    var webView: WKWebView!
    var server: Process?
    var quitting = false
    var titleObserver: NSKeyValueObservation?
    var clipboardProvider: ClipboardImageProvider?
    let splash = Splash()
    var startupSplash = true   // tenuto in vita finché servono gli appunti

    // MARK: avvio

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMainMenu()

        let config = WKWebViewConfiguration()
        config.userContentController.add(self, name: "fc")
        webView = FCWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.allowsBackForwardNavigationGestures = false
        if #available(macOS 13.3, *) { webView.isInspectable = true }
        // il titolo della finestra mostra il catalogo aperto
        titleObserver = webView.observe(\.title, options: [.new]) { [weak self] wv, _ in
            guard let self = self else { return }
            let t = wv.title ?? ""
            self.window?.title = (t.isEmpty || t == "Provinia") ? "Provinia" : "Provinia — \(t)"
        }

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1440, height: 900),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "Provinia"
        window.appearance = NSAppearance(named: .darkAqua)
        window.backgroundColor = NSColor(calibratedWhite: 0.12, alpha: 1)
        window.minSize = NSSize(width: 960, height: 600)
        window.collectionBehavior = [.fullScreenPrimary]
        // La pagina sta SOTTO la barra del titolo (contentLayoutGuide): con le versioni più
        // recenti di macOS il contenuto può finire dietro la barra, che ne ruba i clic
        // (es. il pulsante «Libreria» in alto).
        let container = NSView()
        container.wantsLayer = true
        container.layer?.backgroundColor = NSColor(calibratedWhite: 0.12, alpha: 1).cgColor
        window.contentView = container
        webView.translatesAutoresizingMaskIntoConstraints = false
        container.addSubview(webView)
        let guide = window.contentLayoutGuide as? NSLayoutGuide
        NSLayoutConstraint.activate([
            webView.topAnchor.constraint(equalTo: guide?.topAnchor ?? container.topAnchor),
            webView.leadingAnchor.constraint(equalTo: container.leadingAnchor),
            webView.trailingAnchor.constraint(equalTo: container.trailingAnchor),
            webView.bottomAnchor.constraint(equalTo: container.bottomAnchor),
        ])
        window.delegate = self
        window.center()
        window.setFrameAutosaveName("FotoCatalogoFinestra")   // ricorda posizione e dimensioni (nome storico)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        showMessage("Avvio in corso…")
        splash.show(over: window, about: false)
        // in ogni caso non resta più di 10 secondi
        DispatchQueue.main.asyncAfter(deadline: .now() + 10) { self.endStartupSplash() }
        startServer()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        window.makeKeyAndOrderFront(nil)
        return true
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if server != nil && runningJobs() > 0 {
            let alert = NSAlert()
            alert.messageText = "Ci sono lavori in corso"
            alert.informativeText = "Un'importazione, una sincronizzazione o la generazione delle anteprime non è ancora finita. Se esci verrà interrotta (potrai riprenderla in seguito)."
            alert.addButton(withTitle: "Esci comunque")
            alert.addButton(withTitle: "Annulla")
            if alert.runModal() != .alertFirstButtonReturn { return .terminateCancel }
        }
        return .terminateNow
    }

    func applicationWillTerminate(_ notification: Notification) {
        quitting = true
        server?.terminate()
    }

    // MARK: motore Python

    func checkServer(_ done: @escaping (Bool) -> Void) {
        var req = URLRequest(url: baseURL.appendingPathComponent("api/jobs"))
        req.timeoutInterval = 1
        URLSession.shared.dataTask(with: req) { _, resp, _ in
            let ok = (resp as? HTTPURLResponse)?.statusCode == 200
            DispatchQueue.main.async { done(ok) }
        }.resume()
    }

    func startServer() {
        checkServer { alive in
            if alive { self.loadApp(); return }   // già acceso (es. avviato dal .command)
            guard let script = Bundle.main.resourceURL?.appendingPathComponent("app/server.py") else { return }
            let p = Process()
            var env = ProcessInfo.processInfo.environment
            // Python dentro l'app (versione da condividere, costruita su GitHub); altrimenti
            // quello del Mac (app creata in locale con «Crea app»)
            let pyDir = Bundle.main.resourceURL!.appendingPathComponent("python")
            let bundled = pyDir.appendingPathComponent("bin/python3")
            if FileManager.default.isExecutableFile(atPath: bundled.path) {
                // scaricata da internet: si toglie la «quarantena» al Python interno, se si può
                let x = Process()
                x.executableURL = URL(fileURLWithPath: "/usr/bin/xattr")
                x.arguments = ["-dr", "com.apple.quarantine", pyDir.path]
                if (try? x.run()) != nil { x.waitUntilExit() }
                p.executableURL = bundled
                p.arguments = [script.path, "--no-browser"]
                env["PYTHONNOUSERSITE"] = "1"
            } else {
                p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
                p.arguments = ["python3", script.path, "--no-browser"]
            }
            env["PATH"] = "/usr/bin:/opt/homebrew/bin:/usr/local/bin:/bin:" + (env["PATH"] ?? "")
            env["FOTOCAT_PORT"] = String(port)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            env["PYTHONUNBUFFERED"] = "1"
            p.environment = env
            FileManager.default.createFile(atPath: logPath, contents: nil)
            if let h = FileHandle(forWritingAtPath: logPath) {
                p.standardOutput = h
                p.standardError = h
            }
            p.terminationHandler = { proc in
                let status = proc.terminationStatus
                DispatchQueue.main.async { self.serverStopped(status) }
            }
            do {
                try p.run()
                self.server = p
                self.waitForServer(attempt: 0)
            } catch {
                self.showMessage("Impossibile avviare Python 3.<br>Apri il Terminale ed esegui <code>xcode-select --install</code>.")
            }
        }
    }

    func waitForServer(attempt: Int) {
        checkServer { ok in
            if ok { self.loadApp() }
            else if self.server == nil { return }
            else if attempt > 120 { self.showMessage("Il motore non risponde.", log: true) }
            else {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { self.waitForServer(attempt: attempt + 1) }
            }
        }
    }

    func endStartupSplash() {
        guard startupSplash else { return }
        startupSplash = false
        splash.close(minimum: 1.5)
    }

    @objc func showAbout(_ sender: Any?) {
        startupSplash = false
        splash.show(over: window, about: true)
    }

    // l'interfaccia è pronta: via la schermata di avvio
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        if webView.url?.host == "localhost" { endStartupSplash() }
    }

    func serverStopped(_ status: Int32) {
        server = nil
        if quitting { return }
        endStartupSplash()
        showMessage("Il motore si è fermato (codice \(status)).", log: true)
    }

    func runningJobs() -> Int {
        var count = 0
        let sem = DispatchSemaphore(value: 0)
        var req = URLRequest(url: baseURL.appendingPathComponent("api/jobs"))
        req.timeoutInterval = 1.5
        URLSession.shared.dataTask(with: req) { data, _, _ in
            if let data = data,
               let jobs = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] {
                // la sincronizzazione rapida si può interrompere senza problemi
                count = jobs.filter { ($0["status"] as? String) == "running" && ($0["quick"] as? Bool) != true }.count
            }
            sem.signal()
        }.resume()
        _ = sem.wait(timeout: .now() + 2)
        return count
    }

    func loadApp() {
        webView.load(URLRequest(url: baseURL))
    }

    func showMessage(_ html: String, log: Bool = false) {
        var extra = ""
        if log, let text = try? String(contentsOfFile: logPath, encoding: .utf8) {
            let tail = text.split(separator: "\n").suffix(15).joined(separator: "\n")
            let safe = tail.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;")
            extra = "<pre>\(safe)</pre><p class=s>Registro completo: menu Aiuto › Mostra registro</p>"
        }
        let page = """
        <html><head><meta charset="utf-8"><style>
        body{background:#1e1e1f;color:#d9d9db;font:14px -apple-system,sans-serif;display:flex;flex-direction:column;
        align-items:center;justify-content:center;height:100vh;margin:0;text-align:center;-webkit-user-select:none}
        h1{font-weight:500;font-size:20px;margin:0 0 12px} code,pre{background:#2e2e31;padding:2px 6px;border-radius:4px}
        pre{text-align:left;max-width:80%;overflow:auto;padding:12px;font-size:11px;-webkit-user-select:text} .s{color:#8e8e93}
        </style></head><body><h1>Provinia</h1><p>\(html)</p>\(extra)</body></html>
        """
        webView.loadHTMLString(page, baseURL: nil)
    }

    // MARK: collegamento con l'interfaccia

    func runCommand(_ name: String) {
        webView.evaluateJavaScript("window.fc && fc.cmd(\(jsString(name)))", completionHandler: nil)
    }

    func userContentController(_ userContentController: WKUserContentController,
                               didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any], let cmd = body["cmd"] as? String else { return }
        if cmd == "drag", let wv = webView as? FCWebView {
            let ids = (body["ids"] as? [Any] ?? []).compactMap { ($0 as? NSNumber)?.intValue }
            wv.onDragEnd = { [weak wv] in wv?.evaluateJavaScript("window.fc && fc.dragEnded()", completionHandler: nil) }
            wv.requestDrag(ids: ids)
            return
        }
        if cmd == "pick" {
            let kind = body["kind"] as? String ?? "folder"
            let reqId = (body["id"] as? NSNumber)?.intValue ?? 0
            let isFolder = kind == "folder" || kind == "dest"
            let panel = NSOpenPanel()
            panel.canChooseDirectories = isFolder
            panel.canChooseFiles = !isFolder
            panel.canCreateDirectories = kind == "dest"
            panel.allowsMultipleSelection = false
            panel.prompt = "Scegli"
            panel.message = kind == "folder" ? "Scegli la cartella delle foto sul NAS"
                          : kind == "dest" ? "Scegli dove salvare"
                          : "Scegli il catalogo Lightroom (.lrcat)"
            if kind == "folder" { panel.directoryURL = URL(fileURLWithPath: "/Volumes") }
            panel.beginSheetModal(for: window) { response in
                let path = response == .OK ? panel.url?.path : nil
                self.webView.evaluateJavaScript("window.fc && fc.pickResult(\(reqId), \(jsString(path)))",
                                                completionHandler: nil)
            }
        }
    }

    // Link esterni nel browser; la finestra mostra solo l'interfaccia locale.
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        if url.host == "localhost" || url.scheme == "about" {
            decisionHandler(.allow)
        } else {
            if url.scheme == "http" || url.scheme == "https" { NSWorkspace.shared.open(url) }
            decisionHandler(.cancel)
        }
    }

    // MARK: menu

    @objc func menuCommand(_ sender: NSMenuItem) {
        if let name = sender.representedObject as? String { runCommand(name) }
    }

    @objc func reloadUI(_ sender: Any?) { startServer() }

    // ⌘C: se si sta scrivendo o è selezionato del testo copia il testo, altrimenti le foto
    // ⌘Z: annulla l'ultima operazione di Provinia; mentre si scrive in un campo, il testo
    @objc func undoAction(_ sender: Any?) {
        webView.evaluateJavaScript("window.fc && fc.undoHere ? fc.undoHere() : false") { result, _ in
            if (result as? Bool) == true {
                self.webView.evaluateJavaScript("fc.cmd('undo')", completionHandler: nil)
            } else {
                NSApp.sendAction(Selector(("undo:")), to: nil, from: sender)
            }
        }
    }

    @objc func redoAction(_ sender: Any?) {
        webView.evaluateJavaScript("window.fc && fc.undoHere ? fc.undoHere() : false") { result, _ in
            if (result as? Bool) == true {
                self.webView.evaluateJavaScript("fc.cmd('redo')", completionHandler: nil)
            } else {
                NSApp.sendAction(Selector(("redo:")), to: nil, from: sender)
            }
        }
    }

    @objc func copyAction(_ sender: Any?) {
        webView.evaluateJavaScript("window.fc && fc.copyIds ? fc.copyIds() : null") { result, _ in
            let ids = (result as? [Any])?.compactMap { ($0 as? NSNumber)?.intValue } ?? []
            if ids.isEmpty {
                NSApp.sendAction(#selector(NSText.copy(_:)), to: nil, from: sender)
                return
            }
            fetchPaths(ids: ids) { paths in self.copyFiles(paths, ids: ids) }
        }
    }

    func copyFiles(_ paths: [String], ids: [Int]) {
        if !paths.isEmpty {
            let pb = NSPasteboard.general
            pb.clearContents()
            clipboardProvider = nil
            var items: [NSPasteboardItem] = []
            for path in paths {
                let item = NSPasteboardItem()
                item.setString(URL(fileURLWithPath: path).absoluteString, forType: .fileURL)
                items.append(item)
            }
            let ext = (paths[0] as NSString).pathExtension.lowercased()
            if paths.count == 1 && ids.count == 1 && !videoExtensions.contains(ext) {
                let provider = ClipboardImageProvider(photoId: ids[0])
                items[0].setDataProvider(provider, forTypes: [.tiff, ClipboardImageProvider.jpeg])
                clipboardProvider = provider
            }
            pb.writeObjects(items)
        }
        webView.evaluateJavaScript("window.fc && fc.copied(\(paths.count), \(ids.count - paths.count))", completionHandler: nil)
    }

    @objc func showLog(_ sender: Any?) {
        NSWorkspace.shared.open(URL(fileURLWithPath: logPath))
    }

    @objc func showDataFolder(_ sender: Any?) {
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first!
        var dir = docs.appendingPathComponent("Provinia")
        if !FileManager.default.fileExists(atPath: dir.path) { dir = docs.appendingPathComponent("FotoCatalogo") }
        NSWorkspace.shared.open(dir)
    }

    func buildMainMenu() {
        let main = NSMenu()

        func submenu(_ title: String) -> NSMenu {
            let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
            let menu = NSMenu(title: title)
            item.submenu = menu
            main.addItem(item)
            return menu
        }
        func cmd(_ menu: NSMenu, _ title: String, _ name: String, _ key: String = "",
                 _ mods: NSEvent.ModifierFlags = [.command]) {
            let item = NSMenuItem(title: title, action: #selector(menuCommand(_:)), keyEquivalent: key)
            item.keyEquivalentModifierMask = mods
            item.representedObject = name
            item.target = self
            menu.addItem(item)
        }
        func sys(_ menu: NSMenu, _ title: String, _ action: Selector, _ key: String = "",
                 _ mods: NSEvent.ModifierFlags = [.command], target: AnyObject? = nil) {
            let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
            item.keyEquivalentModifierMask = mods
            item.target = target
            menu.addItem(item)
        }

        let app = submenu("Provinia")
        sys(app, "Informazioni su Provinia", #selector(showAbout(_:)), target: self)
        app.addItem(.separator())
        sys(app, "Mostra cartella dei cataloghi", #selector(showDataFolder(_:)), target: self)
        app.addItem(.separator())
        sys(app, "Nascondi Provinia", #selector(NSApplication.hide(_:)), "h")
        sys(app, "Nascondi altre", #selector(NSApplication.hideOtherApplications(_:)), "h", [.command, .option])
        sys(app, "Mostra tutte", #selector(NSApplication.unhideAllApplications(_:)))
        app.addItem(.separator())
        sys(app, "Esci da Provinia", #selector(NSApplication.terminate(_:)), "q")

        let lib = submenu("Libreria")
        cmd(lib, "Cambia catalogo…", "switchCatalog", "o", [.command, .option])
        cmd(lib, "Blocca catalogo protetto", "lockCatalog", "l")
        lib.addItem(.separator())
        cmd(lib, "Nuova raccolta…", "newColl", "n")
        cmd(lib, "Nuovo set di raccolte…", "newSet", "n", [.command, .shift])
        lib.addItem(.separator())
        cmd(lib, "Aggiungi cartella di foto…", "addFolder", "o", [.command, .shift])
        cmd(lib, "Importa catalogo Lightroom…", "importLr", "i", [.command, .shift])
        cmd(lib, "Esporta nel catalogo Lightroom…", "exportLr")
        lib.addItem(.separator())
        cmd(lib, "Trova duplicati…", "findDups")
        cmd(lib, "Elimina dal disco tutte le scartate…", "deleteRejected")
        cmd(lib, "Genera anteprime della vista corrente", "pregen")
        cmd(lib, "Cambia percorso del NAS…", "relocate")

        let edit = submenu("Modifica")
        sys(edit, "Annulla", #selector(undoAction(_:)), "z", target: self)
        sys(edit, "Ripeti", #selector(redoAction(_:)), "z", [.command, .shift], target: self)
        edit.addItem(.separator())
        sys(edit, "Taglia", #selector(NSText.cut(_:)), "x")
        sys(edit, "Copia", #selector(copyAction(_:)), "c", target: self)
        sys(edit, "Incolla", #selector(NSText.paste(_:)), "v")
        cmd(edit, "Seleziona tutto", "selectAll", "a")
        cmd(edit, "Deseleziona", "selectNone", "d")
        edit.addItem(.separator())
        cmd(edit, "Cerca", "find", "f")

        let photo = submenu("Foto")
        cmd(photo, "Apri in Photoshop", "photoshop", "e")
        cmd(photo, "Apri con l'app predefinita", "openDefault")
        cmd(photo, "Mostra nel Finder", "finder", "r")
        photo.addItem(.separator())
        cmd(photo, "Vista ingrandita", "loupe")
        photo.addItem(.separator())
        cmd(photo, "Aggiungi a raccolta…", "addToColl")
        cmd(photo, "Togli dalla raccolta corrente", "removeFromColl")
        cmd(photo, "Crea pila", "stack", "g")
        cmd(photo, "Aggiungi parole chiave…", "keywords", "k")
        photo.addItem(.separator())
        photo.addItem(.separator())
        let left = String(utf16CodeUnits: [unichar(NSLeftArrowFunctionKey)], count: 1)
        let right = String(utf16CodeUnits: [unichar(NSRightArrowFunctionKey)], count: 1)
        cmd(photo, "Ruota a sinistra", "rotateLeft", left)
        cmd(photo, "Ruota a destra", "rotateRight", right)
        cmd(photo, "Esporta in JPEG…", "exportSel", "e", [.command, .shift])
        photo.addItem(.separator())
        cmd(photo, "Rinomina", "renameSel", String(utf16CodeUnits: [unichar(NSF2FunctionKey)], count: 1), [])
        cmd(photo, "Rinomina con nome e numero…", "renameNum")
        cmd(photo, "Sposta in…", "moveSel")
        photo.addItem(.separator())
        cmd(photo, "Converti (RAW → DNG, PNG → JPEG)…", "convertDng")
        cmd(photo, "Elimina dal disco…", "deleteFiles", "\u{8}")

        let view = submenu("Vista")
        cmd(view, "Mappa dei luoghi…", "map", "m", [.command, .option])
        view.addItem(.separator())
        cmd(view, "Miniature più grandi", "zoomIn", "+")
        cmd(view, "Miniature più piccole", "zoomOut", "-")
        view.addItem(.separator())
        sys(view, "Ricarica", #selector(reloadUI(_:)), "r", [.command, .option], target: self)
        sys(view, "Schermo intero", #selector(NSWindow.toggleFullScreen(_:)), "f", [.command, .control])

        let win = submenu("Finestra")
        sys(win, "Contrai", #selector(NSWindow.performMiniaturize(_:)), "m")
        sys(win, "Ridimensiona", #selector(NSWindow.performZoom(_:)))
        NSApp.windowsMenu = win

        let help = submenu("Aiuto")
        cmd(help, "Scorciatoie da tastiera", "help", "/")
        sys(help, "Mostra registro", #selector(showLog(_:)), target: self)
        NSApp.helpMenu = help

        NSApp.mainMenu = main
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
