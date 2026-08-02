// Plancia per macOS.
//
// L'app è una finestra nativa sulla dashboard più le cose che il browser non
// può fare: tenere vivo il backend, stare nella barra dei menu, parlare e
// ascoltare. Il contenuto resta quello servito da 127.0.0.1, così l'app e il
// browser mostrano sempre la stessa cosa.

import AppKit
import AVFoundation
import CoreServices
import Speech
import UserNotifications
import WebKit

// MARK: - configurazione

struct Conf {
    static let home = FileManager.default.homeDirectoryForCurrentUser
    static var dataDir: URL { home.appendingPathComponent(".plancia") }

    static var settings: [String: Any] {
        guard let d = try? Data(contentsOf: dataDir.appendingPathComponent("config.json")),
              let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any] else { return [:] }
        return j
    }
    static var port: Int { (settings["port"] as? Int) ?? 7773 }
    static var lang: String { (settings["lingua"] as? String) ?? "it" }
    static var base: String { "http://127.0.0.1:\(port)" }
    static var token: String {
        (try? String(contentsOf: dataDir.appendingPathComponent("token"), encoding: .utf8))?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    }

    /// Il comando `plancia`. Il percorso vero lo scrive lo script di build,
    /// gli altri sono i posti dove finisce normalmente.
    static var executable: String? {
        var candidati: [String] = []
        if let p = Bundle.main.object(forInfoDictionaryKey: "PlanciaExecutable") as? String {
            candidati.append(p)
        }
        candidati += [
            home.appendingPathComponent(".local/bin/plancia").path,
            "/usr/local/bin/plancia",
            "/opt/homebrew/bin/plancia",
            home.appendingPathComponent("dev/plancia/bin/plancia").path,
        ]
        return candidati.first { FileManager.default.isExecutableFile(atPath: $0) }
    }
}

// MARK: - diario

/// Un file di testo in ~/.plancia/app.log. Senza questo, quando l'app non fa
/// quello che dovrebbe non si vede niente da nessuna parte.
enum Log {
    static let file = Conf.dataDir.appendingPathComponent("app.log")
    static func write(_ s: String) {
        let riga = "\(ISO8601DateFormatter().string(from: Date())) \(s)\n"
        guard let d = riga.data(using: .utf8) else { return }
        if let fh = try? FileHandle(forWritingTo: file) {
            fh.seekToEndOfFile(); fh.write(d); try? fh.close()
        } else {
            try? d.write(to: file)
        }
    }
}

// MARK: - chiamate al backend

enum API {
    static func request(_ path: String, method: String = "GET", body: [String: Any]? = nil,
                        timeout: TimeInterval = 180,
                        done: @escaping ([String: Any]?, String?) -> Void) {
        guard let url = URL(string: Conf.base + path) else { return done(nil, "url non valida") }
        var req = URLRequest(url: url, timeoutInterval: timeout)
        req.httpMethod = method
        req.setValue(Conf.token, forHTTPHeaderField: "X-Plancia-Token")
        if let body = body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        }
        Log.write("richiesta \(method) \(path)")
        URLSession.shared.dataTask(with: req) { data, res, err in
            if let err = err {
                Log.write("errore \(path): \(err.localizedDescription)")
                return DispatchQueue.main.async { done(nil, err.localizedDescription) }
            }
            let code = (res as? HTTPURLResponse)?.statusCode ?? 0
            let j = data.flatMap { try? JSONSerialization.jsonObject(with: $0) } as? [String: Any]
            Log.write("risposta \(path): \(code)")
            DispatchQueue.main.async { done(j, j?["errore"] as? String) }
        }.resume()
    }

    static func alive(_ done: @escaping (Bool) -> Void) {
        guard let url = URL(string: Conf.base + "/api/status") else { return done(false) }
        var req = URLRequest(url: url, timeoutInterval: 2)
        req.httpMethod = "GET"
        URLSession.shared.dataTask(with: req) { data, res, _ in
            let ok = (res as? HTTPURLResponse)?.statusCode == 200 && data != nil
            DispatchQueue.main.async { done(ok) }
        }.resume()
    }
}

