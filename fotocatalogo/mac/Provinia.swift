// Provinia.app (ex FotoCatalogo) — finestra nativa per macOS.
//
// Avvia il motore Python (server.py) in background, mostra l'interfaccia in una
// finestra WebKit e fornisce la barra dei menu di macOS. Alla chiusura dell'app
// il motore viene fermato. Si compila con mac/crea_app.sh.

import Cocoa
import WebKit
import ObjectiveC

let port = 8765
let baseURL = URL(string: "http://localhost:\(port)/")!
let logPath = (NSHomeDirectory() as NSString).appendingPathComponent("Library/Logs/Provinia.log")

func jsString(_ value: String?) -> String {
    guard let value = value,
          let data = try? JSONSerialization.data(withJSONObject: [value]),
          let s = String(data: data, encoding: .utf8) else { return "null" }
    return String(s.dropFirst().dropLast())   // ["…"] -> "…"
}

// MARK: trascinare le foto fuori dall'app = copiarle
//
// Il trascinamento delle miniature è gestito da WebKit (serve per raccolte e cartelle
// dentro l'app). Quando inizia, la pagina mette nel testo del trascinamento
// "provinia-ids:1,2,3": qui lo leggiamo, chiediamo al motore i percorsi dei file e li
// aggiungiamo al trascinamento. Fuori dall'app (Finder, scrivania, Mail…) si può
// solo copiare: gli originali restano dove sono.
final class FCWebView: WKWebView {
    var dragPaths: [String] = []
    static let filenamesType = NSPasteboard.PasteboardType("NSFilenamesPboardType")

    func photoPaths(fromText text: String?) -> [String] {
        guard let text = text, text.hasPrefix("provinia-ids:") else { return [] }
        let ids = text.dropFirst("provinia-ids:".count).split(separator: ",").compactMap { Int($0) }
        if ids.isEmpty { return [] }
        var paths: [String] = []
        let sem = DispatchSemaphore(value: 0)
        var req = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/api/paths")!)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["ids": ids])
        req.timeoutInterval = 3
        URLSession.shared.dataTask(with: req) { data, _, _ in
            if let data = data, let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                paths = obj["paths"] as? [String] ?? []
            }
            sem.signal()
        }.resume()
        _ = sem.wait(timeout: .now() + 4)
        return paths
    }

    // usato da WebKit per i trascinamenti dalla pagina
    override func dragImage(_ image: NSImage, at viewLocation: NSPoint, offset initialOffset: NSSize,
                           event: NSEvent, pasteboard pboard: NSPasteboard, source sourceObj: Any, slideBack: Bool) {
        dragPaths = photoPaths(fromText: pboard.string(forType: .string))
        if !dragPaths.isEmpty {
            // si aggiungono i file all'elemento già presente, senza toccare i dati della pagina
            pboard.addTypes([FCWebView.filenamesType], owner: nil)
            pboard.setPropertyList(dragPaths, forType: FCWebView.filenamesType)
        }
        super.dragImage(image, at: viewLocation, offset: initialOffset, event: event,
                        pasteboard: pboard, source: sourceObj, slideBack: slideBack)
    }

    // nel caso WebKit usi l'API più recente
    override func beginDraggingSession(with items: [NSDraggingItem], event: NSEvent,
                                       source: NSDraggingSource) -> NSDraggingSession {
        var text: String?
        for item in items {
            if let pi = item.item as? NSPasteboardItem, let t = pi.string(forType: .string) { text = t; break }
        }
        dragPaths = photoPaths(fromText: text)
        var all = items
        if let frame = items.first?.draggingFrame {
            for path in dragPaths {
                let extra = NSDraggingItem(pasteboardWriter: URL(fileURLWithPath: path) as NSURL)
                extra.setDraggingFrame(frame, contents: nil)
                all.append(extra)
            }
        }
        return super.beginDraggingSession(with: all, event: event, source: source)
    }

    // Fuori dall'app solo "copia" (il Finder non deve mai spostare gli originali).
    // Il metodo è di WebKit e non è dichiarato pubblicamente: lo si sostituisce al volo.
    static func installDragMask() {
        let sel = NSSelectorFromString("draggingSession:sourceOperationMaskForDraggingContext:")
        typealias Fn = @convention(c) (AnyObject, Selector, NSDraggingSession, NSDraggingContext) -> NSDragOperation
        let superMethod = class_getInstanceMethod(WKWebView.self, sel)
        let callSuper: Fn? = superMethod.map { unsafeBitCast(method_getImplementation($0), to: Fn.self) }
        let block: @convention(block) (AnyObject, NSDraggingSession, NSDraggingContext) -> NSDragOperation = { obj, session, context in
            if context == .outsideApplication, let wv = obj as? FCWebView, !wv.dragPaths.isEmpty { return .copy }
            if let callSuper = callSuper { return callSuper(obj, sel, session, context) }
            return [.copy, .move, .generic]
        }
        let imp = imp_implementationWithBlock(block)
        let added: Bool
        if let m = superMethod, let types = method_getTypeEncoding(m) {
            added = class_addMethod(FCWebView.self, sel, imp, types)
        } else {
            added = class_addMethod(FCWebView.self, sel, imp, "Q@:@q")
        }
        if !added {
            if let m = class_getInstanceMethod(FCWebView.self, sel) { method_setImplementation(m, imp) }
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate,
                         WKScriptMessageHandler, WKNavigationDelegate, WKUIDelegate {
    var window: NSWindow!
    var webView: WKWebView!
    var server: Process?
    var quitting = false
    var titleObserver: NSKeyValueObservation?

    // MARK: avvio

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMainMenu()

        let config = WKWebViewConfiguration()
        config.userContentController.add(self, name: "fc")
        FCWebView.installDragMask()
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
        window.contentView = webView
        window.delegate = self
        window.center()
        window.setFrameAutosaveName("FotoCatalogoFinestra")   // ricorda posizione e dimensioni (nome storico)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        showMessage("Avvio in corso…")
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
            p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
            p.arguments = ["python3", script.path, "--no-browser"]
            var env = ProcessInfo.processInfo.environment
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

    func serverStopped(_ status: Int32) {
        server = nil
        if quitting { return }
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
        sys(app, "Informazioni su Provinia", #selector(NSApplication.orderFrontStandardAboutPanel(_:)))
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
        lib.addItem(.separator())
        cmd(lib, "Trova duplicati…", "findDups")
        cmd(lib, "Genera anteprime della vista corrente", "pregen")
        cmd(lib, "Cambia percorso del NAS…", "relocate")

        let edit = submenu("Modifica")
        sys(edit, "Annulla", Selector(("undo:")), "z")
        sys(edit, "Ripeti", Selector(("redo:")), "z", [.command, .shift])
        edit.addItem(.separator())
        sys(edit, "Taglia", #selector(NSText.cut(_:)), "x")
        sys(edit, "Copia", #selector(NSText.copy(_:)), "c")
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
