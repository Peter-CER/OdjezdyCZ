"""Live departures from Golemio (Prague, with key) and MOTIS (all of Czechia, no key).
Mirrors DepartureProvider.swift, GolemioClient.swift and MotisClient.swift.

All calls block, so the app runs them on worker threads. Standard library only.
MOTIS (motis.obehy.cz) is a volunteer's server whose operator asks that apps identify
themselves in the User-Agent.
"""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from . import REPO_URL, __version__
from .models import (BUS, FERRY, METRO, TRAIN, TRAM, TROLLEYBUS, Departure, MissingKey, Stop,
                     StopBoard, StopGroup, Unavailable, distance, mode_from_gtfs, parse_time)
from .stops import group, normalize

MOTIS_BASE = "https://motis.obehy.cz/api/v1"
GOLEMIO_URL = "https://api.golemio.cz/v2/pid/departureboards"
SIGN_UP_URL = "https://api.golemio.cz/api-keys/auth/sign-up"
USER_AGENT = f"Odjezdy-Linux/{__version__} (+{REPO_URL})"


def _get_json(url: str, params: list[tuple[str, str]], headers: dict | None = None, timeout=10):
    req = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode(params)}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


# --- Golemio ---

def parse_golemio(data: dict, now: float | None = None) -> StopBoard:
    now = time.time() if now is None else now
    notices: list[str] = []
    for i in data.get("infotexts") or []:
        start, end = parse_time(i.get("valid_from")), parse_time(i.get("valid_to"))
        if (start and start > now) or (end and end < now):
            continue
        text = (i.get("text_en") or i.get("text") or "").strip()
        if text and text not in notices:
            notices.append(text)
    out = []
    for d in data.get("departures") or []:
        route, trip = d.get("route") or {}, d.get("trip") or {}
        stamp = d.get("departure_timestamp") or {}
        line, planned = route.get("short_name"), parse_time(stamp.get("scheduled"))
        if line is None or planned is None:
            continue
        live = bool((d.get("delay") or {}).get("is_available"))
        cancelled = bool(trip.get("is_canceled"))
        stop_id = (d.get("stop") or {}).get("id") or ""
        status = ("cancelled" if cancelled else "arrived" if trip.get("is_at_stop")
                  else "driving" if live else "planned")
        route_type = route.get("type")
        out.append(Departure(
            id=f"{trip.get('id') or uuid.uuid4()}@{stop_id}", tpc=stop_id, line=line,
            mode=mode_from_gtfs(3 if route_type is None else route_type),
            destination=trip.get("headsign") or "", planned=planned,
            expected=parse_time(stamp.get("predicted")) or planned,
            status=status, is_live=live and not cancelled))
    out.sort(key=lambda d: d.expected)
    return StopBoard(out, notices, "Golemio")


def golemio_board(group_: StopGroup, key: str) -> StopBoard:
    params = [("ids[]", t) for t in group_.tpcs] + [
        ("minutesAfter", "90"), ("limit", "150"), ("order", "real"),
        ("mode", "departures"), ("airCondition", "false")]
    try:
        return parse_golemio(_get_json(GOLEMIO_URL, params, {"X-Access-Token": key}, timeout=8))
    except urllib.error.HTTPError as e:
        raise (MissingKey() if e.code in (401, 403) else Unavailable()) from e
    except (OSError, ValueError) as e:
        raise Unavailable() from e


# --- MOTIS ---

def motis_mode(m: str | None) -> int:
    return {"SUBWAY": METRO, "METRO": METRO, "TRAM": TRAM, "FUNICULAR": TRAM, "CABLE_CAR": TRAM,
            "AERIAL_LIFT": TRAM, "FERRY": FERRY, "TROLLEYBUS": TROLLEYBUS, "BUS": BUS,
            "COACH": BUS}.get(m or "", TRAIN)


def short_line(s: str) -> str:
    """"S3 (Os 4658)" → "S3"."""
    return s.split(" (")[0]


def split_name(full: str) -> tuple[str, str]:
    """"Brno,Náměstí Svobody" → ("Brno", "Náměstí Svobody")."""
    town, sep, name = full.partition(",")
    return (town, name) if sep else ("", full)


def motis_get(path: str, params: dict):
    try:
        return _get_json(MOTIS_BASE + path, list(params.items()))
    except (OSError, ValueError) as e:
        raise Unavailable() from e


def parse_stoptimes(data: dict, stop_id: str, now: float | None = None):
    """Departures plus stop alerts (`place.alerts`: headerText / descriptionText)."""
    now = time.time() if now is None else now
    entries = data.get("stopTimes") or []
    alerts: list[str] = []
    out: list[Departure] = []
    for x in entries:
        place = x.get("place") or {}
        for a in place.get("alerts") or []:
            header = (a.get("headerText") or "").strip()
            desc = (a.get("descriptionText") or "").strip()
            text = (desc if not header else header if not desc or desc == header
                    else f"{header}: {desc}")
            if text and text not in alerts:
                alerts.append(text)
        planned = parse_time(place.get("scheduledDeparture"))
        if planned is None:
            continue
        expected = parse_time(place.get("departure")) or planned
        if expected < now - 30:
            continue
        raw = x.get("routeShortName") or x.get("displayName") or ""
        if not raw:
            continue
        live = bool(x.get("realTime"))
        cancelled = bool(x.get("cancelled") or place.get("cancelled"))
        out.append(Departure(
            id=f"{x.get('tripId') or uuid.uuid4()}@{place.get('stopId') or stop_id}",
            tpc=stop_id, line=short_line(raw), mode=motis_mode(x.get("mode")),
            destination=x.get("headsign") or "", planned=planned, expected=expected,
            status="cancelled" if cancelled else "driving" if live else "planned",
            is_live=live and not cancelled, precise=False, color_hex=x.get("routeColor")))
    return out, alerts


