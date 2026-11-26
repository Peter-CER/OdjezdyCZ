import Foundation
import CoreLocation

/// The supported area: Prague Integrated Transport (PID) — Prague and Central Bohemia.
/// Kept as an enum so other networks can be added again later.
enum Region: String, CaseIterable {
    case prague

    var name: String { "Prague (PID)" }

    var stopsResource: String { "stops_\(rawValue)" }

    /// Coarse box around the network; the precise check is "is there a PID stop within 1 km".
    var bounds: (lat: ClosedRange<Double>, lon: ClosedRange<Double>) { (49.4...50.8, 13.3...15.6) }

    /// Refresh interval while the popover is open.
    var refreshInterval: UInt64 { 15 }

    func roughlyContains(_ c: CLLocationCoordinate2D) -> Bool {
        bounds.lat.contains(c.latitude) && bounds.lon.contains(c.longitude)
    }
}
