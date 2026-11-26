import Foundation
import CoreLocation

/// Offline "is this point inside the country?" check against a bundled simplified border
/// (Natural Earth, built by tools/build_border.py).
struct CountryBorder {
    private let rings: [[(lat: Double, lon: Double)]]

    init(resource: String, bundle: Bundle = .main) {
        var rings: [[(Double, Double)]] = []
        if let url = bundle.url(forResource: resource, withExtension: "json"),
           let data = try? Data(contentsOf: url),
           let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let raw = root["rings"] as? [[[Double]]] {
            rings = raw.map { $0.compactMap { $0.count == 2 ? ($0[0], $0[1]) : nil } }
        }
        self.rings = rings
    }

    /// True when the point, or any point ~2.5 km around it, is inside. The buffer absorbs the
    /// simplified border's error so river-border towns (Český Těšín, Aš, …) count; a few metres
    /// across the border may count too, which is harmless.
    func contains(_ c: CLLocationCoordinate2D) -> Bool {
        guard !rings.isEmpty else { return true }   // no data → never hide
        let dLat = 0.0225, dLon = 0.035   // ≈2.5 km: Natural Earth is ~1.5 km off at Český Těšín
        let probes = [(0.0, 0.0), (dLat, 0), (-dLat, 0), (0, dLon), (0, -dLon),
                      (dLat * 0.7, dLon * 0.7), (dLat * 0.7, -dLon * 0.7),
                      (-dLat * 0.7, dLon * 0.7), (-dLat * 0.7, -dLon * 0.7)]
        return probes.contains { inside(c.latitude + $0.0, c.longitude + $0.1) }
    }

    private func inside(_ lat: Double, _ lon: Double) -> Bool {
        for ring in rings {
            var hit = false
            var j = ring.count - 1
            for i in 0..<ring.count {
                let (yi, xi) = ring[i], (yj, xj) = ring[j]
                if (yi > lat) != (yj > lat), lon < (xj - xi) * (lat - yi) / (yj - yi) + xi { hit.toggle() }
                j = i
            }
            if hit { return true }
        }
        return false
    }
}
