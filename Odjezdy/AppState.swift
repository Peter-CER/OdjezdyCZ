import Foundation
import AppKit
import CoreLocation
import Combine

@MainActor
final class AppState: ObservableObject {
    enum Phase: Equatable {
        case idle
        case locating
        case noPermission
        case noLocation
        case outsideArea
        case needsKey          // Prague: Golemio API key missing or rejected
        case noStops
        case ready
    }

    enum LiveState: Equatable {
        case loading
        case ok
        case unavailable
    }

    /// Menu bar visibility (auto-hide outside NL). Stored in UserDefaults and bound via @AppStorage
    /// in the App: binding MenuBarExtra(isInserted:) to a @Published property causes a SwiftUI update loop.
    static let showIconKey = "showMenuBarIcon"
    var showIcon: Bool {
        get { UserDefaults.standard.object(forKey: Self.showIconKey) as? Bool ?? true }
        set { if newValue != showIcon { UserDefaults.standard.set(newValue, forKey: Self.showIconKey) } }
    }

    @Published private(set) var phase: Phase = .idle
    @Published private(set) var groups: [StopGroup] = []
    @Published var selectedID: String?
    /// Departures for all nearby stops, fetched together so switching tabs is instant.
    @Published private(set) var departuresByGroup: [String: [Departure]] = [:]
    /// Service notices (disruptions, stop moved, …) per stop group.
    @Published private(set) var noticesByGroup: [String: [String]] = [:]
    var notices: [String] { selected.flatMap { noticesByGroup[$0.id] } ?? [] }

    let leaveAlert = LeaveAlert()

    /// Starred lines per stop, shown in their own section at the top ("Your lines").
    @Published private(set) var favouriteLines: [String: [String]] =
        UserDefaults.standard.dictionary(forKey: "favouriteLines") as? [String: [String]] ?? [:]

    func isFavourite(_ line: String) -> Bool {
        guard let g = selected else { return false }
        return favouriteLines[g.id]?.contains(line) ?? false
    }

    func toggleFavourite(_ line: String) {
        guard let g = selected else { return }
        var lines = favouriteLines[g.id] ?? []
        if let i = lines.firstIndex(of: line) { lines.remove(at: i) } else { lines.append(line) }
        favouriteLines[g.id] = lines.isEmpty ? nil : lines
        UserDefaults.standard.set(favouriteLines, forKey: "favouriteLines")
    }

    func toggleLeaveAlert(_ d: Departure) {
        guard let g = selected, let r = region, let p = providers[r] else { return }
        leaveAlert.toggle(d, group: g, region: r, provider: p)
    }
    @Published private(set) var live: LiveState = .loading
    @Published private(set) var lastUpdated: Date?
    @Published var startAtLogin = LoginItem.isEnabled {
        didSet {
            guard startAtLogin != LoginItem.isEnabled else { return }
            LoginItem.set(startAtLogin)
        }
    }

    let location = LocationService()
    private var indexes: [Region: StopIndex] = [:]   // each loaded on first use, not at launch
    private lazy var providers: [Region: DepartureProvider] = [.prague: GolemioClient()]
    @Published private(set) var region: Region?

    private var refreshTask: Task<Void, Never>?
    /// After the user re-opens the app, keep the icon visible for the rest of the session.
    private var pinnedVisible = false
    private var isOpen = false
    private var lastCoordinate: CLLocationCoordinate2D?

    var selected: StopGroup? {
        if let s = searchedGroup, s.id == selectedID { return s }
        return groups.first { $0.id == selectedID } ?? groups.first
    }

    // MARK: Stop search (current region only)

    @Published var searchOpen = false
    @Published var searchText = "" { didSet { updateSearch() } }
    @Published private(set) var searchResults: [StopGroup] = []
    /// A stop picked from search; shown instead of the nearby tabs until closed.
    @Published private(set) var searchedGroup: StopGroup?

    private var recentsKey: String { "recentStops_\(region?.rawValue ?? "")" }
    private var recentIDs: [String] { UserDefaults.standard.stringArray(forKey: recentsKey) ?? [] }

    func openSearch() {
        searchText = ""
        searchOpen = true
        updateSearch()
    }

    func closeSearch() { searchOpen = false }

    private func updateSearch() {
        guard let r = region else { searchResults = []; return }
        let idx = index(r)
        if searchText.trimmingCharacters(in: .whitespaces).count < 2 {
            // Recent picks (resolved by name; ids are "town|name").
            searchResults = recentIDs.compactMap { id in
                let name = String(id.split(separator: "|", maxSplits: 1).last ?? "")
                return idx.search(name, from: lastCoordinate, limit: 20).first { $0.id == id }
            }
        } else {
            searchResults = idx.search(searchText, from: lastCoordinate)
        }
    }

    func choose(_ g: StopGroup) {
        searchedGroup = g
        selectedID = g.id
        expandedModes = []
        searchOpen = false
        var recents = recentIDs.filter { $0 != g.id }
        recents.insert(g.id, at: 0)
        UserDefaults.standard.set(Array(recents.prefix(5)), forKey: recentsKey)
        reload()
    }