// MARK: - il backend, tenuto vivo

final class Backend {
    private var process: Process?
    private(set) var avviatoDaNoi = false

    func ensureRunning(_ done: @escaping (Bool) -> Void) {
        API.alive { up in
            if up { return done(true) }
            guard let exe = Conf.executable else { return done(false) }
            let p = Process()
            p.executableURL = URL(fileURLWithPath: exe)
            p.arguments = ["serve"]
            p.standardOutput = FileHandle.nullDevice
            p.standardError = FileHandle.nullDevice
            do { try p.run() } catch { return done(false) }
            self.process = p
            self.avviatoDaNoi = true
            self.attendi(tentativi: 25, done: done)
        }
    }

    private func attendi(tentativi: Int, done: @escaping (Bool) -> Void) {
        if tentativi <= 0 { return done(false) }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) {
            API.alive { up in
                if up { done(true) } else { self.attendi(tentativi: tentativi - 1, done: done) }
            }
        }
    }

    func stopIfOurs() {
        // Se il backend gira per conto suo (launchd) non è compito nostro spegnerlo.
        if avviatoDaNoi, let p = process, p.isRunning { p.terminate() }
    }
}

// MARK: - riproduzione

final class Player: NSObject, AVAudioPlayerDelegate {
    static let shared = Player()
    private var player: AVAudioPlayer?
    var onFinish: (() -> Void)?

    func play(path: String) {
        stop()
        guard let p = try? AVAudioPlayer(contentsOf: URL(fileURLWithPath: path)) else { return }
        p.delegate = self
        player = p
        p.play()
    }
    func stop() {
        player?.stop()
        player = nil
    }
    var isPlaying: Bool { player?.isPlaying ?? false }

    func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        self.player = nil
        onFinish?()
    }
}

// MARK: - ascolto

final class Listener {
    private let engine = AVAudioEngine()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var recognizer: SFSpeechRecognizer?
    private(set) var attivo = false

    static func locale(for lang: String) -> Locale {
        let map = ["it": "it-IT", "en": "en-US", "es": "es-ES",
                   "fr": "fr-FR", "de": "de-DE", "pt": "pt-BR"]
        return Locale(identifier: map[lang] ?? "en-US")
    }

    func chiediPermessi(_ done: @escaping (Bool, String) -> Void) {
        SFSpeechRecognizer.requestAuthorization { stato in
            DispatchQueue.main.async {
                guard stato == .authorized else {
                    return done(false, "Riconoscimento vocale non autorizzato. Si abilita in Impostazioni di sistema, Privacy e sicurezza.")
                }
                AVCaptureDevice.requestAccess(for: .audio) { ok in
                    DispatchQueue.main.async {
                        done(ok, ok ? "" : "Microfono non autorizzato.")
                    }
                }
            }
        }
    }

    func start(lang: String, parziale: @escaping (String) -> Void,
               errore: @escaping (String) -> Void) {
        stop()
        let rec = SFSpeechRecognizer(locale: Listener.locale(for: lang))
        guard let rec = rec, rec.isAvailable else {
            return errore("Riconoscimento non disponibile per questa lingua.")
        }
        recognizer = rec
        let req = SFSpeechAudioBufferRecognitionRequest()
        req.shouldReportPartialResults = true
        request = req

        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        input.removeTap(onBus: 0)
        input.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
            req.append(buffer)
        }
        engine.prepare()
        do { try engine.start() } catch {
            return errore("Non riesco ad aprire il microfono.")
        }
        attivo = true
        task = rec.recognitionTask(with: req) { result, err in
            if let result = result {
                parziale(result.bestTranscription.formattedString)
            }
            if err != nil && self.attivo == false { return }
        }
    }

    @discardableResult
    func stop() -> Bool {
        guard attivo else { return false }
        attivo = false
        engine.inputNode.removeTap(onBus: 0)
        if engine.isRunning { engine.stop() }
        request?.endAudio()
        task?.finish()
        request = nil
        task = nil
        return true
    }
}

// MARK: - pannello della voce

