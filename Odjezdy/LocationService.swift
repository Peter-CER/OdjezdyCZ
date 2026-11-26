import Foundation
import CoreLocation

/// Location, used sparingly:
///  • `currentLocation()` — one fix when the popover opens (reused for 2 minutes).
///  • significant-change monitoring — passive, Wi-Fi based, only to decide whether to show the icon.
@MainActor
final class LocationService: NSObject, ObservableObject {
    enum LocationError: LocalizedError {
        case denied, unavailable
        var errorDescription: String? {
            switch self {
            case .denied: return "Location access is off"
            case .unavailable: return "Couldn't determine your location"
            }
        }
    }

    @Published private(set) var authorization: CLAuthorizationStatus
    private(set) var lastLocation: CLLocation?

    /// Called whenever a new location arrives (any source). Used for the NL check.
    var onLocation: ((CLLocation) -> Void)?
    var onAuthorizationChange: ((CLAuthorizationStatus) -> Void)?

    private let manager = CLLocationManager()
    private var waiters: [CheckedContinuation<CLLocation, Error>] = []

    /// `defaults write io.github.rp20048.Odjezdy debugLocation "50.0875,14.4213"` to fake a location while testing.
    private var debugLocation: CLLocation? {
        guard let s = UserDefaults.standard.string(forKey: "debugLocation") else { return nil }
        let p = s.split(separator: ",").compactMap { Double($0.trimmingCharacters(in: .whitespaces)) }
        return p.count == 2 ? CLLocation(latitude: p[0], longitude: p[1]) : nil
    }

    override init() {
        authorization = manager.authorizationStatus
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyHundredMeters
    }

    var isAuthorized: Bool {
        authorization == .authorizedAlways || authorization == .authorized
    }

    func requestPermissionIfNeeded() {
        if authorization == .notDetermined { manager.requestAlwaysAuthorization() }
    }

    func startPassiveMonitoring() {
        guard isAuthorized, CLLocationManager.significantLocationChangeMonitoringAvailable() else { return }
        manager.startMonitoringSignificantLocationChanges()
    }

    func currentLocation(maxAge: TimeInterval = 120) async throws -> CLLocation {
        if let d = debugLocation { return d }
        if let l = lastLocation, -l.timestamp.timeIntervalSinceNow < maxAge { return l }
        if authorization == .denied || authorization == .restricted { throw LocationError.denied }
        requestPermissionIfNeeded()
        return try await withCheckedThrowingContinuation { cont in
            waiters.append(cont)
            if waiters.count == 1 { manager.requestLocation() }
        }
    }

    private func resolve(_ result: Result<CLLocation, Error>) {
        let w = waiters
        waiters.removeAll()
        w.forEach { $0.resume(with: result) }
    }
}

extension LocationService: CLLocationManagerDelegate {
    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let loc = locations.last else { return }
        if UserDefaults.standard.bool(forKey: "debugLogLocation") {
            // Opt-in only: `defaults write io.github.rp20048.Odjezdy debugLogLocation -bool YES`
            ovLog(String(format: "location update (±%d m) %.5f,%.5f", Int(loc.horizontalAccuracy),
                         loc.coordinate.latitude, loc.coordinate.longitude))
        } else {
            ovLog("location update (±\(Int(loc.horizontalAccuracy)) m)")
        }
        Task { @MainActor in
            self.lastLocation = loc
            self.resolve(.success(loc))
            self.onLocation?(self.debugLocation ?? loc)
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        let denied = (error as? CLError)?.code == .denied
        Task { @MainActor in
            self.resolve(.failure(denied ? LocationError.denied : LocationError.unavailable))
        }
    }

    nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        let status = manager.authorizationStatus
        ovLog("location authorization = \(status.rawValue)")
        Task { @MainActor in
            self.authorization = status
            if self.isAuthorized {
                self.startPassiveMonitoring()
                if !self.waiters.isEmpty { manager.requestLocation() }
            } else if status == .denied || status == .restricted {
                self.resolve(.failure(LocationError.denied))
            }
            self.onAuthorizationChange?(status)
        }
    }
}
