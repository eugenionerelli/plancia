// Modalità Jarvis: parli, lui fa.
//
// Un pannello che sta sopra tutto, ascolta di continuo e capisce quando hai
// finito di parlare dal silenzio, non da un tasto tenuto premuto. Quello che
// dici va al backend, che esegue subito i comandi che riconosce e passa il
// resto a Claude Code con i tool di Plancia aperti. Poi risponde a voce e
// torna in ascolto.

import AppKit
import AVFoundation
import Carbon.HIToolbox
import Speech

// MARK: - stato

enum StatoJarvis {
    case spento, ascolto, penso, parlo

    var colore: NSColor {
        switch self {
        case .spento: return NSColor(calibratedRed: 0.38, green: 0.42, blue: 0.49, alpha: 1)
        case .ascolto: return NSColor(calibratedRed: 0.91, green: 0.58, blue: 0.31, alpha: 1)
        case .penso: return NSColor(calibratedRed: 0.56, green: 0.72, blue: 0.85, alpha: 1)
        case .parlo: return NSColor(calibratedRed: 0.36, green: 0.72, blue: 0.61, alpha: 1)
        }
    }

    func didascalia(_ lang: String) -> String {
        let it: [StatoJarvis: String] = [.spento: "premi per iniziare", .ascolto: "ti ascolto",
                                         .penso: "ci penso", .parlo: "…"]
        let en: [StatoJarvis: String] = [.spento: "tap to start", .ascolto: "listening",
                                         .penso: "thinking", .parlo: "…"]
        let es: [StatoJarvis: String] = [.spento: "toca para empezar", .ascolto: "te escucho",
                                         .penso: "lo pienso", .parlo: "…"]
        let m = lang == "en" ? en : (lang == "es" ? es : it)
        return m[self] ?? ""
    }
}

// MARK: - l'orbita

/// Cerchi concentrici che respirano col volume della voce. Non è decorazione:
/// è l'unico modo per sapere, senza guardare la trascrizione, che ti sta
/// davvero sentendo.
final class Orbita: NSView {
    var stato: StatoJarvis = .spento { didSet { needsDisplay = true } }
    var livello: CGFloat = 0
    private var fase: CGFloat = 0
    private var timer: Timer?

    override var isFlipped: Bool { false }

    func anima(_ acceso: Bool) {
        timer?.invalidate()
        guard acceso else { timer = nil; return }
        timer = Timer.scheduledTimer(withTimeInterval: 1.0 / 30, repeats: true) { [weak self] _ in
            guard let self = self else { return }
            self.fase += 0.055
            self.needsDisplay = true
        }
        RunLoop.main.add(timer!, forMode: .common)
    }

    override func draw(_ dirty: NSRect) {
        let c = NSPoint(x: bounds.midX, y: bounds.midY)
        let base = min(bounds.width, bounds.height) * 0.28
        let colore = stato.colore

        // respiro lento di fondo, sempre presente
        let respiro = 1 + sin(fase) * 0.045
        // il volume spinge il raggio, ma con un tetto: non deve saltare
        let spinta = stato == .ascolto ? min(livello, 1) * 0.42 : 0
        let pulsa = stato == .parlo ? (1 + sin(fase * 2.4) * 0.09) : 1

        // aloni
        for (i, k) in [2.15, 1.72, 1.36].enumerated() {
            let r = base * CGFloat(k) * respiro * (1 + spinta * CGFloat(3 - i) / 3) * pulsa
            let p = NSBezierPath(ovalIn: NSRect(x: c.x - r, y: c.y - r, width: r * 2, height: r * 2))
            colore.withAlphaComponent(0.05 + 0.035 * CGFloat(i)).setFill()
            p.fill()
        }

        // anello
        let ra = base * 1.08 * respiro * (1 + spinta * 0.5)
        let anello = NSBezierPath(ovalIn: NSRect(x: c.x - ra, y: c.y - ra, width: ra * 2, height: ra * 2))
        anello.lineWidth = 1.2
        colore.withAlphaComponent(stato == .spento ? 0.35 : 0.7).setStroke()
        anello.stroke()

        // nucleo
        let rn = base * 0.46 * (stato == .spento ? 0.8 : 1) * pulsa * (1 + spinta * 0.3)
        let nucleo = NSBezierPath(ovalIn: NSRect(x: c.x - rn, y: c.y - rn, width: rn * 2, height: rn * 2))
        colore.withAlphaComponent(stato == .spento ? 0.5 : 0.95).setFill()
        nucleo.fill()

        // mentre pensa, un arco che gira: dice che sta lavorando senza numeri
        if stato == .penso {
            let rr = base * 1.5
            let arco = NSBezierPath()
            let inizio = fase * 60
            arco.appendArc(withCenter: c, radius: rr, startAngle: inizio, endAngle: inizio + 72)
            arco.lineWidth = 2
            arco.lineCapStyle = .round
            colore.withAlphaComponent(0.9).setStroke()
            arco.stroke()
        }
    }
}