final class VoicePanel: NSWindowController {
    private let testo = NSTextView()
    private let stato = NSTextField(labelWithString: "")
    private let bottoneParla = NSButton()
    private let bottoneRiepilogo = NSButton()
    private let bottoneStop = NSButton()
    private let scelta = NSPopUpButton()
    private let listener = Listener()
    private var trascrizione = ""
    private var occupato = false

    convenience init() {
        let panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 460, height: 340),
                            styleMask: [.titled, .closable, .utilityWindow, .hudWindow],
                            backing: .buffered, defer: false)
        panel.title = "Plancia"
        panel.isFloatingPanel = true
        panel.hidesOnDeactivate = false
        self.init(window: panel)
        costruisci()
    }

    private func costruisci() {
        guard let content = window?.contentView else { return }

        let scroll = NSScrollView()
        scroll.hasVerticalScroller = true
        scroll.drawsBackground = false
        testo.isEditable = false
        testo.drawsBackground = false
        testo.font = NSFont.systemFont(ofSize: 13)
        testo.textContainerInset = NSSize(width: 6, height: 6)
        testo.string = ""
        scroll.documentView = testo
        scroll.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(scroll)

        stato.font = NSFont.systemFont(ofSize: 11)
        stato.textColor = .secondaryLabelColor
        stato.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(stato)

        for (b, titolo, sel) in [
            (bottoneRiepilogo, "Riepilogo", #selector(riepilogo)),
            (bottoneParla, "Tieni premuto e parla", #selector(parla)),
            (bottoneStop, "Ferma", #selector(ferma)),
        ] {
            b.title = titolo
            b.bezelStyle = .rounded
            b.target = self
            b.action = sel
            b.translatesAutoresizingMaskIntoConstraints = false
            content.addSubview(b)
        }
        bottoneParla.setButtonType(.pushOnPushOff)

        scelta.addItems(withTitles: ["it", "en", "es", "fr", "de", "pt"])
        scelta.selectItem(withTitle: Conf.lang)
        scelta.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(scelta)

        NSLayoutConstraint.activate([
            scroll.topAnchor.constraint(equalTo: content.topAnchor, constant: 12),
            scroll.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 12),
            scroll.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -12),
            scroll.bottomAnchor.constraint(equalTo: stato.topAnchor, constant: -8),

            stato.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 14),
            stato.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -14),
            stato.bottomAnchor.constraint(equalTo: bottoneParla.topAnchor, constant: -8),

            bottoneRiepilogo.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 12),
            bottoneRiepilogo.bottomAnchor.constraint(equalTo: content.bottomAnchor, constant: -12),
            bottoneParla.leadingAnchor.constraint(equalTo: bottoneRiepilogo.trailingAnchor, constant: 8),
            bottoneParla.bottomAnchor.constraint(equalTo: content.bottomAnchor, constant: -12),
            bottoneStop.leadingAnchor.constraint(equalTo: bottoneParla.trailingAnchor, constant: 8),
            bottoneStop.bottomAnchor.constraint(equalTo: content.bottomAnchor, constant: -12),
            scelta.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -12),
            scelta.bottomAnchor.constraint(equalTo: content.bottomAnchor, constant: -12),
            scelta.widthAnchor.constraint(equalToConstant: 66),
        ])
    }

    private var lingua: String { scelta.titleOfSelectedItem ?? Conf.lang }

    private func mostra(_ s: String) {
        testo.string = s
        testo.scrollToBeginningOfDocument(nil)
    }

    private func lavora(_ acceso: Bool, _ messaggio: String = "") {
        occupato = acceso
        stato.stringValue = messaggio
        bottoneRiepilogo.isEnabled = !acceso
    }

    @objc func riepilogo() {
        guard !occupato else { return }
        lavora(true, "preparo il riepilogo…")
        mostra("")
        API.request("/api/recap", method: "POST",
                    body: ["voce": true, "lang": lingua]) { j, err in
            self.lavora(false, "")
            if let err = err { return self.mostra("Errore: \(err)") }
            guard let j = j else { return self.mostra("Nessuna risposta dal backend.") }
            self.mostra((j["testo"] as? String) ?? "")
            self.stato.stringValue = "voce: \((j["motore"] as? String) ?? "?")"
            if let file = j["file"] as? String { Player.shared.play(path: file) }
            else { self.scaricaEsuona(j["url"] as? String) }
        }
    }

    private func scaricaEsuona(_ url: String?) {
        guard let url = url, let u = URL(string: Conf.base + url) else { return }
        URLSession.shared.downloadTask(with: u) { tmp, _, _ in
            guard let tmp = tmp else { return }
            let dest = FileManager.default.temporaryDirectory
                .appendingPathComponent("plancia-\(UUID().uuidString).wav")
            try? FileManager.default.moveItem(at: tmp, to: dest)
            DispatchQueue.main.async { Player.shared.play(path: dest.path) }
        }.resume()
    }

    @objc func ferma() {
        Player.shared.stop()
        if listener.stop() { bottoneParla.state = .off }
        lavora(false, "")
    }

    @objc func parla() {
        if listener.attivo {
            listener.stop()
            bottoneParla.state = .off
            invia(trascrizione)
            return
        }
        listener.chiediPermessi { ok, messaggio in
            guard ok else {
                self.bottoneParla.state = .off
                return self.mostra(messaggio)
            }
            self.trascrizione = ""
            self.mostra("")
            self.stato.stringValue = "ti ascolto, premi di nuovo quando hai finito"
            self.bottoneParla.state = .on
            self.listener.start(lang: self.lingua, parziale: { t in
                self.trascrizione = t
                self.mostra(t)
            }, errore: { e in
                self.bottoneParla.state = .off
                self.mostra(e)
            })
        }
    }

    fileprivate func invia(_ domanda: String) {
        let q = domanda.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !q.isEmpty else { return lavora(false, "non ho sentito niente") }
        lavora(true, "ci penso…")
        mostra(q + "\n\n…")
        API.request("/api/voice/ask", method: "POST",
                    body: ["domanda": q, "lang": lingua, "voce": true]) { j, err in
            self.lavora(false, "")
            if let err = err { return self.mostra("Errore: \(err)") }
            let risposta = (j?["risposta"] as? String) ?? ""
            self.mostra("\(q)\n\n\(risposta)")
            if let file = j?["file"] as? String { Player.shared.play(path: file) }
            else { self.scaricaEsuona(j?["url"] as? String) }
        }
    }

    /// Domanda arrivata da fuori, per esempio da uno schema URL.
    func chiediTesto(_ q: String) {
        invia(q)
    }

    func apri() {
        window?.center()
        showWindow(nil)
        window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func apriEriepiloga() {
        apri()
        riepilogo()
    }
}