    /// Nearby stops plus a searched one (if any), all fetched together.
    private var fetchGroups: [StopGroup] {
        groups + (searchedGroup.map { s in groups.contains { $0.id == s.id } ? [] : [s] } ?? [])
    }

    func clearSearchedStop() {
        searchedGroup = nil
        selectedID = groups.first?.id
        expandedModes = []
    }

    /// The selected stop's departures, split by transport type (metro, tram, bus, ferry).
    /// Transport types the user expanded (per stop; reset when switching stops).
    @Published var expandedModes: Set<Int> = []

    func toggleExpanded(_ sectionID: Int) {
        if expandedModes.contains(sectionID) { expandedModes.remove(sectionID) }
        else { expandedModes.insert(sectionID) }
    }

    /// The selected stop's departures, split by transport type (metro, tram, bus, ferry).
    /// Departures you can't reach on foot in time are skipped (at most one, dimmed, is kept),
    /// so a busy stop doesn't fill the list with trams leaving in the next 2 minutes.
    var sections: [ModeSection] {
        guard let g = selected, let deps = departuresByGroup[g.id] else { return [] }
        let now = Date()
        let favs = Set(favouriteLines[g.id] ?? [])
        let favDeps = deps.filter { favs.contains($0.line) }
        let rest = deps.filter { !favs.contains($0.line) }
        let byMode = Dictionary(grouping: rest, by: \.mode)
        let modes = Modes.displayOrder.filter { byMode[$0] != nil }
        let count = modes.count + (favDeps.isEmpty ? 0 : 1)
        // Collapsed: ~10 rows in total (one section 8, two 5 each, three+ 4, min 3). Expanded: 12.
        let perSection = count <= 1 ? 8 : max(3, 10 / count)

        func make(_ list: [Departure], mode: Modes, id: Int, favourites: Bool) -> ModeSection {
            let firstCatchable = list.firstIndex { g.isCatchable($0, now: now) } ?? list.count
            let start = max(0, firstCatchable - 1)
            let expanded = expandedModes.contains(id)
            let end = min(list.count, start + (expanded ? 12 : perSection))
            return ModeSection(mode: mode, departures: Array(list[start..<end]),
                               hasMore: list.count > start + perSection, expanded: expanded,
                               isFavourites: favourites)
        }
        var out: [ModeSection] = []
        if !favDeps.isEmpty { out.append(make(favDeps, mode: g.modes, id: -1, favourites: true)) }
        out += modes.map { make(byMode[$0]!, mode: $0, id: $0.rawValue, favourites: false) }
        return out
    }

    var hasDeparturesForSelected: Bool { selected.map { departuresByGroup[$0.id] != nil } ?? false }

    private var bag: Set<AnyCancellable> = []