// MARK: - ascolto continuo

/// Come il push to talk, ma la fine della frase la decide il silenzio.
final class AscoltoContinuo {
    private let engine = AVAudioEngine()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var recognizer: SFSpeechRecognizer?
    private var timerSilenzio: Timer?
    private var ultimo = ""
    private(set) var attivo = false

    var onParziale: ((String) -> Void)?
    var onFrase: ((String) -> Void)?
    var onLivello: ((CGFloat) -> Void)?
    var onErrore: ((String) -> Void)?

    /// Quanto silenzio serve per considerare finita la frase. Sotto il secondo
    /// taglia le pause di chi pensa mentre parla.
    var attesa: TimeInterval = 1.25

    func permessi(_ done: @escaping (Bool, String) -> Void) {
        SFSpeechRecognizer.requestAuthorization { stato in
            DispatchQueue.main.async {
                guard stato == .authorized else {
                    return done(false, "Il riconoscimento vocale non è autorizzato. Impostazioni di sistema, Privacy e sicurezza.")
                }
                AVCaptureDevice.requestAccess(for: .audio) { ok in
                    DispatchQueue.main.async { done(ok, ok ? "" : "Il microfono non è autorizzato.") }
                }
            }
        }
    }

    func avvia(lang: String) {
        ferma()
        guard let rec = SFSpeechRecognizer(locale: Listener.locale(for: lang)), rec.isAvailable else {
            return onErrore?("Il riconoscimento non è disponibile in questa lingua.") ?? ()
        }
        recognizer = rec
        let req = SFSpeechAudioBufferRecognitionRequest()
        req.shouldReportPartialResults = true
        request = req
        ultimo = ""

        let input = engine.inputNode
        let formato = input.outputFormat(forBus: 0)
        input.removeTap(onBus: 0)
        input.installTap(onBus: 0, bufferSize: 1024, format: formato) { [weak self] buffer, _ in
            req.append(buffer)
            self?.misura(buffer)
        }
        engine.prepare()
        do { try engine.start() } catch {
            return onErrore?("Non riesco ad aprire il microfono.") ?? ()
        }
        attivo = true
        task = rec.recognitionTask(with: req) { [weak self] result, _ in
            guard let self = self, self.attivo, let result = result else { return }
            let testo = result.bestTranscription.formattedString
            if testo != self.ultimo {
                self.ultimo = testo
                DispatchQueue.main.async {
                    self.onParziale?(testo)
                    self.riarma()
                }
            }
        }
    }

    private func misura(_ buffer: AVAudioPCMBuffer) {
        guard let dati = buffer.floatChannelData?[0] else { return }
        let n = Int(buffer.frameLength)
        guard n > 0 else { return }
        var somma: Float = 0
        for i in 0..<n { somma += dati[i] * dati[i] }
        let rms = sqrt(somma / Float(n))
        // la voce sta in un intervallo stretto: si allarga per renderla visibile
        let livello = min(1, CGFloat(rms) * 14)
        DispatchQueue.main.async { self.onLivello?(livello) }
    }

    private func riarma() {
        timerSilenzio?.invalidate()
        timerSilenzio = Timer.scheduledTimer(withTimeInterval: attesa, repeats: false) { [weak self] _ in
            guard let self = self, self.attivo else { return }
            let frase = self.ultimo.trimmingCharacters(in: .whitespacesAndNewlines)
            guard frase.count > 1 else { return }
            self.ferma()
            self.onFrase?(frase)
        }
        RunLoop.main.add(timerSilenzio!, forMode: .common)
    }

    func ferma() {
        timerSilenzio?.invalidate()
        timerSilenzio = nil
        guard attivo else { return }
        attivo = false
        engine.inputNode.removeTap(onBus: 0)
        if engine.isRunning { engine.stop() }
        request?.endAudio()
        task?.cancel()
        request = nil
        task = nil
        onLivello?(0)
    }
}

// MARK: - il pannello

