"""Location → stops → fetch loop, sections, tabs, search, favourites. Mirrors AppState.swift.

State lives on the GTK main thread; network calls run on worker threads and post results
back with GLib.idle_add. Every reload / search bumps a generation number so that answers to
superseded requests are dropped.
"""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from gi.repository import GLib

from . import providers
from .models import (CZECHIA, DISPLAY_ORDER, PRAGUE, CountryBorder, Departure, DepartureError,
                     MissingKey, ModeSection, StopGroup, data_dir, distance,
                     region_roughly_contains)
from .stops import StopIndex

SEARCH_DEBOUNCE_MS = 300


def run_in_thread(work, done) -> None:
    """work() on a thread, then done(result, error) on the main loop."""
    def body():
        try:
            result, error = work(), None
        except Exception as e:  # report to the UI instead of dying silently
            result, error = None, e
        GLib.idle_add(lambda: done(result, error) and False)
    threading.Thread(target=body, daemon=True).start()


def build_sections(group: StopGroup, departures: list[Departure], favourites: set[str],
                   expanded: set[int], now: float) -> list[ModeSection]:
    """Split by transport type. Departures you can't reach on foot in time are skipped (at
    most one, dimmed, is kept)."""
    fav = [d for d in departures if d.line in favourites]
    by_mode: dict[int, list[Departure]] = {}
    for d in departures:
        if d.line not in favourites:
            by_mode.setdefault(d.mode, []).append(d)
    modes = [m for m in DISPLAY_ORDER if m in by_mode]
    count = len(modes) + (1 if fav else 0)
    # Collapsed: ~10 rows in total (one section 8, two 5 each, three+ 4, min 3). Expanded: 12.
    per_section = 8 if count <= 1 else max(3, 10 // count)

    def make(items, mode, favourites_section):
        section_id = -1 if favourites_section else mode
        first = next((i for i, d in enumerate(items) if group.is_catchable(d, now)), len(items))
        start = max(0, first - 1)
        is_open = section_id in expanded
        end = min(len(items), start + (12 if is_open else per_section))
        return ModeSection(mode, items[start:end], len(items) > start + per_section, is_open,
                           favourites_section)

    out = [make(fav, group.modes, True)] if fav else []
    return out + [make(by_mode[m], m, False) for m in modes]


class AppState:
    def __init__(self, config, location, log):
        self.config = config
        self.location = location
        self.log = log
        self.on_change = lambda: None        # set by the UI

        # idle / locating / no_location / outside_area / no_stops / ready
        self.phase = "idle"
        self.location_reason: str | None = None
        self.location_source = ""
        self.groups: list[StopGroup] = []
        self.selected_id: str | None = None
        self.searched_group: StopGroup | None = None
        self.departures: dict[str, list[Departure]] = {}
        self.notices_by_group: dict[str, list[str]] = {}
        self.live = "loading"                # loading / ok / unavailable
        self.last_updated: float | None = None
        self.data_source = ""
        self.region: str | None = None
        self.expanded: set[int] = set()

        self.search_open = False
        self.search_text = ""
        self.search_results: list[StopGroup] = []

        self.is_open = False
        self._coordinate: tuple[float, float] | None = None
        self._generation = 0
        self._search_generation = 0
        self._search_timer = None
        self._refresh_timer = None
        self._indexes: dict[str, StopIndex] = {}
        self._index_lock = threading.Lock()
        self._border: CountryBorder | None = None
        self.providers = {PRAGUE: providers.PragueProvider(config, log),
                          CZECHIA: providers.MotisProvider()}

    # --- Derived ---

    @property
    def selected(self) -> StopGroup | None:
        if self.searched_group and self.searched_group.id == self.selected_id:
            return self.searched_group
        return next((g for g in self.groups if g.id == self.selected_id),
                    self.groups[0] if self.groups else None)

    @property
    def notices(self) -> list[str]:
        g = self.selected
        return self.notices_by_group.get(g.id, []) if g else []

    def sections(self, now: float | None = None) -> list[ModeSection]:
        g = self.selected
        if not g or g.id not in self.departures:
            return []
        now = time.time() if now is None else now
        favourites = set((self.config.get("favouriteLines") or {}).get(g.id, []))
        upcoming = [d for d in self.departures[g.id] if d.expected > now - 30]
        return build_sections(g, upcoming, favourites, self.expanded, now)

    @property
    def refresh_seconds(self) -> int:
        # MOTIS is one volunteer's server and updates per minute: poll it more gently.
        return 15 if self.region == PRAGUE and self.config.golemio_key else 30

    def index(self, region: str) -> StopIndex:
        with self._index_lock:
            if region not in self._indexes:
                self._indexes[region] = StopIndex.load(
                    os.path.join(data_dir(), f"stops_{region}.json"))
            return self._indexes[region]

    def region_for(self, lat: float, lon: float) -> str | None:
        """Inside the PID network if a PID stop is within 1 km; else inside Czechia."""
        if region_roughly_contains(PRAGUE, lat, lon) and \
                self.index(PRAGUE).has_stop_near(lat, lon, 1000):
            return PRAGUE
        if self._border is None:
            self._border = CountryBorder(os.path.join(data_dir(), "cz_border.json"))
        if region_roughly_contains(CZECHIA, lat, lon) and self._border.contains(lat, lon):
            return CZECHIA
        return None

    # --- Window ---

    def opened(self) -> None:
        self.is_open = True
        self.reload()

    def closed(self) -> None:
        self.is_open = False
        self._generation += 1
        self._stop_timer()

    def _stop_timer(self) -> None:
        if self._refresh_timer:
            GLib.source_remove(self._refresh_timer)
            self._refresh_timer = None

    # --- Location → stops → departures ---

    def reload(self) -> None:
        self._generation += 1
        generation = self._generation
        self._stop_timer()
        if not self.groups and not self.searched_group:
            self.phase = "locating"
        self.on_change()

        def located(fix, reason):
            if generation != self._generation:
                return
            self._located(generation, fix, reason)

        self.location.current(located)

    def _located(self, generation, fix, reason) -> None:
        self.location_reason = reason
        if fix:
            self.location_source = fix.source
            coordinate = (fix.lat, fix.lon)
        elif self._coordinate and self.groups:
            coordinate = self._coordinate     # no fix right now: keep the last stops
        else:
            self.location_source = ""
            if self.searched_group:           # a searched stop works without any location
                self.phase = "ready"
                self._fetch(generation)
            else:
                self.phase = "no_location"
                self.on_change()
            return

        moved = (not self.groups or self._coordinate is None
                 or distance(*self._coordinate, *coordinate) > 75)
        preferred = self.config.get(self._cell_key(coordinate))
        previous_region = self.region

        def find():
            region = self.region_for(*coordinate)
            if region is None:
                return None, []
            if not moved and region == previous_region:
                return region, None
            if region == CZECHIA:
                return region, providers.motis_nearby(*coordinate, preferred=preferred)
            return region, self.index(region).best_stops(*coordinate, preferred=preferred)

        def found(result, error):
            if generation != self._generation:
                return
            if error:
                self.log(f"finding stops failed: {error}")
                self.live = "unavailable"
                self.phase = "ready" if self.selected else "no_stops"
                self.on_change()
                return
            region, groups = result
            if region is None:
                self.phase = "ready" if self.searched_group else "outside_area"
                if self.searched_group:
                    self._fetch(generation)
                self.on_change()
                return
            if region != self.region:
                self.region = region
                self.groups = []
                self.searched_group = None
            if groups is not None:
                self._coordinate = coordinate
                if [g.id for g in groups] != [g.id for g in self.groups]:
                    self.groups = groups
                    if not (self.searched_group and self.selected_id == self.searched_group.id):
                        self.selected_id = groups[0].id if groups else None
                        self.expanded = set()
                    self.departures = {}
                    self.notices_by_group = {}
                    self.live = "loading"
                else:
                    self.groups = groups      # same stops, fresh distances
            if not self.selected:
                self.phase = "no_stops"
                self.on_change()
                return
            self.phase = "ready"
            self.on_change()
            self._fetch(generation)

        run_in_thread(find, found)

    def _fetch_groups(self) -> list[StopGroup]:
        s = self.searched_group
        extra = [s] if s and all(g.id != s.id for g in self.groups) else []
        return self.groups + extra

    def _fetch(self, generation) -> None:
        """All nearby stops in parallel (so switching tabs is instant)."""
        current = self._fetch_groups()
        if not current or not self.region:
            return
        provider = self.providers[self.region]
        region = self.region

        def one(g):
            try:
                return g.id, provider.board(g), None
            except DepartureError as e:
                return g.id, None, e
            except Exception as e:
                return g.id, None, e

        def work():
            with ThreadPoolExecutor(max_workers=4) as pool:
                return list(pool.map(one, current))

        def done(results, error):
            if generation != self._generation:
                return
            boards = {}
            for group_id, board, failure in results or []:
                if board:
                    boards[group_id] = board
                else:
                    self.log(f"fetch failed for {group_id}: {failure}")
                    if isinstance(failure, MissingKey):
                        self.log("Golemio key rejected")
            self.log(f"fetched {sum(len(b.departures) for b in boards.values())} departures"
                     f" for {len(boards)}/{len(current)} stops ({region})")
            if not boards:
                self.live = "unavailable"
            else:
                for group_id, board in boards.items():
                    # One copy of each trip (it can show up at two quays of a merged stop).
                    seen, unique = set(), []
                    for d in board.departures:
                        key = (d.line, d.destination, d.planned)
                        if key not in seen:
                            seen.add(key)
                            unique.append(d)
                    self.departures[group_id] = unique
                    self.notices_by_group[group_id] = board.notices
                self.live = "ok"
                self.last_updated = time.time()
                selected = self.selected
                shown = boards.get(selected.id if selected else "") or next(iter(boards.values()))
                self.data_source = shown.source
            self.on_change()
            if self.is_open:
                self._stop_timer()
                self._refresh_timer = GLib.timeout_add_seconds(
                    self.refresh_seconds, self._refresh, generation)

        run_in_thread(work, done)

    def _refresh(self, generation):
        self._refresh_timer = None
        if generation == self._generation and self.is_open:
            self._fetch(generation)
        return False

    # --- Tabs, sections, favourites ---

    def select(self, group_id: str) -> None:
        if group_id == self.selected_id:
            return
        self.selected_id = group_id
        self.expanded = set()
        self._remember_preference(group_id)
        self.on_change()

    def toggle_expanded(self, section_id: int) -> None:
        self.expanded ^= {section_id}
        self.on_change()

    def is_favourite(self, line: str) -> bool:
        g = self.selected
        return bool(g) and line in (self.config.get("favouriteLines") or {}).get(g.id, [])

    def toggle_favourite(self, line: str) -> None:
        g = self.selected
        if not g:
            return
        favourites = dict(self.config.get("favouriteLines") or {})
        lines = list(favourites.get(g.id, []))
        if line in lines:
            lines.remove(line)
        else:
            lines.append(line)
        if lines:
            favourites[g.id] = lines
        else:
            favourites.pop(g.id, None)
        self.config.set("favouriteLines", favourites)
        self.on_change()

    # --- Search ---

    @property
    def search_region(self) -> str:
        """Without a location the whole country is searched online (MOTIS)."""
        return self.region or CZECHIA

    def recents(self) -> list[StopGroup]:
        out = []
        for d in self.config.get(f"recentStopGroups_{self.search_region}") or []:
            try:
                dist = (distance(*self._coordinate, d["lat"], d["lon"])
                        if self._coordinate else 0)
                out.append(StopGroup(d["name"], d["town"], dist, d["lat"], d["lon"],
                                     list(d["tpcs"]), d.get("modes", 0), 0, []))
            except (KeyError, TypeError):
                continue
        return out

    def _remember(self, g: StopGroup) -> None:
        key = f"recentStopGroups_{self.search_region}"
        items = [d for d in self.config.get(key) or []
                 if f"{d.get('town', '')}|{d.get('name', '')}" != g.id]
        items.insert(0, {"name": g.name, "town": g.town, "lat": g.lat, "lon": g.lon,
                         "tpcs": g.tpcs, "modes": g.modes})
        self.config.set(key, items[:5])

    def open_search(self) -> None:
        self.search_open = True
        self.set_search_text("")

    def close_search(self) -> None:
        self.search_open = False
        self.on_change()

    def set_search_text(self, text: str) -> None:
        self.search_text = text
        self._search_generation += 1
        generation = self._search_generation
        if self._search_timer:
            GLib.source_remove(self._search_timer)
            self._search_timer = None
        query = text.strip()
        if len(query) < 2:
            self.search_results = self.recents()
            self.on_change()
            return
        region, loc = self.search_region, self._coordinate

        def start():
            self._search_timer = None

            def work():
                if region == PRAGUE:
                    return self.index(PRAGUE).search(query, loc)
                return providers.motis_search(query, loc)

            def done(results, error):
                if generation == self._search_generation:
                    self.search_results = results or []
                    self.on_change()

            run_in_thread(work, done)
            return False

        # Wait for a pause in typing before asking.
        self._search_timer = GLib.timeout_add(SEARCH_DEBOUNCE_MS, start)

    def choose(self, g: StopGroup) -> None:
        self._remember(g)
        if self.region is None:
            self.region = self.region_for(g.lat, g.lon) or CZECHIA
            if self.region == PRAGUE:
                # Found online (MOTIS ids), so fetch it through MOTIS, not Golemio.
                self.region = CZECHIA
        self.searched_group = g
        self.selected_id = g.id
        self.expanded = set()
        self.search_open = False
        self.phase = "ready"
        self.live = "loading"
        self.reload()

    def clear_searched(self) -> None:
        self.searched_group = None
        self.selected_id = self.groups[0].id if self.groups else None
        self.expanded = set()
        if not self.groups:
            self.region = None
            self.reload()
        else:
            self.on_change()

    # --- Settings ---

    def set_golemio_key(self, key: str) -> None:
        self.config.set("golemioKey", key.strip() or None)
        self.reload()

    def set_home(self, group: StopGroup | None) -> None:
        """Use a stop's position when there's no good location fix."""
        self.config.set("homeLocation", f"{group.lat},{group.lon}" if group else None)
        self.groups = []
        self.searched_group = None
        self._coordinate = None
        self.region = None
        self.reload()

    # --- Remembered stop choice per spot (~300 m grid cell) ---

    @staticmethod
    def _cell_key(c: tuple[float, float]) -> str:
        return f"pref:{round(c[0] / 0.0027)}:{round(c[1] / 0.0045)}"

    def _remember_preference(self, group_id: str) -> None:
        if not self._coordinate:
            return
        key = self._cell_key(self._coordinate)
        if self.groups and group_id == self.groups[0].id and self.config.get(key) is None:
            return
        self.config.set(key, group_id)