    init() {
        leaveAlert.objectWillChange.sink { [weak self] _ in self?.objectWillChange.send() }.store(in: &bag)
        location.onLocation = { [weak self] loc in self?.updateVisibility(for: loc.coordinate) }
        location.onAuthorizationChange = { [weak self] status in
            guard let self else { return }
            if status == .denied || status == .restricted { self.showIcon = true }
            if self.isOpen, self.phase == .noPermission || self.phase == .locating { self.reload() }
        }
        NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didWakeNotification, object: nil, queue: .main
        ) { [weak self] _ in
            Task { @MainActor in self?.checkCountry() }
        }
    }

    // MARK: - Lifecycle

    func launched() {
        LoginItem.enableOnFirstLaunch()
        location.requestPermissionIfNeeded()
        location.startPassiveMonitoring()
        checkCountry()
    }

    /// Re-opening the app (Spotlight/Finder) always brings the icon back — the escape hatch.
    func reopened() {
        guard !showIcon else { return }   // only when hidden; normal popover clicks don't pin
        ovLog("reopened while hidden, showing icon")
        pinnedVisible = true
        showIcon = true
    }

    func checkCountry() {
        Task {
            guard let loc = try? await location.currentLocation(maxAge: 600) else {
                showIcon = true   // unknown → stay visible
                return
            }
            updateVisibility(for: loc.coordinate)
        }
    }

    private func index(_ r: Region) -> StopIndex {
        if let i = indexes[r] { return i }
        let i = StopIndex(resource: r.stopsResource)
        indexes[r] = i
        return i
    }

    /// Inside the PID network if a PID stop is within 1 km. The stop list is only loaded
    /// when you're inside the network's rough box.
    private func region(for c: CLLocationCoordinate2D) -> Region? {
        for r in Region.allCases where r.roughlyContains(c) {
            if index(r).hasStop(near: c, within: 1000) { return r }
        }
        return nil
    }

    private func updateVisibility(for c: CLLocationCoordinate2D) {
        let r = region(for: c)
        ovLog("location check: region = \(r?.rawValue ?? "none")\(pinnedVisible ? " (icon pinned)" : "")")
        showIcon = r != nil || pinnedVisible
    }

    // MARK: - Popover

    func popoverOpened() {
        ovLog("popover opened")
        isOpen = true
        if startAtLogin != LoginItem.isEnabled { startAtLogin = LoginItem.isEnabled }
        reload()
    }

    func popoverClosed() {
        ovLog("popover closed")
        isOpen = false
        refreshTask?.cancel()
        refreshTask = nil
    }

    func reload() {
        refreshTask?.cancel()
        refreshTask = Task { await self.run() }
    }

    func select(_ id: String) {
        guard id != selectedID else { return }
        selectedID = id      // data is already there: no reload, no flicker
        expandedModes = []
        rememberPreference(id)
    }

    private func run() async {
        if groups.isEmpty || phase != .ready { phase = .locating }
        let loc: CLLocation
        do {
            loc = try await location.currentLocation()
        } catch LocationService.LocationError.denied {
            phase = .noPermission
            return
        } catch {
            // No fix right now: keep showing the last stop if we have one.
            guard let c = lastCoordinate, !groups.isEmpty else {
                phase = .noLocation
                return
            }
            loc = CLLocation(latitude: c.latitude, longitude: c.longitude)
        }
        if Task.isCancelled { return }

        guard let r = region(for: loc.coordinate) else {
            phase = .outsideArea
            return
        }
        if r != region {
            region = r
            groups = []
            searchedGroup = nil
        }

        // Re-pick stops only when we've moved (>75 m) or have none.
        if groups.isEmpty || lastCoordinate.map({
            StopIndex.distance($0.latitude, $0.longitude, loc.coordinate.latitude, loc.coordinate.longitude) > 75
        }) ?? true {
            lastCoordinate = loc.coordinate
            let found = index(r).bestStops(near: loc.coordinate, limit: 3, preferred: preference(for: loc.coordinate))
            if found.map(\.id) != groups.map(\.id) {
                groups = found
                selectedID = found.first?.id
                expandedModes = []
                departuresByGroup = [:]
                noticesByGroup = [:]
                live = .loading
            }
        }
        guard !groups.isEmpty else {
            phase = .noStops
            return
        }
        phase = .ready

        // Refresh while the popover is open.
        while !Task.isCancelled {
            await fetchDepartures()
            if phase == .needsKey { return }
            try? await Task.sleep(nanoseconds: r.refreshInterval * 1_000_000_000)
        }
    }

    /// Fetch all nearby stops in parallel (so switching tabs is instant), then keep one copy
    /// of each trip per stop (the same trip can show up at two quays of a merged stop).
    private func fetchDepartures() async {
        let current = fetchGroups
        guard !current.isEmpty, let r = region, let provider = providers[r] else { return }
        var results: [String: [Departure]] = [:]
        var notices: [String: [String]] = [:]
        var missingKey = false
        await withTaskGroup(of: (String, Result<StopBoard, Error>).self) { tg in
            for g in current {
                tg.addTask {
                    do { return (g.id, .success(try await provider.board(for: g))) }
                    catch { return (g.id, .failure(error)) }
                }
            }
            for await (id, result) in tg {
                switch result {
                case .success(let board):
                    var seen = Set<String>()
                    results[id] = board.departures.filter { seen.insert("\($0.line)|\($0.destination)|\($0.planned)").inserted }
                    notices[id] = board.notices
                case .failure(let e):
                    if case DepartureError.missingKey = e { missingKey = true }
                    ovLog("fetch failed for \(id): \(e.localizedDescription)")
                }
            }
        }
        if Task.isCancelled || current.map(\.id) != fetchGroups.map(\.id) { return }
        if missingKey {
            phase = .needsKey
            return
        }
        ovLog("fetched \(results.values.reduce(0) { $0 + $1.count }) departures for \(results.count)/\(current.count) stops (\(r.rawValue))")
        if results.isEmpty {
            live = .unavailable
            return
        }
        departuresByGroup.merge(results) { _, new in new }
        noticesByGroup.merge(notices) { _, new in new }
        live = .ok
        lastUpdated = Date()
    }

    /// Inline key editor in the popover (no @State: keeps the CLT build working).
    @Published var editingKey = false
    @Published var keyDraft = ""

    func beginKeyEntry() {
        keyDraft = GolemioClient.apiKey ?? ""
        editingKey = true
    }

    func setGolemioKey(_ key: String) {
        editingKey = false
        UserDefaults.standard.set(key.trimmingCharacters(in: .whitespacesAndNewlines), forKey: GolemioClient.keyDefaultsKey)
        if phase == .needsKey { phase = .ready }
        reload()
    }

    // MARK: - Remembered stop choice per spot (~300 m grid cell)

    private func cellKey(_ c: CLLocationCoordinate2D) -> String {
        "pref:\(Int((c.latitude / 0.0027).rounded())):\(Int((c.longitude / 0.0045).rounded()))"
    }

    private func preference(for c: CLLocationCoordinate2D) -> String? {
        UserDefaults.standard.string(forKey: cellKey(c))
    }

    private func rememberPreference(_ id: String) {
        guard let c = lastCoordinate else { return }
        if id == groups.first?.id && preference(for: c) == nil { return }
        UserDefaults.standard.set(id, forKey: cellKey(c))
    }
}
