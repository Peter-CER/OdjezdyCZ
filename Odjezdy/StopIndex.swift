import Foundation
import CoreLocation

/// Loads the bundled stop list and picks the most sensible nearby stops.
final class StopIndex {
    private(set) var stops: [Stop] = []

    init(resource: String, bundle: Bundle = .main) {
        guard let url = bundle.url(forResource: resource, withExtension: "json"),
              let data = try? Data(contentsOf: url) else { return }
        stops = Self.parse(data)
    }

    init(data: Data) { stops = Self.parse(data) }

    static func parse(_ data: Data) -> [Stop] {
        guard let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let rows = root["stops"] as? [[Any]] else { return [] }
        var out: [Stop] = []
        out.reserveCapacity(rows.count)
        for r in rows where r.count >= 8 {
            guard let name = r[0] as? String, let town = r[1] as? String,
                  let lat = (r[2] as? NSNumber)?.doubleValue, let lon = (r[3] as? NSNumber)?.doubleValue,
                  let tpcs = r[4] as? [String], let modes = (r[5] as? NSNumber)?.intValue,
                  let deps = (r[6] as? NSNumber)?.intValue, let lines = r[7] as? [[Any]] else { continue }
            let li: [LineInfo] = lines.compactMap { l in
                guard l.count >= 3, let n = l[0] as? String, let m = (l[1] as? NSNumber)?.intValue,
                      let c = l[2] as? String else { return nil }
                return LineInfo(number: n, mode: Modes(rawValue: m), colorHex: c)
            }
            var stop = Stop(name: name, town: town, lat: lat, lon: lon, tpcs: tpcs,
                            modes: Modes(rawValue: modes), weekdayDepartures: deps, lines: li)
            if r.count >= 9, let plats = r[8] as? [[Any]] {
                stop.platforms = plats.compactMap { p in
                    guard p.count >= 2, let la = (p[0] as? NSNumber)?.doubleValue,
                          let lo = (p[1] as? NSNumber)?.doubleValue else { return nil }
                    return (la, lo)
                }
            }
            out.append(stop)
        }
        return out
    }

    /// Fast equirectangular distance in metres; plenty accurate at walking scale.
    static func distance(_ lat1: Double, _ lon1: Double, _ lat2: Double, _ lon2: Double) -> Double {
        let k = Double.pi / 180
        let x = (lon2 - lon1) * k * cos((lat1 + lat2) / 2 * k)
        let y = (lat2 - lat1) * k
        return sqrt(x * x + y * y) * 6_371_000
    }

    func hasStop(near c: CLLocationCoordinate2D, within metres: Double) -> Bool {
        stops.contains { s in
            s.weekdayDepartures > 0 && abs(s.lat - c.latitude) < 0.01 && abs(s.lon - c.longitude) < 0.015 &&
                s.distance(from: c.latitude, c.longitude) <= metres
        }
    }

    /// Merge same-named areas within 150 m (e.g. bus vs tram side of a hub) and score them:
    /// log(1 + weekday departures) × mode bonus / (1 + walk/250 m) × preference boost.
    static func group(_ candidates: [(Stop, Double)], preferred: String? = nil) -> [StopGroup] {
        var groups: [[(Stop, Double)]] = []
        for c in candidates {
            if let i = groups.firstIndex(where: { g in
                g[0].0.name == c.0.name && g[0].0.town == c.0.town &&
                    distance(g[0].0.lat, g[0].0.lon, c.0.lat, c.0.lon) < 150
            }) {
                groups[i].append(c)
            } else {
                groups.append([c])
            }
        }
        return groups.map { g in
            let deps = g.reduce(0) { $0 + $1.0.weekdayDepartures }
            let modes = g.reduce(Modes()) { $0.union($1.0.modes) }
            let dist = g.map(\.1).min() ?? 0
            var seen = Set<String>(), lines: [LineInfo] = []
            for (s, _) in g { for l in s.lines where seen.insert("\(l.mode.rawValue)\(l.number)").inserted { lines.append(l) } }
            let bonus = modes.contains(.metro) ? 1.3 : modes.contains(.tram) ? 1.15 : modes.contains(.ferry) ? 1.1 : 1.0
            var score = log(1 + Double(deps)) * bonus / (1 + dist / 250)
            let id = "\(g[0].0.town)|\(g[0].0.name)"
            if id == preferred { score *= 1.6 }
            return StopGroup(name: g[0].0.name, town: g[0].0.town, distance: dist,
                             lat: g[0].0.lat, lon: g[0].0.lon,
                             tpcs: Array(Set(g.flatMap(\.0.tpcs))).sorted(), modes: modes,
                             weekdayDepartures: deps, lines: lines, score: score)
        }
    }

    static func normalize(_ s: String) -> String {
        s.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: nil)
            .replacingOccurrences(of: "-", with: " ")
    }

    /// Stops of this region matching `query` (diacritics/case-insensitive, name or "town name"),
    /// best first: name starts with it, then a word starts with it, then contains; busier first.
    private lazy var normalizedNames: [(name: String, full: String)] =
        stops.map { (Self.normalize($0.name), Self.normalize($0.town + " " + $0.name)) }

    func search(_ query: String, from loc: CLLocationCoordinate2D?, limit: Int = 8) -> [StopGroup] {
        let q = Self.normalize(query.trimmingCharacters(in: .whitespaces))
        guard q.count >= 2 else { return [] }
        var hits: [(Stop, Int)] = []
        for (i, s) in stops.enumerated() where s.weekdayDepartures > 0 {
            let (name, full) = normalizedNames[i]
            let rank: Int
            if name.hasPrefix(q) { rank = 0 }
            else if name.contains(" " + q) || name.contains("," + q) { rank = 1 }
            else if name.contains(q) || full.contains(q) { rank = 2 }
            else { continue }
            hits.append((s, rank))
        }
        let rankOf = Dictionary(grouping: hits, by: { "\($0.0.town)|\($0.0.name)" }).mapValues { $0.map(\.1).min()! }
        let dist: (Stop) -> Double = { s in loc.map { s.distance(from: $0.latitude, $0.longitude) } ?? 0 }
        // Busy stops first, a better text match is worth ~4.5× the departures, nearer is a bit better.
        func score(_ g: StopGroup) -> Double {
            log(1 + Double(g.weekdayDepartures)) - 1.5 * Double(rankOf[g.id] ?? 3) - g.distance / 20_000
        }
        return Self.group(hits.map { ($0.0, dist($0.0)) })
            .sorted { score($0) > score($1) }
            .prefix(limit).map { $0 }
    }

    /// Up to `limit` stop groups, best first.
    /// Score = log(1 + weekday departures) × mode bonus / (1 + walk/250 m) × preference boost.
    func bestStops(near loc: CLLocationCoordinate2D, limit: Int = 3,
                   preferred: String? = nil) -> [StopGroup] {
        // 1. Candidates: everything within 600 m, or the nearest 15 if that's too few.
        var near: [(Stop, Double)] = []
        for s in stops where s.weekdayDepartures > 0 {
            // cheap bounding box (~2.5 km) before the real distance
            if abs(s.lat - loc.latitude) > 0.025 || abs(s.lon - loc.longitude) > 0.04 { continue }
            near.append((s, s.distance(from: loc.latitude, loc.longitude)))
        }
        near.sort { $0.1 < $1.1 }
        let within = near.filter { $0.1 <= 600 }
        let candidates = within.count >= 3 ? within : Array(near.prefix(15))

        var result = Self.group(candidates, preferred: preferred)
        result.sort { $0.score > $1.score }
        return Array(result.prefix(limit))
    }
}
