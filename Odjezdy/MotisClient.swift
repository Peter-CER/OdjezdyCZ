import Foundation
import CoreLocation

/// Departures, nearby stops and stop search for all of Czechia from the community MOTIS
/// instance at motis.obehy.cz (no key; the operator asks that apps identify themselves in the
/// User-Agent). Real-time data for all trains and most regions' buses; timetable elsewhere.
/// Times are to the minute, so departures are marked `precise = false` (no m:ss countdown).
/// API: https://redocly.github.io/redoc?url=https://raw.githubusercontent.com/motis-project/motis/refs/tags/v2.11.4/openapi.yaml
struct MotisClient: DepartureProvider {
    static let base = "https://motis.obehy.cz/api/v1"
    static let userAgent = "Odjezdy/\(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "1") (+https://github.com/rp20048/OdjezdyPraha)"

    private static let session: URLSession = {
        let cfg = URLSessionConfiguration.ephemeral
        cfg.timeoutIntervalForRequest = 10
        cfg.timeoutIntervalForResource = 15
        cfg.httpAdditionalHeaders = ["User-Agent": userAgent, "Accept": "application/json"]
        return URLSession(configuration: cfg)
    }()

    private static func get(_ path: String, _ query: [String: String]) async throws -> Data {
        var c = URLComponents(string: base + path)!
        c.queryItems = query.map { URLQueryItem(name: $0.key, value: $0.value) }
        let (data, resp) = try await session.data(from: c.url!)
        guard (resp as? HTTPURLResponse)?.statusCode == 200 else { throw DepartureError.unavailable }
        return data
    }

    // MARK: - Departures

    func board(for group: StopGroup) async throws -> StopBoard {
        try await Self.board(stopIDs: group.tpcs, town: group.town)
    }

    /// `town`: destinations in the same town lose their "Town," prefix ("Brno,Juliánov" → "Juliánov").
    static func board(stopIDs: [String], town: String = "") async throws -> StopBoard {
        var all: [Departure] = []
        var notices: [String] = []
        var failures = 0
        await withTaskGroup(of: ([Departure], [String])?.self) { tg in
            for id in stopIDs {
                tg.addTask { try? await stopTimes(id) }
            }
            for await r in tg {
                if let (deps, alerts) = r {
                    all += deps
                    for a in alerts where !notices.contains(a) { notices.append(a) }
                } else { failures += 1 }
            }
        }
        if failures == stopIDs.count && !stopIDs.isEmpty { throw DepartureError.unavailable }
        all.sort { $0.expected < $1.expected }
        if !town.isEmpty {
            let prefix = town + ","
            all = all.map { d in
                guard d.destination.hasPrefix(prefix) else { return d }
                var c = Departure(id: d.id, tpc: d.tpc, line: d.line, mode: d.mode,
                                  destination: String(d.destination.dropFirst(prefix.count)),
                                  planned: d.planned, expected: d.expected, status: d.status, isLive: d.isLive)
                c.precise = d.precise
                c.colorHex = d.colorHex
                return c
            }
        }
        return StopBoard(departures: all, notices: notices, source: "MOTIS")
    }

    private static let iso: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()
    private static let isoFrac: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
    private static func date(_ s: Any?) -> Date? {
        guard let s = s as? String else { return nil }
        return iso.date(from: s) ?? isoFrac.date(from: s)
    }

    static func mode(_ m: String?) -> Modes {
        switch m ?? "" {
        case "SUBWAY", "METRO": return .metro
        case "TRAM", "FUNICULAR", "CABLE_CAR", "AERIAL_LIFT": return .tram
        case "FERRY": return .ferry
        case "TROLLEYBUS": return .trolleybus
        case "BUS", "COACH": return .bus
        default: return .train   // RAIL, REGIONAL_RAIL, LONG_DISTANCE, HIGHSPEED_RAIL, NIGHT_RAIL, SUBURBAN, …
        }
    }

