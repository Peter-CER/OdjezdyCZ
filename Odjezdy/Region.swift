import Foundation
import CoreLocation

/// Supported areas.
///  • prague: PID (Prague + Central Bohemia) — bundled stop list, Golemio (with key) or MOTIS.
///  • czechia: the rest of the country — stops, search and departures from MOTIS.
enum Region: String, CaseIterable {
    case prague, czechia

    var name: String {
        switch self {
        case .prague: return "Prague (PID)"
        case .czechia: return "Czechia"
        }
    }

    var stopsResource: String { "stops_\(rawValue)" }

    /// Coarse box; the precise checks are "PID stop within 1 km" / the Czech border.
    var bounds: (lat: ClosedRange<Double>, lon: ClosedRange<Double>) {
        switch self {
        case .prague: return (49.4...50.8, 13.3...15.6)
        case .czechia: return (48.5...51.1, 12.0...18.9)
        }
    }

    /// Refresh interval while the popover is open. MOTIS is one volunteer's server and updates
    /// per minute, so it's polled more gently.
    var refreshInterval: UInt64 { self == .prague && GolemioClient.apiKey != nil ? 15 : 30 }

    func roughlyContains(_ c: CLLocationCoordinate2D) -> Bool {
        bounds.lat.contains(c.latitude) && bounds.lon.contains(c.longitude)
    }
}