final class JarvisPanel: NSWindowController {
    private let orbita = Orbita()
    private let trascritto = NSTextField(labelWithString: "")
    private let risposta = NSTextField(wrappingLabelWithString: "")
    private let didascalia = NSTextField(labelWithString: "")
    private let ascolto = AscoltoContinuo()
    private var stato: StatoJarvis = .spento { didSet { orbita.stato = stato; aggiornaTesti() } }
    /// Il microfono si apre solo dopo che l'utente ha detto di sì una volta.
    /// Senza questo, riprendere l'ascolto da solo fa comparire la richiesta di
    /// sistema in un momento qualsiasi e l'app resta ferma ad aspettarla.
    private var autorizzato = false
    private var soloTesto = false
    private var lingua: String { Conf.lang }

    /// Azioni che il pannello non sa eseguire da solo: navigare, sincronizzare.
    var onAzione: (([String: Any]) -> Void)?

    convenience init() {
        let panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 320, height: 360),
                            styleMask: [.borderless, .nonactivatingPanel],
                            backing: .buffered, defer: false)
        panel.isFloatingPanel = true
        panel.level = .floating
        panel.backgroundColor = .clear
        panel.isOpaque = false
        panel.hasShadow = true
        panel.isMovableByWindowBackground = true
        panel.hidesOnDeactivate = false
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        self.init(window: panel)
        costruisci()
        collega()
    }

    private func costruisci() {
        guard let content = window?.contentView else { return }
        content.wantsLayer = true

        let sfondo = NSVisualEffectView(frame: content.bounds)
        sfondo.material = .hudWindow
        sfondo.blendingMode = .behindWindow
        sfondo.state = .active
        sfondo.wantsLayer = true
        sfondo.layer?.cornerRadius = 24
        sfondo.layer?.masksToBounds = true
        sfondo.layer?.borderWidth = 1
        sfondo.layer?.borderColor = NSColor.white.withAlphaComponent(0.08).cgColor
        sfondo.autoresizingMask = [.width, .height]
        content.addSubview(sfondo)

        orbita.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(orbita)

        didascalia.font = .systemFont(ofSize: 11, weight: .medium)
        didascalia.textColor = .tertiaryLabelColor
        didascalia.alignment = .center
        didascalia.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(didascalia)

        trascritto.font = .systemFont(ofSize: 12)
        trascritto.textColor = .secondaryLabelColor
        trascritto.alignment = .center
        trascritto.lineBreakMode = .byTruncatingHead
        trascritto.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(trascritto)

        risposta.font = .systemFont(ofSize: 13.5)
        risposta.textColor = .labelColor
        risposta.alignment = .center
        risposta.maximumNumberOfLines = 6
        risposta.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(risposta)

        NSLayoutConstraint.activate([
            orbita.topAnchor.constraint(equalTo: content.topAnchor, constant: 22),
            orbita.centerXAnchor.constraint(equalTo: content.centerXAnchor),
            orbita.widthAnchor.constraint(equalToConstant: 150),
            orbita.heightAnchor.constraint(equalToConstant: 150),

            didascalia.topAnchor.constraint(equalTo: orbita.bottomAnchor, constant: 8),
            didascalia.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 16),
            didascalia.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -16),

            trascritto.topAnchor.constraint(equalTo: didascalia.bottomAnchor, constant: 4),
            trascritto.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 16),
            trascritto.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -16),

            risposta.topAnchor.constraint(equalTo: trascritto.bottomAnchor, constant: 12),
            risposta.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 20),
            risposta.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -20),
            risposta.bottomAnchor.constraint(lessThanOrEqualTo: content.bottomAnchor, constant: -18),
        ])

        let click = NSClickGestureRecognizer(target: self, action: #selector(tocca))
        orbita.addGestureRecognizer(click)
    }

    private func collega() {
        ascolto.onLivello = { [weak self] l in self?.orbita.livello = l }
        ascolto.onParziale = { [weak self] t in self?.trascritto.stringValue = t }
        ascolto.onErrore = { [weak self] e in
            self?.stato = .spento
            self?.risposta.stringValue = e
        }
        ascolto.onFrase = { [weak self] frase in self?.manda(frase) }
        Player.shared.onFinish = { [weak self] in
            guard let self = self, self.window?.isVisible == true else { return }
            self.riprendiAscolto()
        }
    }

    private func aggiornaTesti() {
        didascalia.stringValue = stato.didascalia(lingua)
        orbita.anima(stato != .spento)
    }

    // --- ciclo ---

    @objc private func tocca() {
        if stato == .spento { avvia() } else { sospendi() }
    }

    func apri() {
        posiziona()
        window?.orderFrontRegardless()
        if stato == .spento { avvia() }
    }

    private func posiziona() {
        guard let w = window, let schermo = NSScreen.main else { return }
        let vf = schermo.visibleFrame
        w.setFrameOrigin(NSPoint(x: vf.maxX - w.frame.width - 28, y: vf.minY + 28))
    }

    func avvia() {
        ascolto.permessi { [weak self] ok, messaggio in
            guard let self = self else { return }
            guard ok else {
                self.autorizzato = false
                self.stato = .spento
                self.risposta.stringValue = messaggio
                return
            }
            self.autorizzato = true
            self.soloTesto = false
            self.risposta.stringValue = ""
            self.trascritto.stringValue = ""
            self.stato = .ascolto
            self.ascolto.avvia(lang: self.lingua)
            Log.write("jarvis: in ascolto (\(self.lingua))")
        }
    }

    func sospendi() {
        ascolto.ferma()
        Player.shared.stop()
        stato = .spento
        trascritto.stringValue = ""
    }

    func chiudi() {
        sospendi()
        window?.orderOut(nil)
    }

    private func riprendiAscolto() {
        guard autorizzato, !soloTesto, stato != .spento else {
            stato = .spento
            return
        }
        stato = .ascolto
        trascritto.stringValue = ""
        ascolto.avvia(lang: lingua)
    }

    /// Frase iniettata da fuori (schema URL o riga di comando): stesso percorso
    /// della voce, senza microfono. Serve a provarlo e a usarlo scrivendo.
    func detta(_ frase: String) {
        posiziona()
        window?.orderFrontRegardless()
        soloTesto = !autorizzato
        orbita.anima(true)
        manda(frase)
    }

    /// Il pannello che si fotografa da solo, per vedere com'è venuto.
    func schermata(in cartella: URL) -> URL? {
        guard let v = window?.contentView,
              let rep = v.bitmapImageRepForCachingDisplay(in: v.bounds) else { return nil }
        v.cacheDisplay(in: v.bounds, to: rep)
        guard let png = rep.representation(using: .png, properties: [:]) else { return nil }
        let dest = cartella.appendingPathComponent("jarvis-\(Int(Date().timeIntervalSince1970)).png")
        try? png.write(to: dest)
        return dest
    }

    private func manda(_ frase: String) {
        stato = .penso
        trascritto.stringValue = frase
        Log.write("jarvis: \"\(frase)\"")
        API.request("/api/jarvis", method: "POST",
                    body: ["testo": frase, "lang": lingua, "voce": true]) { [weak self] j, err in
            guard let self = self else { return }
            if let err = err {
                self.risposta.stringValue = err
                self.stato = .ascolto
                self.ascolto.avvia(lang: self.lingua)
                return
            }
            let testo = (j?["risposta"] as? String) ?? ""
            self.risposta.stringValue = testo
            if let azione = j?["azione"] as? [String: Any] {
                self.onAzione?(azione)
                if (azione["tipo"] as? String) == "ferma" { return self.chiudi() }
            }
            if let file = j?["file"] as? String {
                self.stato = .parlo
                Player.shared.play(path: file)
            } else {
                self.riprendiAscolto()
            }
        }
    }

    override func keyDown(with event: NSEvent) {
        if event.keyCode == 53 { chiudi() } else { super.keyDown(with: event) }
    }
}

// MARK: - scorciatoia globale

/// ⌥Spazio ovunque. Passa da Carbon perché è l'unica strada che non chiede
/// l'accesso all'accessibilità solo per leggere una combinazione di tasti.
enum Scorciatoia {
    private static var ref: EventHotKeyRef?
    static var azione: (() -> Void)?

    static func registra() {
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard),
                                 eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetApplicationEventTarget(), { _, _, _ in
            DispatchQueue.main.async { Scorciatoia.azione?() }
            return noErr
        }, 1, &spec, nil, nil)
        let id = EventHotKeyID(signature: OSType(0x504C4E43), id: 1)
        let esito = RegisterEventHotKey(UInt32(kVK_Space), UInt32(optionKey), id,
                                        GetApplicationEventTarget(), 0, &ref)
        Log.write(esito == noErr ? "scorciatoia ⌥Spazio registrata"
                                 : "scorciatoia ⌥Spazio occupata da un'altra app (\(esito))")
    }
}