// MARK: - applicazione

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    private var window: NSWindow!
    private var web: WKWebView!
    private let backend = Backend()
    private var statusItem: NSStatusItem!
    private lazy var voce = VoicePanel()
    private var timerRiepilogo: Timer?

    /// L'evento di apertura URL va agganciato prima che l'app finisca di
    /// avviarsi, altrimenti il primo `plancia://` si perde.
    func applicationWillFinishLaunching(_ n: Notification) {
        NSAppleEventManager.shared().setEventHandler(
            self, andSelector: #selector(gestisciEventoURL(_:withReply:)),
            forEventClass: AEEventClass(kInternetEventClass),
            andEventID: AEEventID(kAEGetURL))
    }

    @objc func gestisciEventoURL(_ evento: NSAppleEventDescriptor,
                                 withReply reply: NSAppleEventDescriptor) {
        guard let s = evento.paramDescriptor(forKeyword: keyDirectObject)?.stringValue,
              let url = URL(string: s) else { return }
        esegui(url)
    }

    func applicationDidFinishLaunching(_ n: Notification) {
        Log.write("avvio, backend su \(Conf.base), token \(Conf.token.isEmpty ? "assente" : "presente")")
        costruisciFinestra()
        costruisciMenuBar()
        costruisciMenuPrincipale()
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }

        backend.ensureRunning { ok in
            if ok {
                self.carica()
            } else {
                self.mostraErroreBackend()
            }
        }
        programmaRiepilogo()
    }

    // --- finestra ---
    private func costruisciFinestra() {
        let conf = WKWebViewConfiguration()
        let js = """
        document.documentElement.dataset.app = 'mac';
        """
        conf.userContentController.addUserScript(
            WKUserScript(source: js, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        web = WKWebView(frame: .zero, configuration: conf)
        web.navigationDelegate = self

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1180, height: 780),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable,
                                      .fullSizeContentView],
                          backing: .buffered, defer: false)
        window.title = "Plancia"
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.minSize = NSSize(width: 880, height: 560)
        window.contentView = web
        window.setFrameAutosaveName("PlanciaMain")
        window.center()
        window.makeKeyAndOrderFront(nil)
    }

    private func carica() {
        guard let url = URL(string: Conf.base) else { return }
        web.load(URLRequest(url: url))
    }

    private func mostraErroreBackend() {
        let html = """
        <html><body style="font:14px -apple-system;background:#0b0e13;color:#e9edf4;
        display:flex;align-items:center;justify-content:center;height:100vh;text-align:center">
        <div><h2>Plancia non parte</h2>
        <p>Non trovo il comando <code>plancia</code>.<br>
        Apri il Terminale ed esegui <code>cd ~/dev/plancia &amp;&amp; ./bin/plancia install</code>,
        poi riapri l'app.</p></div></body></html>
        """
        web.loadHTMLString(html, baseURL: nil)
    }

    // --- barra dei menu ---
    private func costruisciMenuBar() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem.button {
            button.image = NSImage(systemSymbolName: "location.north.circle",
                                   accessibilityDescription: "Plancia")
            button.image?.isTemplate = true
        }
        let menu = NSMenu()
        menu.addItem(voceMenu("Riepilogo vocale", #selector(riepilogoVocale), "r"))
        menu.addItem(voceMenu("Chiedi a Plancia", #selector(apriVoce), "j"))
        menu.addItem(NSMenuItem.separator())
        menu.addItem(voceMenu("Apri la dashboard", #selector(apriFinestra), "o"))
        menu.addItem(voceMenu("Aggiorna i dati", #selector(sincronizza), "u"))
        menu.addItem(voceMenu("Salva una schermata", #selector(schermata), "s"))
        menu.addItem(NSMenuItem.separator())
        menu.addItem(voceMenu("Esci da Plancia", #selector(esci), "q"))
        statusItem.menu = menu
    }

    private func voceMenu(_ titolo: String, _ sel: Selector, _ tasto: String) -> NSMenuItem {
        let item = NSMenuItem(title: titolo, action: sel, keyEquivalent: tasto)
        item.target = self
        return item
    }

    private func costruisciMenuPrincipale() {
        let main = NSMenu()
        let appItem = NSMenuItem()
        main.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(voceMenu("Riepilogo vocale", #selector(riepilogoVocale), "r"))
        appMenu.addItem(voceMenu("Chiedi a Plancia", #selector(apriVoce), "j"))
        appMenu.addItem(voceMenu("Aggiorna i dati", #selector(sincronizza), "u"))
        appMenu.addItem(NSMenuItem.separator())
        // Nascondi va al responder chain, non a noi
        appMenu.addItem(NSMenuItem(title: "Nascondi", action: #selector(NSApplication.hide(_:)),
                                   keyEquivalent: "h"))
        appMenu.addItem(voceMenu("Esci da Plancia", #selector(esci), "q"))
        appItem.submenu = appMenu

        let vista = NSMenuItem()
        main.addItem(vista)
        let vistaMenu = NSMenu(title: "Vista")
        vistaMenu.addItem(voceMenu("Ricarica", #selector(ricarica), "R"))
        vistaMenu.addItem(voceMenu("Schermo intero", #selector(schermoIntero), "f"))
        vista.submenu = vistaMenu

        NSApp.mainMenu = main
    }

    // --- azioni ---
    @objc func apriFinestra() {
        if !window.isVisible { window.makeKeyAndOrderFront(nil) }
        NSApp.activate(ignoringOtherApps: true)
    }
    @objc func ricarica() { carica() }
    @objc func schermoIntero() { window.toggleFullScreen(nil) }
    @objc func apriVoce() { voce.apri() }
    @objc func riepilogoVocale() { voce.apriEriepiloga() }
    @objc func sincronizza() {
        API.request("/api/sync", method: "POST", body: [:]) { _, _ in
            DispatchQueue.main.asyncAfter(deadline: .now() + 2) { self.carica() }
        }
    }
    /// Salva la finestra in un PNG. Serve per gli screenshot dei post e per
    /// vedere com'è venuta senza dover essere davanti al Mac.
    @objc func schermata() {
        let conf = WKSnapshotConfiguration()
        conf.afterScreenUpdates = true
        web.takeSnapshot(with: conf) { image, errore in
            guard let image = image,
                  let tiff = image.tiffRepresentation,
                  let rep = NSBitmapImageRep(data: tiff),
                  let png = rep.representation(using: .png, properties: [:]) else {
                Log.write("schermata fallita: \(errore?.localizedDescription ?? "?")")
                return
            }
            let dir = Conf.dataDir.appendingPathComponent("shots")
            try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            let f = DateFormatter()
            f.dateFormat = "yyyyMMdd-HHmmss"
            let dest = dir.appendingPathComponent("plancia-\(f.string(from: Date())).png")
            try? png.write(to: dest)
            Log.write("schermata salvata: \(dest.path)")
        }
    }

    @objc func esci() {
        backend.stopIfOurs()
        NSApp.terminate(nil)
    }

    // --- riepilogo della mattina ---
    private func programmaRiepilogo() {
        timerRiepilogo?.invalidate()
        // Lo scheduler vero è launchd (`plancia daily on`). Qui solo se lo si
        // chiede esplicitamente con una chiave diversa, per non parlare due volte.
        guard let ora = Conf.settings["riepilogo_ora_app"] as? String, ora.contains(":") else { return }
        let pezzi = ora.split(separator: ":").compactMap { Int($0) }
        guard pezzi.count == 2 else { return }
        var comp = DateComponents()
        comp.hour = pezzi[0]
        comp.minute = pezzi[1]
        guard let prossimo = Calendar.current.nextDate(after: Date(), matching: comp,
                                                       matchingPolicy: .nextTime) else { return }
        timerRiepilogo = Timer(fire: prossimo, interval: 86400, repeats: true) { _ in
            self.voce.apriEriepiloga()
        }
        RunLoop.main.add(timerRiepilogo!, forMode: .common)
    }

    /// plancia://recap, plancia://ask?q=..., plancia://open, plancia://sync
    /// Serve per legarlo a una scorciatoia di sistema o a Raycast.
    func application(_ application: NSApplication, open urls: [URL]) {
        urls.forEach(esegui)
    }

    private func esegui(_ url: URL) {
        Log.write("url ricevuto: \(url.absoluteString)")
        guard url.scheme == "plancia" else { return }
        let azione = url.host ?? url.path.replacingOccurrences(of: "/", with: "")
        switch azione {
        case "recap", "riepilogo": riepilogoVocale()
        case "ask", "chiedi":
            let q = URLComponents(url: url, resolvingAgainstBaseURL: false)?
                .queryItems?.first(where: { $0.name == "q" })?.value ?? ""
            voce.apri()
            if !q.isEmpty { voce.chiediTesto(q) }
        case "sync", "aggiorna": sincronizza()
        case "screenshot", "schermata": schermata()
        case "open", "apri", "vista":
            let q = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
            let vista = q.first(where: { $0.name == "view" })?.value
            let ui = q.first(where: { $0.name == "ui" })?.value
            apriFinestra()
            if vista != nil || ui != nil { vai(vista: vista, ui: ui) }
        default: apriFinestra()
        }
    }

    /// Porta la finestra su una vista precisa, opzionalmente in un'altra lingua.
    private func vai(vista: String?, ui: String?) {
        var s = Conf.base + "/"
        if let ui = ui { s += "?ui=" + ui }
        if let v = vista { s += "#/" + v }
        if let u = URL(string: s) { web.load(URLRequest(url: u)) }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ s: NSApplication) -> Bool { false }
    func applicationShouldHandleReopen(_ s: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        apriFinestra()
        return true
    }
    func applicationWillTerminate(_ n: Notification) { backend.stopIfOurs() }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
