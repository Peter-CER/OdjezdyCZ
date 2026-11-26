import Foundation

/// A source of live departures for one stop group (all its quays/platforms).
/// Golemio (Prague, with key), MOTIS (all of Czechia, no key), PragueProvider (picks between them).
protocol DepartureProvider {
    func board(for group: StopGroup) async throws -> StopBoard
}

/// What a stop's sign shows: departures plus any service notices (disruptions, stop moved, …).
struct StopBoard {
    var departures: [Departure]
    var notices: [String] = []
    var source = "Golemio"
}

enum DepartureError: LocalizedError {
    case unavailable
    case missingKey
    var errorDescription: String? {
        switch self {
        case .unavailable: return "Live data unavailable"
        case .missingKey: return "API key needed"
        }
    }
}
