import Foundation
import UserNotifications

/// "Leave now" alert for one departure: a notification when it's time to walk to the stop.
/// While an alert is pending, the departure is re-checked once a minute (even with the popover
/// closed) so the alert follows live delays. Nothing runs when no alert is set.
@MainActor
final class LeaveAlert: ObservableObject {
    struct Pending: Equatable {
        let departureID: String
        let group: StopGroup
        let region: Region
        let line: String
        let destination: String
        var expected: Date
        var bufferMinutes: Int
        /// Alert when there are `bufferMinutes` left beyond the walk: "leave within N min".
        var fireAt: Date { expected.addingTimeInterval(-(group.walkSeconds + Double(bufferMinutes) * 60)) }
    }

    /// Minutes of slack before you must leave (Settings → Alert Me; default 5).
    static let bufferKey = "alertBufferMinutes"
    static let bufferChoices = [1, 2, 3, 5, 10, 15]
    static var bufferMinutes: Int {
        let v = UserDefaults.standard.integer(forKey: bufferKey)
        return v > 0 ? v : 5
    }

    @Published private(set) var pending: Pending?
    private var watchTask: Task<Void, Never>?
    private let notificationID = "leave-now"

    func isSet(_ d: Departure) -> Bool { pending?.departureID == d.id }

    func toggle(_ d: Departure, group: StopGroup, region: Region, provider: DepartureProvider) {
        if isSet(d) { cancel(); return }
        Task {
            let center = UNUserNotificationCenter.current()
            let granted = (try? await center.requestAuthorization(options: [.alert, .sound])) ?? false
            guard granted else {
                ovLog("leave alert: notifications not allowed")
                return
            }
            pending = Pending(departureID: d.id, group: group, region: region,
                              line: d.line, destination: d.destination, expected: d.expected,
                              bufferMinutes: Self.bufferMinutes)
            schedule()
            watch(provider: provider)
        }
    }

    func cancel() {
        watchTask?.cancel()
        watchTask = nil
        pending = nil
        UNUserNotificationCenter.current().removePendingNotificationRequests(withIdentifiers: [notificationID])
    }

    private func schedule() {
        guard let p = pending else { return }
        let center = UNUserNotificationCenter.current()
        center.removePendingNotificationRequests(withIdentifiers: [notificationID])
        let f = DateFormatter()
        f.dateFormat = "HH:mm"
        let content = UNMutableNotificationContent()
        // Slack = time left before you must start walking. Normally the alert fires with exactly
        // `bufferMinutes` of slack; if you set it later than that, it fires now with what's left.
        let slackAtFire = min(Double(p.bufferMinutes) * 60,
                              p.expected.timeIntervalSinceNow - p.group.walkSeconds)
        let slackMin = Int(slackAtFire / 60)
        content.title = slackMin >= 1 ? "Leave within \(slackMin) min for \(p.line) → \(p.destination)"
                                      : "Leave now for \(p.line) → \(p.destination)"
        content.body = "Departs \(f.string(from: p.expected)) from \(p.group.name) · \(p.group.walkMinutes) min walk"
            + (slackMin >= 1 ? " — that gets you there on time" : (slackAtFire < 0 ? " — you'll need to hurry" : ""))
        content.sound = .default
        // Time Sensitive would break through Focus, but needs a paid developer account (entitlement);
        // with a free team it is ignored and Focus still mutes the banner.
        content.interruptionLevel = .timeSensitive
        let delay = max(1, p.fireAt.timeIntervalSinceNow)
        let trigger = UNTimeIntervalNotificationTrigger(timeInterval: delay, repeats: false)
        center.add(UNNotificationRequest(identifier: notificationID, content: content, trigger: trigger))
        ovLog("leave alert: \(p.line) → \(p.destination), notify in \(Int(delay)) s")
    }

    /// Re-check the departure every 60 s until the alert fires; reschedule if its time moved.
    private func watch(provider: DepartureProvider) {
        watchTask?.cancel()
        watchTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self, let p = self.pending else { return }
                let wait = min(60, p.fireAt.timeIntervalSinceNow)
                if wait <= 0 {
                    // Fired (or about to): we're done.
                    try? await Task.sleep(nanoseconds: 5_000_000_000)
                    if self.pending == p { self.pending = nil }
                    return
                }
                try? await Task.sleep(nanoseconds: UInt64(wait * 1_000_000_000))
                if Task.isCancelled { return }
                guard let current = self.pending, current.fireAt.timeIntervalSinceNow > 30 else { continue }
                if let board = try? await provider.board(for: current.group),
                   let d = board.departures.first(where: { $0.id == current.departureID }),
                   abs(d.expected.timeIntervalSince(current.expected)) >= 20 {
                    self.pending?.expected = d.expected
                    self.schedule()
                }
            }
        }
    }
}
