// Disegna l'icona dell'app: la rosa dei venti della plancia.
// Si esegue con `swift mac/makeicon.swift <cartella-iconset>`.

import AppKit
import CoreGraphics
import Foundation

let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "./Plancia.iconset"
try? FileManager.default.createDirectory(atPath: out, withIntermediateDirectories: true)

func disegna(lato: Int) -> Data? {
    let s = CGFloat(lato)
    guard let ctx = CGContext(data: nil, width: lato, height: lato, bitsPerComponent: 8,
                              bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(),
                              bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
    else { return nil }

    // fondo: quadrato arrotondato con una leggera profondità
    let inset = s * 0.055
    let rect = CGRect(x: inset, y: inset, width: s - inset * 2, height: s - inset * 2)
    let path = CGPath(roundedRect: rect, cornerWidth: s * 0.225, cornerHeight: s * 0.225,
                      transform: nil)
    ctx.addPath(path)
    ctx.clip()
    let colori = [CGColor(red: 0.10, green: 0.13, blue: 0.18, alpha: 1),
                  CGColor(red: 0.05, green: 0.06, blue: 0.09, alpha: 1)] as CFArray
    if let grad = CGGradient(colorsSpace: CGColorSpaceCreateDeviceRGB(), colors: colori,
                             locations: [0, 1]) {
        ctx.drawLinearGradient(grad, start: CGPoint(x: 0, y: s), end: CGPoint(x: 0, y: 0),
                               options: [])
    }
    ctx.resetClip()

    let centro = CGPoint(x: s / 2, y: s / 2)
    let raggio = s * 0.31
    let ambra = CGColor(red: 0.949, green: 0.635, blue: 0.361, alpha: 1)

    // cerchio esterno
    ctx.setStrokeColor(ambra)
    ctx.setLineWidth(max(1, s * 0.045))
    ctx.addArc(center: centro, radius: raggio, startAngle: 0, endAngle: .pi * 2, clockwise: false)
    ctx.strokePath()

    // quattro punte cardinali
    ctx.setLineCap(.round)
    ctx.setLineWidth(max(1, s * 0.045))
    let dentro = raggio * 0.42
    let fuori = raggio * 1.32
    for i in 0..<4 {
        let a = CGFloat(i) * .pi / 2
        ctx.move(to: CGPoint(x: centro.x + cos(a) * dentro, y: centro.y + sin(a) * dentro))
        ctx.addLine(to: CGPoint(x: centro.x + cos(a) * fuori, y: centro.y + sin(a) * fuori))
    }
    ctx.strokePath()

    // punte diagonali, più corte
    ctx.setLineWidth(max(1, s * 0.022))
    ctx.setStrokeColor(CGColor(red: 0.949, green: 0.635, blue: 0.361, alpha: 0.55))
    for i in 0..<4 {
        let a = CGFloat(i) * .pi / 2 + .pi / 4
        ctx.move(to: CGPoint(x: centro.x + cos(a) * raggio * 0.55, y: centro.y + sin(a) * raggio * 0.55))
        ctx.addLine(to: CGPoint(x: centro.x + cos(a) * raggio * 0.92, y: centro.y + sin(a) * raggio * 0.92))
    }
    ctx.strokePath()

    // perno
    ctx.setFillColor(ambra)
    ctx.addArc(center: centro, radius: raggio * 0.24, startAngle: 0, endAngle: .pi * 2,
               clockwise: false)
    ctx.fillPath()

    guard let img = ctx.makeImage() else { return nil }
    let rep = NSBitmapImageRep(cgImage: img)
    return rep.representation(using: .png, properties: [:])
}

let misure: [(String, Int)] = [
    ("icon_16x16", 16), ("icon_16x16@2x", 32),
    ("icon_32x32", 32), ("icon_32x32@2x", 64),
    ("icon_128x128", 128), ("icon_128x128@2x", 256),
    ("icon_256x256", 256), ("icon_256x256@2x", 512),
    ("icon_512x512", 512), ("icon_512x512@2x", 1024),
]
for (nome, lato) in misure {
    if let data = disegna(lato: lato) {
        try? data.write(to: URL(fileURLWithPath: "\(out)/\(nome).png"))
    }
}
print("iconset in \(out)")
