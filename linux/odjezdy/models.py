"""Transport modes, stops, departures, regions and the country-border check.

Mirrors Models.swift, Region.swift and CountryBorder.swift. No GTK imports here, so this
module (and stops.py / providers.py) can be tested headless.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime

# --- Transport modes: bitmask as in stops_prague.json (1=tram 2=metro 4=bus 8=ferry) ---

TRAM, METRO, BUS, FERRY, TRAIN, TROLLEYBUS = 1, 2, 4, 8, 16, 32
DISPLAY_ORDER = [METRO, TRAIN, TRAM, TROLLEYBUS, BUS, FERRY]

_TITLES = {METRO: "Metro", TRAM: "Tram", FERRY: "Ferry", TRAIN: "Train",
           TROLLEYBUS: "Trolleybus", BUS: "Bus"}
# Emoji stand in for the SF Symbols the Mac app uses.
_SYMBOLS = {METRO: "🚇", TRAIN: "🚆", TRAM: "🚋", FERRY: "⛴", TROLLEYBUS: "🚎", BUS: "🚌"}
# Fallback badge colours when the operator doesn't publish one.
_FALLBACK = {METRO: "#005CAB", TRAM: "#008C73", FERRY: "#1F73BF", TRAIN: "#264D99",
             TROLLEYBUS: "#8C4073", BUS: "#52575F"}


def mode_from_gtfs(t: int) -> int:
    if t in (0, 7, 900):
        return TRAM
    if t in (1, 401, 402):
        return METRO
    if t == 2 or 100 <= t <= 199:
        return TRAIN
    if t in (4, 1000, 1200):
        return FERRY
    if t in (11, 800):
        return TROLLEYBUS
    return BUS


def _primary(modes: int) -> int:
    """The mode that names a mask, in the same priority order as Models.swift."""
    for m in (METRO, TRAIN, TRAM, FERRY, TROLLEYBUS):
        if modes & m:
            return m
    return BUS


def mode_title(modes: int) -> str:
    # Swift's title checks tram before ferry/train, unlike the symbol: keep that order.
    for m in (METRO, TRAM, FERRY, TRAIN, TROLLEYBUS):
        if modes & m:
            return _TITLES[m]
    return "Bus"


def mode_symbol(modes: int) -> str:
    return _SYMBOLS[_primary(modes)]


def mode_fallback_color(modes: int) -> str:
    for m in (METRO, TRAM, FERRY, TRAIN, TROLLEYBUS):
        if modes & m:
            return _FALLBACK[m]
    return _FALLBACK[BUS]


def modes_list(modes: int) -> list[int]:
    return [m for m in DISPLAY_ORDER if modes & m]


# --- Colours ---

def _parse_hex(h: str | None):
    s = (h or "").strip("# ")
    if len(s) != 6:
        return None
    try:
        v = int(s, 16)
    except ValueError:
        return None
    return ((v >> 16) & 0xFF) / 255, ((v >> 8) & 0xFF) / 255, (v & 0xFF) / 255


def badge_colors(hex_color: str | None, mode: int) -> tuple[str, str]:
    """Badge background + readable text colour (e.g. yellow metro gets dark text)."""
    rgb = _parse_hex(hex_color)
    # Some feeds publish white as the line colour; unreadable as a badge.
    if rgb is None or all(c > 0.92 for c in rgb):
        return mode_fallback_color(mode), "#FFFFFF"
    r, g, b = rgb
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    bg = "#%02X%02X%02X" % (round(r * 255), round(g * 255), round(b * 255))
    return bg, ("rgba(0,0,0,0.85)" if lum > 0.6 else "#FFFFFF")


# --- Geometry ---

def distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Fast equirectangular distance in metres; plenty accurate at walking scale."""
    k = math.pi / 180
    x = (lon2 - lon1) * k * math.cos((lat1 + lat2) / 2 * k)
    y = (lat2 - lat1) * k
    return math.sqrt(x * x + y * y) * 6_371_000


# --- Stops and departures ---

@dataclass(frozen=True)
class LineInfo:
    number: str
    mode: int
    color_hex: str


@dataclass
class Stop:
    """One stop area (both directions of a stop, sometimes several quays)."""
    name: str
    town: str
    lat: float
    lon: float
    tpcs: list[str]
    modes: int
    weekday_departures: int
    lines: list[LineInfo]
    platforms: list[tuple[float, float]] = field(default_factory=list)

    def distance_from(self, lat: float, lon: float) -> float:
        """Walking-relevant distance: to the nearest platform, not the stop's centre."""
        if not self.platforms:
            return distance(lat, lon, self.lat, self.lon)
        return min(distance(lat, lon, a, b) for a, b in self.platforms)


@dataclass
class Departure:
    id: str
    tpc: str                  # timing point (quay) it departs from
    line: str
    mode: int
    destination: str
    planned: float            # unix time
    expected: float
    status: str               # planned / driving / arrived / cancelled
    is_live: bool
    precise: bool = True      # known to the second (Golemio); MOTIS is per minute
    color_hex: str | None = None

    @property
    def delay_minutes(self) -> int:
        return round((self.expected - self.planned) / 60)

    @property
    def is_cancelled(self) -> bool:
        return self.status == "cancelled"


