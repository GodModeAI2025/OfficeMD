// Zeichnet das OfficeMD-Icon (1024x1024 PNG). Aufruf: swift scripts/make-icon.swift out.png
import AppKit

let size = 1024.0
let image = NSImage(size: NSSize(width: size, height: size))
image.lockFocus()
let rect = NSRect(x: 0, y: 0, width: size, height: size).insetBy(dx: 100, dy: 100)
let shape = NSBezierPath(roundedRect: rect, xRadius: 185, yRadius: 185)
NSGradient(starting: NSColor(calibratedRed: 0.12, green: 0.37, blue: 0.55, alpha: 1),
           ending: NSColor(calibratedRed: 0.06, green: 0.22, blue: 0.35, alpha: 1))!.draw(in: shape, angle: -90)

// Dokumentblatt
let sheet = NSRect(x: 290, y: 250, width: 360, height: 470)
NSColor(calibratedWhite: 1, alpha: 0.96).setFill()
NSBezierPath(roundedRect: sheet, xRadius: 28, yRadius: 28).fill()
NSColor(calibratedRed: 0.12, green: 0.37, blue: 0.55, alpha: 0.35).setFill()
for (i, width) in [260.0, 220.0, 250.0, 180.0].enumerated() {
    NSBezierPath(roundedRect: NSRect(x: 340, y: 610 - Double(i) * 62, width: width, height: 26),
                 xRadius: 13, yRadius: 13).fill()
}

// Graph-Knoten als Zeichen für das eingebettete Wissen
let accent = NSColor(calibratedRed: 0.98, green: 0.71, blue: 0.24, alpha: 1)
let nodes = [NSPoint(x: 640, y: 330), NSPoint(x: 760, y: 450), NSPoint(x: 720, y: 250)]
accent.setStroke()
let edges = NSBezierPath()
edges.lineWidth = 18
edges.move(to: nodes[0]); edges.line(to: nodes[1])
edges.move(to: nodes[0]); edges.line(to: nodes[2])
edges.stroke()
accent.setFill()
for (i, point) in nodes.enumerated() {
    let r = i == 0 ? 58.0 : 42.0
    NSBezierPath(ovalIn: NSRect(x: point.x - r, y: point.y - r, width: 2 * r, height: 2 * r)).fill()
}
image.unlockFocus()

let rep = NSBitmapImageRep(data: image.tiffRepresentation!)!
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
