// Renders the Odjezdy app icon (dark "stop sign" tile with a white tram) into the asset catalog.
// Run: swift tools/make_icon.swift
import AppKit

let out = "Odjezdy/Assets.xcassets/AppIcon.appiconset"

func render(_ px: Int) -> Data {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px, bitsPerSample: 8,
                               samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                               colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    let s = CGFloat(px)
    // macOS icon grid: 824/1024 tile, ~22% corner radius.
    let inset = s * 100 / 1024
    let tile = NSRect(x: inset, y: inset, width: s - 2 * inset, height: s - 2 * inset)
    let path = NSBezierPath(roundedRect: tile, xRadius: tile.width * 0.225, yRadius: tile.width * 0.225)
    NSGraphicsContext.current?.saveGraphicsState()
    let shadow = NSShadow()
    shadow.shadowColor = NSColor.black.withAlphaComponent(0.35)
    shadow.shadowOffset = NSSize(width: 0, height: -s * 0.012)
    shadow.shadowBlurRadius = s * 0.025
    shadow.set()
    NSColor.black.setFill()
    path.fill()
    NSGraphicsContext.current?.restoreGraphicsState()
    NSGradient(colors: [NSColor(red: 0.16, green: 0.20, blue: 0.27, alpha: 1),
                        NSColor(red: 0.06, green: 0.07, blue: 0.09, alpha: 1)])!
        .draw(in: path, angle: -90)
    // Tram glyph + amber "next departure" bar (like the LED line on a stop display), laid out as
    // one group centred in the tile, so the icon isn't top-heavy.
    let cfg = NSImage.SymbolConfiguration(pointSize: tile.height * 0.44, weight: .semibold)
        .applying(.init(paletteColors: [.white]))
    guard let sym = NSImage(systemSymbolName: "tram.fill", accessibilityDescription: nil)?
        .withSymbolConfiguration(cfg) else { fatalError("no symbol") }
    let sz = sym.size
    let barH = tile.height * 0.045
    let gap = tile.height * 0.01      // the symbol has built-in padding below the rails
    let groupH = sz.height + gap + barH
    let bottom = tile.midY - groupH / 2
    let bar = NSRect(x: tile.minX + tile.width * 0.24, y: bottom, width: tile.width * 0.52, height: barH)
    NSColor(red: 1.0, green: 0.69, blue: 0.0, alpha: 1).setFill()
    NSBezierPath(roundedRect: bar, xRadius: barH / 2, yRadius: barH / 2).fill()
    sym.draw(in: NSRect(x: tile.midX - sz.width / 2, y: bar.maxY + gap, width: sz.width, height: sz.height))
    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}

var images: [[String: String]] = []
for pt in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let name = "icon_\(pt)x\(pt)\(scale == 2 ? "@2x" : "").png"
        try! render(pt * scale).write(to: URL(fileURLWithPath: "\(out)/\(name)"))
        images.append(["idiom": "mac", "size": "\(pt)x\(pt)", "scale": "\(scale)x", "filename": name])
    }
}
let contents: [String: Any] = ["images": images, "info": ["version": 1, "author": "xcode"]]
try! JSONSerialization.data(withJSONObject: contents, options: [.prettyPrinted, .sortedKeys])
    .write(to: URL(fileURLWithPath: "\(out)/Contents.json"))
print("Wrote \(images.count) icon images to \(out)")