    /// "S3 (Os 4658)" → "S3"; "R 1234" stays.
    static func shortLine(_ s: String) -> String {
        if let i = s.range(of: " (") { return String(s[..<i.lowerBound]) }
        return s
    }

    /// Departures plus stop alerts (`place.alerts`: headerText / descriptionText).
    private static func stopTimes(_ stopID: String) async throws -> ([Departure], [String]) {
        let data = try await get("/stoptimes", ["stopId": stopID, "n": "40"])
        guard let root = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let list = root["stopTimes"] as? [[String: Any]] else { return ([], []) }
        let now = Date()
        var alerts: [String] = []
        for x in list {
            for a in ((x["place"] as? [String: Any])?["alerts"] as? [[String: Any]]) ?? [] {
                let header = (a["headerText"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                let desc = (a["descriptionText"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                let text = header.isEmpty ? desc : (desc.isEmpty || desc == header ? header : "\(header): \(desc)")
                if !text.isEmpty, !alerts.contains(text) { alerts.append(text) }
            }
        }
        let deps: [Departure] = list.compactMap { x in
            guard let place = x["place"] as? [String: Any],
                  let planned = date(place["scheduledDeparture"]) else { return nil }
            let expected = date(place["departure"]) ?? planned
            if expected < now.addingTimeInterval(-30) { return nil }
            let rawLine = (x["routeShortName"] as? String) ?? (x["displayName"] as? String) ?? ""
            guard !rawLine.isEmpty else { return nil }
            let live = x["realTime"] as? Bool ?? false
            let cancelled = (x["cancelled"] as? Bool ?? false) || (place["cancelled"] as? Bool ?? false)
            var d = Departure(
                id: "\(x["tripId"] as? String ?? UUID().uuidString)@\(place["stopId"] as? String ?? stopID)",
                tpc: stopID, line: shortLine(rawLine), mode: mode(x["mode"] as? String),
                destination: (x["headsign"] as? String) ?? "",
                planned: planned, expected: expected,
                status: cancelled ? .cancelled : (live ? .driving : .planned), isLive: live && !cancelled)
            d.precise = false
            d.colorHex = x["routeColor"] as? String
            return d
        }
        return (deps, alerts)
    }

    // MARK: - Stops

    /// "Brno,Náměstí Svobody" → ("Brno", "Náměstí Svobody"); "Praha hlavní nádraží" stays whole.
    static func split(_ full: String) -> (town: String, name: String) {
        if let i = full.firstIndex(of: ",") {
            return (String(full[..<i]), String(full[full.index(after: i)...]))
        }
        return ("", full)
    }

    /// Stops (grouped by MOTIS parent station) in a box around `c`.
    private static func stops(around c: CLLocationCoordinate2D, radius: Double) async throws -> [Stop] {
        let dLat = radius / 111_000, dLon = radius / (111_000 * cos(c.latitude * .pi / 180))
        let data = try await get("/map/stops", [
            "min": String(format: "%.5f,%.5f", c.latitude - dLat, c.longitude - dLon),
            "max": String(format: "%.5f,%.5f", c.latitude + dLat, c.longitude + dLon),
        ])
        guard let list = try JSONSerialization.jsonObject(with: data) as? [[String: Any]] else { return [] }
        var byParent: [String: [[String: Any]]] = [:]
        for p in list {
            guard let id = (p["parentId"] as? String) ?? (p["stopId"] as? String) else { continue }
            byParent[id, default: []].append(p)
        }
        return byParent.compactMap { id, plats in
            guard let full = plats.first?["name"] as? String else { return nil }
            let coords = plats.compactMap { p -> (lat: Double, lon: Double)? in
                guard let la = p["lat"] as? Double, let lo = p["lon"] as? Double else { return nil }
                return (la, lo)
            }
            guard !coords.isEmpty else { return nil }
            var modes = Modes()
            for p in plats { for m in (p["modes"] as? [String]) ?? [] { modes.formUnion(mode(m)) } }
            let importance = plats.compactMap { $0["importance"] as? Double }.max() ?? 0
            let (town, name) = split(full)
            var s = Stop(name: name, town: town,
                         lat: coords.map(\.lat).reduce(0, +) / Double(coords.count),
                         lon: coords.map(\.lon).reduce(0, +) / Double(coords.count),
                         tpcs: [id], modes: modes,
                         weekdayDepartures: Int(importance * 20_000), lines: [])
            s.platforms = coords
            return s
        }
    }

    /// The 3 most sensible nearby stops (same ranking as the bundled Prague list:
    /// busyness × mode bonus / distance), using MOTIS's importance as busyness.
    static func nearbyStops(_ c: CLLocationCoordinate2D, limit: Int = 3, preferred: String? = nil) async throws -> [StopGroup] {
        var found = try await stops(around: c, radius: 650)
        if Set(found.map(\.name)).count < 3 { found = try await stops(around: c, radius: 1500) }
        let candidates = found.map { ($0, $0.distance(from: c.latitude, c.longitude)) }
        return StopIndex.group(candidates, preferred: preferred)
            .sorted { $0.score > $1.score }
            .prefix(limit).map { $0 }
    }

    static func search(_ text: String, from loc: CLLocationCoordinate2D?) async throws -> [StopGroup] {
        var q = ["text": text, "type": "STOP"]
        if let loc { q["place"] = String(format: "%.5f,%.5f", loc.latitude, loc.longitude); q["placeBias"] = "5" }
        let data = try await get("/geocode", q)
        guard let list = try JSONSerialization.jsonObject(with: data) as? [[String: Any]] else { return [] }
        var seen = Set<String>()
        return list.prefix(12).compactMap { r in
            guard let id = r["id"] as? String, let full = r["name"] as? String,
                  let la = r["lat"] as? Double, let lo = r["lon"] as? Double,
                  seen.insert(full).inserted else { return nil }
            let (town, name) = split(full)
            let dist = loc.map { StopIndex.distance($0.latitude, $0.longitude, la, lo) } ?? 0
            return StopGroup(name: name, town: town, distance: dist, lat: la, lon: lo, tpcs: [id],
                             modes: Modes(), weekdayDepartures: 0, lines: [], score: 0)
        }
        .prefix(8).map { $0 }
    }

    // MARK: - Prague without a Golemio key

    /// MOTIS station ids for a stop from the bundled PID list: same-named MOTIS stops within
    /// ~350 m of it. Cached per stop.
    static func motisIDs(forPIDStop g: StopGroup) async -> [String] {
        let key = "motisIDs"
        var map = UserDefaults.standard.dictionary(forKey: key) as? [String: [String]] ?? [:]
        if let ids = map[g.id], !ids.isEmpty { return ids }
        guard let near = try? await stops(around: CLLocationCoordinate2D(latitude: g.lat, longitude: g.lon), radius: 350)
        else { return [] }
        let want = StopIndex.normalize(g.name)
        let ids = near.filter { StopIndex.normalize($0.name) == want }.flatMap(\.tpcs)
        if !ids.isEmpty {
            map[g.id] = ids
            UserDefaults.standard.set(map, forKey: key)
        }
        return ids
    }
}

/// Prague: Golemio when a key is set (second-accurate, live buses, notices); MOTIS otherwise,
/// or when Golemio rejects the key / is down.
struct PragueProvider: DepartureProvider {
    let golemio = GolemioClient()

    func board(for group: StopGroup) async throws -> StopBoard {
        if GolemioClient.apiKey != nil {
            do { return try await golemio.board(for: group) }
            catch { ovLog("Golemio failed (\(error.localizedDescription)), using MOTIS") }
        }
        let ids = await MotisClient.motisIDs(forPIDStop: group)
        guard !ids.isEmpty else { throw DepartureError.unavailable }
        return try await MotisClient.board(stopIDs: ids, town: "Praha")
    }
}
