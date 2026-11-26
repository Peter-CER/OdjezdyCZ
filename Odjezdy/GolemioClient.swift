import Foundation

/// Prague (PID) live departures from the official Golemio API. Needs a free key:
/// https://api.golemio.cz/api-keys  — stored in UserDefaults under `golemioKey`.
struct GolemioClient: DepartureProvider {
    static let keyDefaultsKey = "golemioKey"
    static let signUpURL = URL(string: "https://api.golemio.cz/api-keys/auth/sign-up")!

    static var apiKey: String? {
        let k = UserDefaults.standard.string(forKey: keyDefaultsKey)?.trimmingCharacters(in: .whitespacesAndNewlines)
        return (k?.isEmpty ?? true) ? nil : k
    }

    private let session: URLSession

    init() {
        let cfg = URLSessionConfiguration.ephemeral
        cfg.timeoutIntervalForRequest = 8
        cfg.timeoutIntervalForResource = 12
        cfg.httpAdditionalHeaders = ["User-Agent": "Odjezdy/1.0", "Accept": "application/json"]
        session = URLSession(configuration: cfg)
    }

    func board(for group: StopGroup) async throws -> StopBoard {
        guard let key = Self.apiKey else { throw DepartureError.missingKey }
        var comps = URLComponents(string: "https://api.golemio.cz/v2/pid/departureboards")!
        comps.queryItems = group.tpcs.map { URLQueryItem(name: "ids[]", value: $0) } + [
            URLQueryItem(name: "minutesAfter", value: "90"),
            URLQueryItem(name: "limit", value: "150"),
            URLQueryItem(name: "order", value: "real"),
            URLQueryItem(name: "mode", value: "departures"),
            URLQueryItem(name: "airCondition", value: "false"),
        ]
        var req = URLRequest(url: comps.url!)
        req.setValue(key, forHTTPHeaderField: "X-Access-Token")
        let (data, resp) = try await session.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if code == 401 || code == 403 { throw DepartureError.missingKey }
        guard code == 200 else { throw DepartureError.unavailable }
        return try Self.parse(data)
    }

    private struct Info: Decodable {
        let text: String?
        let text_en: String?
        let valid_from: String?
        let valid_to: String?
    }

    // MARK: - Parsing

    private struct Board: Decodable { let departures: [Dep]?; let infotexts: [Info]? }
    private struct Dep: Decodable {
        struct Time: Decodable { let predicted: String?; let scheduled: String? }
        struct Delay: Decodable { let is_available: Bool? }
        struct Route: Decodable { let short_name: String?; let type: Int? }
        struct StopRef: Decodable { let id: String? }
        struct Trip: Decodable { let headsign: String?; let id: String?; let is_canceled: Bool?; let is_at_stop: Bool? }
        let departure_timestamp: Time?
        let delay: Delay?
        let route: Route?
        let stop: StopRef?
        let trip: Trip?
    }

    private static let iso: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
    private static let isoPlain: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()
    private static func date(_ s: String?) -> Date? {
        guard let s else { return nil }
        return iso.date(from: s) ?? isoPlain.date(from: s)
    }

    static func parse(_ data: Data) throws -> StopBoard {
        let board = try JSONDecoder().decode(Board.self, from: data)
        let now = Date()
        var notices: [String] = []
        for i in board.infotexts ?? [] {
            if let from = date(i.valid_from), from > now { continue }
            if let to = date(i.valid_to), to < now { continue }
            if let t = (i.text_en ?? i.text)?.trimmingCharacters(in: .whitespacesAndNewlines),
               !t.isEmpty, !notices.contains(t) { notices.append(t) }
        }
        var out: [Departure] = []
        for d in board.departures ?? [] {
            guard let line = d.route?.short_name,
                  let planned = date(d.departure_timestamp?.scheduled) else { continue }
            let expected = date(d.departure_timestamp?.predicted) ?? planned
            let live = d.delay?.is_available ?? false
            let cancelled = d.trip?.is_canceled ?? false
            let stopID = d.stop?.id ?? ""
            out.append(Departure(
                id: (d.trip?.id ?? UUID().uuidString) + "@" + stopID, tpc: stopID, line: line,
                mode: Modes(gtfsRouteType: d.route?.type ?? 3), destination: d.trip?.headsign ?? "",
                planned: planned, expected: expected,
                status: cancelled ? .cancelled : (d.trip?.is_at_stop == true ? .arrived : (live ? .driving : .planned)),
                isLive: live && !cancelled))
        }
        out.sort { $0.expected < $1.expected }
        return StopBoard(departures: out, notices: notices)
    }
}