@dataclass
class StopGroup:
    """What the user sees as one tab: same-named stop areas close together, merged."""
    name: str
    town: str
    distance: float           # metres, to the nearest member
    lat: float
    lon: float
    tpcs: list[str]
    modes: int
    weekday_departures: int
    lines: list[LineInfo]
    score: float = 0.0

    @property
    def id(self) -> str:
        return f"{self.town}|{self.name}"

    @property
    def walk_seconds(self) -> float:
        """Straight line × 1.25 for real streets, at 80 m/min (4.8 km/h)."""
        return round(self.distance * 1.25 / 80 * 60)

    @property
    def walk_minutes(self) -> int:
        return max(1, math.ceil(self.walk_seconds / 60))

    @property
    def is_nearby(self) -> bool:
        """Close enough that walking time means something (searched stops can be far)."""
        return self.distance <= 2000

    def is_catchable(self, d: Departure, now: float) -> bool:
        """A departure you can still catch if you leave now (allowing a bit of hurrying)."""
        return not self.is_nearby or d.expected - now >= self.walk_seconds * 0.75

    def badge_colors(self, d: Departure) -> tuple[str, str]:
        known = next((l.color_hex for l in self.lines if l.number == d.line and l.mode == d.mode), None) \
            or next((l.color_hex for l in self.lines if l.number == d.line), None)
        return badge_colors(known or d.color_hex, d.mode)


@dataclass
class StopBoard:
    """What a stop's sign shows: departures plus any service notices."""
    departures: list[Departure]
    notices: list[str] = field(default_factory=list)
    source: str = "Golemio"


@dataclass
class ModeSection:
    mode: int
    departures: list[Departure]
    has_more: bool = False
    expanded: bool = False
    is_favourites: bool = False

    @property
    def id(self) -> int:
        return -1 if self.is_favourites else self.mode


class DepartureError(Exception):
    pass


class Unavailable(DepartureError):
    def __str__(self):
        return "Live data unavailable"


class MissingKey(DepartureError):
    def __str__(self):
        return "API key needed"


def parse_time(s) -> float | None:
    """ISO 8601 → unix time ('Z', offsets and fractional seconds all accepted)."""
    if not isinstance(s, str) or not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# --- Regions ---

PRAGUE, CZECHIA = "prague", "czechia"
REGION_NAMES = {PRAGUE: "Prague (PID)", CZECHIA: "Czechia"}
# Coarse boxes; the precise checks are "PID stop within 1 km" / the Czech border.
_BOUNDS = {PRAGUE: ((49.4, 50.8), (13.3, 15.6)), CZECHIA: ((48.5, 51.1), (12.0, 18.9))}


def region_roughly_contains(region: str, lat: float, lon: float) -> bool:
    (la0, la1), (lo0, lo1) = _BOUNDS[region]
    return la0 <= lat <= la1 and lo0 <= lon <= lo1


# --- Country border ---

class CountryBorder:
    """Offline "is this point inside Czechia?" against a simplified bundled border."""

    def __init__(self, path: str):
        self.rings: list[list[tuple[float, float]]] = []
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)["rings"]
            self.rings = [[(p[0], p[1]) for p in ring if len(p) == 2] for ring in raw]
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def contains(self, lat: float, lon: float) -> bool:
        """True when the point, or any point ~2.5 km around it, is inside (the buffer absorbs
        the simplified border's error so river-border towns count)."""
        if not self.rings:
            return True  # no data → never hide
        d_lat, d_lon = 0.0225, 0.035
        probes = [(0, 0), (d_lat, 0), (-d_lat, 0), (0, d_lon), (0, -d_lon),
                  (d_lat * .7, d_lon * .7), (d_lat * .7, -d_lon * .7),
                  (-d_lat * .7, d_lon * .7), (-d_lat * .7, -d_lon * .7)]
        return any(self._inside(lat + a, lon + b) for a, b in probes)

    def _inside(self, lat: float, lon: float) -> bool:
        for ring in self.rings:
            hit = False
            j = len(ring) - 1
            for i in range(len(ring)):
                yi, xi = ring[i]
                yj, xj = ring[j]
                if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
                    hit = not hit
                j = i
            if hit:
                return True
        return False


# --- Bundled data files ---

def data_dir() -> str:
    """Where stops_prague.json / cz_border.json live: $ODJEZDY_DATA, the repo's shared
    Odjezdy/Resources (when run from a checkout), or the installed /usr/share/odjezdy."""
    env = os.environ.get("ODJEZDY_DATA")
    if env:
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.normpath(os.path.join(here, "..", "..", "Odjezdy", "Resources"))
    if os.path.exists(os.path.join(repo, "stops_prague.json")):
        return repo
    return "/usr/share/odjezdy"
