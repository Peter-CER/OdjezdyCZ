import Foundation
import SwiftUI

/// Transport modes, matching the bitmask in stops.json (1=tram 2=metro 4=bus 8=ferry).
struct Modes: OptionSet, Hashable {
    let rawValue: Int
    static let tram = Modes(rawValue: 1)
    static let metro = Modes(rawValue: 2)
    static let bus = Modes(rawValue: 4)
    static let ferry = Modes(rawValue: 8)
    static let train = Modes(rawValue: 16)
    static let trolleybus = Modes(rawValue: 32)

    init(rawValue: Int) { self.rawValue = rawValue }

    /// From a GTFS `route_type`.
    init(gtfsRouteType t: Int) {
        switch t {
        case 0, 7, 900: self = .tram
        case 1, 401, 402: self = .metro
        case 2, 100...199: self = .train
        case 4, 1000, 1200: self = .ferry
        case 11, 800: self = .trolleybus
        default: self = .bus
        }
    }

    var symbol: String {
        if contains(.metro) { return "tram.fill.tunnel" }
        if contains(.train) { return "train.side.front.car" }
        if contains(.tram) { return "tram.fill" }
        if contains(.ferry) { return "ferry.fill" }
        return "bus.fill"
    }

    /// Fallback badge colour when the operator doesn't publish one.
    var fallbackColor: Color {
        if contains(.metro) { return Color(red: 0.0, green: 0.36, blue: 0.67) }
        if contains(.tram) { return Color(red: 0.0, green: 0.55, blue: 0.45) }
        if contains(.ferry) { return Color(red: 0.12, green: 0.45, blue: 0.75) }
        if contains(.train) { return Color(red: 0.15, green: 0.30, blue: 0.60) }
        if contains(.trolleybus) { return Color(red: 0.55, green: 0.25, blue: 0.45) }
        return Color(red: 0.32, green: 0.34, blue: 0.38)
    }

    static let displayOrder: [Modes] = [.metro, .train, .tram, .trolleybus, .bus, .ferry]
    var all: [Modes] { Self.displayOrder.filter { contains($0) } }

    var title: String {
        if contains(.metro) { return "Metro" }
        if contains(.tram) { return "Tram" }
        if contains(.ferry) { return "Ferry" }
        if contains(.train) { return "Train" }
        if contains(.trolleybus) { return "Trolleybus" }
        return "Bus"
    }
}

struct LineInfo: Hashable {
    let number: String
    let mode: Modes
    let colorHex: String
}

/// One stop area from stops.json (both directions of a stop, sometimes several quays).
struct Stop: Hashable {
    let name: String
    let town: String
    let lat: Double
    let lon: Double
    let tpcs: [String]
    let modes: Modes
    let weekdayDepartures: Int
    let lines: [LineInfo]
    var platforms: [(lat: Double, lon: Double)] = []

    /// Walking-relevant distance: to the nearest platform, not the stop's centre.
    func distance(from lat: Double, _ lon: Double) -> Double {
        guard !platforms.isEmpty else { return StopIndex.distance(lat, lon, self.lat, self.lon) }
        return platforms.map { StopIndex.distance(lat, lon, $0.lat, $0.lon) }.min()!
    }

    static func == (a: Stop, b: Stop) -> Bool { a.name == b.name && a.lat == b.lat && a.lon == b.lon && a.tpcs == b.tpcs }
    func hash(into h: inout Hasher) { h.combine(name); h.combine(lat); h.combine(lon) }
}

/// What the user sees as one tab: same-named stop areas close together merged into one.
struct StopGroup: Identifiable, Hashable {
    let name: String
    let town: String
    let distance: Double          // metres, to the nearest member
    let lat: Double
    let lon: Double
    let tpcs: [String]
    let modes: Modes
    let weekdayDepartures: Int
    let lines: [LineInfo]
    var score: Double

    var id: String { "\(town)|\(name)" }