def motis_board(stop_ids: list[str], town: str = "") -> StopBoard:
    """`town`: destinations in the same town lose their "Town," prefix."""
    def one(stop_id):
        try:
            return parse_stoptimes(motis_get("/stoptimes", {"stopId": stop_id, "n": "40"}), stop_id)
        except Unavailable:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(one, stop_ids))
    if stop_ids and all(r is None for r in results):
        raise Unavailable()
    departures: list[Departure] = []
    notices: list[str] = []
    for r in results:
        if r:
            departures += r[0]
            notices += [a for a in r[1] if a not in notices]
    departures.sort(key=lambda d: d.expected)
    if town:
        prefix = town + ","
        departures = [replace(d, destination=d.destination[len(prefix):])
                      if d.destination.startswith(prefix) else d for d in departures]
    return StopBoard(departures, notices, "MOTIS")


def parse_map_stops(items: list) -> list[Stop]:
    """MOTIS platforms grouped by parent station."""
    by_parent: dict[str, list[dict]] = {}
    for p in items:
        key = p.get("parentId") or p.get("stopId")
        if key:
            by_parent.setdefault(key, []).append(p)
    out = []
    for parent, platforms in by_parent.items():
        full = platforms[0].get("name")
        coords = [(p["lat"], p["lon"]) for p in platforms if "lat" in p and "lon" in p]
        if not full or not coords:
            continue
        modes = 0
        for p in platforms:
            for m in p.get("modes") or []:
                modes |= motis_mode(m)
        importance = max((p.get("importance") or 0 for p in platforms), default=0)
        town, name = split_name(full)
        out.append(Stop(name, town, sum(c[0] for c in coords) / len(coords),
                        sum(c[1] for c in coords) / len(coords), [parent], modes,
                        int(importance * 20_000), [], coords))
    return out


def motis_stops_around(lat: float, lon: float, radius: float) -> list[Stop]:
    d_lat = radius / 111_000
    d_lon = radius / (111_000 * math.cos(math.radians(lat)))
    return parse_map_stops(motis_get("/map/stops", {
        "min": f"{lat - d_lat:.5f},{lon - d_lon:.5f}",
        "max": f"{lat + d_lat:.5f},{lon + d_lon:.5f}"}))


def motis_nearby(lat: float, lon: float, limit: int = 3,
                 preferred: str | None = None) -> list[StopGroup]:
    found = motis_stops_around(lat, lon, 650)
    if len({s.name for s in found}) < 3:
        found = motis_stops_around(lat, lon, 1500)
    candidates = [(s, s.distance_from(lat, lon)) for s in found]
    return sorted(group(candidates, preferred), key=lambda g: g.score, reverse=True)[:limit]


def motis_search(text: str, loc: tuple[float, float] | None = None) -> list[StopGroup]:
    params = {"text": text, "type": "STOP"}
    if loc:
        params["place"] = f"{loc[0]:.5f},{loc[1]:.5f}"
        params["placeBias"] = "5"
    out, seen = [], set()
    for r in motis_get("/geocode", params)[:12]:
        stop_id, full, lat, lon = r.get("id"), r.get("name"), r.get("lat"), r.get("lon")
        if stop_id is None or full is None or lat is None or lon is None or full in seen:
            continue
        seen.add(full)
        town, name = split_name(full)
        dist = distance(loc[0], loc[1], lat, lon) if loc else 0
        out.append(StopGroup(name, town, dist, lat, lon, [stop_id], 0, 0, []))
    return out[:8]


# --- Providers: callables from a stop group to its board ---

class MotisProvider:
    def board(self, g: StopGroup) -> StopBoard:
        return motis_board(g.tpcs, g.town)


class PragueProvider:
    """Golemio when a key is set; MOTIS otherwise, or when Golemio rejects it / is down."""

    def __init__(self, config, log=lambda message: None):
        self.config = config
        self.log = log

    def motis_ids(self, g: StopGroup) -> list[str]:
        """MOTIS ids for a bundled PID stop: same-named MOTIS stops within ~350 m. Cached."""
        cache = dict(self.config.get("motisIDs") or {})
        if cache.get(g.id):
            return cache[g.id]
        want = normalize(g.name)
        ids = [t for s in motis_stops_around(g.lat, g.lon, 350)
               if normalize(s.name) == want for t in s.tpcs]
        if ids:
            cache[g.id] = ids
            self.config.set("motisIDs", cache)
        return ids

    def board(self, g: StopGroup) -> StopBoard:
        key = self.config.golemio_key
        if key:
            try:
                return golemio_board(g, key)
            except (MissingKey, Unavailable) as e:
                self.log(f"Golemio failed ({e}), using MOTIS")
        ids = self.motis_ids(g)
        if not ids:
            raise Unavailable()
        return motis_board(ids, "Praha")