    /// Rough walking time: straight line × 1.25 for real streets, at 80 m/min (4.8 km/h).
    var walkSeconds: TimeInterval { (distance * 1.25 / 80 * 60).rounded() }
    var walkMinutes: Int { max(1, Int((walkSeconds / 60).rounded(.up))) }

    /// Close enough that walking time means something (searched stops can be far away).
    var isNearby: Bool { distance <= 2000 }

    /// A departure you can still catch if you leave now (allowing a bit of hurrying).
    func isCatchable(_ d: Departure, now: Date) -> Bool {
        !isNearby || d.expected.timeIntervalSince(now) >= walkSeconds * 0.75
    }

    /// Badge background + readable text colour for a line (e.g. yellow metro 54 gets dark text).
    /// Transport type of a line at this stop (fallback when a feed doesn't send it).
    func mode(forLine line: String) -> Modes {
        lines.first { $0.number == line }?.mode ?? .bus
    }

    func badgeColors(for d: Departure) -> (bg: Color, fg: Color) {
        badgeColors(forLine: d.line, mode: d.mode, fallbackHex: d.colorHex)
    }

    func badgeColors(forLine line: String, mode: Modes, fallbackHex: String? = nil) -> (bg: Color, fg: Color) {
        let known = lines.first(where: { $0.number == line && $0.mode == mode })?.colorHex
            ?? lines.first(where: { $0.number == line })?.colorHex
        let hex = (known?.isEmpty == false ? known : fallbackHex) ?? ""
        guard let bg = Color(hex: hex), let lum = Color.luminance(hex: hex) else {
            return (mode.fallbackColor, .white)
        }
        return (bg, lum > 0.6 ? Color.black.opacity(0.85) : .white)
    }
}

struct Departure: Identifiable, Hashable {
    enum Status: String { case planned, driving, arrived, passed, cancelled, unknown }

    let id: String
    let tpc: String               // timing point (quay) it departs from
    let line: String
    let mode: Modes
    let destination: String
    let planned: Date
    let expected: Date
    let status: Status
    let isLive: Bool
    /// Times known to the second (Golemio). MOTIS is per minute → no m:ss countdown.
    var precise = true
    /// Line colour sent with the departure (MOTIS), used when the stop has no colour for the line.
    var colorHex: String?

    var delayMinutes: Int { Int((expected.timeIntervalSince(planned) / 60).rounded()) }
    var isCancelled: Bool { status == .cancelled }
}

extension Color {
    init?(hex: String) {
        let s = hex.trimmingCharacters(in: CharacterSet(charactersIn: "# "))
        guard s.count == 6, let v = Int(s, radix: 16) else { return nil }
        // Some feeds publish white/near-white as the line colour; that's unreadable as a badge.
        let r = Double((v >> 16) & 0xFF) / 255, g = Double((v >> 8) & 0xFF) / 255, b = Double(v & 0xFF) / 255
        if r > 0.92 && g > 0.92 && b > 0.92 { return nil }
        self = Color(red: r, green: g, blue: b)
    }

    static func luminance(hex: String) -> Double? {
        let s = hex.trimmingCharacters(in: CharacterSet(charactersIn: "# "))
        guard s.count == 6, let v = Int(s, radix: 16) else { return nil }
        let r = Double((v >> 16) & 0xFF) / 255, g = Double((v >> 8) & 0xFF) / 255, b = Double(v & 0xFF) / 255
        return 0.299 * r + 0.587 * g + 0.114 * b
    }
}

/// Departures of one transport type at the selected stop.
struct ModeSection: Identifiable, Hashable {
    let mode: Modes
    let departures: [Departure]
    var hasMore = false        // more departures than shown (collapsed)
    var expanded = false
    var isFavourites = false   // "Your lines" section (starred lines, any mode)
    var id: Int { isFavourites ? -1 : mode.rawValue }
}
