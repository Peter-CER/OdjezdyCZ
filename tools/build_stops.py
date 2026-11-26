#!/usr/bin/env python3
"""Build the bundled stop index for Odjezdy (Prague / PID) from the PID GTFS feed.

Usage:
  python3 tools/build_stops.py --region nl         --gtfs gtfs-nl.zip [--border countries-10m.json]
  python3 tools/build_stops.py --region prague     --gtfs PID_GTFS.zip [--pid-stops stops.json]
  python3 tools/build_stops.py --region bratislava --gtfs ba_gtfs.zip

Sources:
  nl          https://gtfs.ovapi.nl/nl/gtfs-nl.zip                     (~300 MB, daily)
  prague      https://data.pid.cz/PID_GTFS.zip  (+ https://data.pid.cz/stops/json/stops.json for town names)
  bratislava  https://www.arcgis.com/sharing/rest/content/items/aba12fd2cbac4843bc7406151bc66106/data
  border      https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-10m.json (Natural Earth)

Writes Odjezdy/Resources/stops_<region>.json, and for bratislava also timetable_bratislava.json
(offline fallback when the imhd.sk live feed is down).

stops_<region>.json:
  {"v": 2, "region": "...", "built": "YYYY-MM-DD", "stops": [
     [name, town, lat, lon, [stopRef, ...], modes_bitmask, weekday_departures, [[line, mode, "rrggbb"], ...],
   [[platformLat, platformLon], ...]]   (platforms; empty when the stop has a single position)
  ]}
  stopRef = what the region's live API takes: OVapi timing point code (nl), GTFS stop_id (prague),
            stop_code = imhd.sk stop number (bratislava).
  modes: 1=tram 2=metro 4=bus 8=ferry 16=train 32=trolleybus
"""
import argparse
import csv
import datetime as dt
import io
import json
import math
import os
import sys
import zipfile
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "Odjezdy", "Resources")

TRAM, METRO, BUS, FERRY, TRAIN, TROLLEY = 1, 2, 4, 8, 16, 32


def mode_bit(route_type, region):
    t = int(route_type)
    if t in (0, 900, 7, 1400):  # tram, funicular (Petřín) counts as tram
        return TRAM
    if t in (1, 401, 402):
        return METRO
    if t in (3, 700, 702, 704, 715):
        return BUS
    if t in (4, 1000, 1200):
        return FERRY
    if t == 11 or t == 800:
        return TROLLEY
    if t == 2 or 100 <= t < 200:
        return None if region == "nl" else TRAIN   # OVapi has no NS trains
    return None


def open_csv(zf, name):
    return csv.DictReader(io.TextIOWrapper(zf.open(name), encoding="utf-8-sig", newline=""))


class Calendar:
    """calendar.txt rules + calendar_dates.txt exceptions."""

    def __init__(self, zf):
        names = set(zf.namelist())
        self.rules = {}   # sid -> (days[7], start, end)
        self.add = defaultdict(set)
        self.remove = defaultdict(set)
        if "calendar.txt" in names:
            for r in open_csv(zf, "calendar.txt"):
                days = [int(r[d]) for d in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")]
                self.rules[r["service_id"]] = (days, r["start_date"], r["end_date"])
        if "calendar_dates.txt" in names:
            for r in open_csv(zf, "calendar_dates.txt"):
                (self.add if r["exception_type"] == "1" else self.remove)[r["service_id"]].add(r["date"])

    def active(self, date):
        ds = date.strftime("%Y%m%d")
        out = set()
        for sid, (days, start, end) in self.rules.items():
            if start <= ds <= end and days[date.weekday()]:
                out.add(sid)
        for sid, dates in self.add.items():
            if ds in dates:
                out.add(sid)
        for sid, dates in self.remove.items():
            if ds in dates:
                out.discard(sid)
        return out

    def export(self, sids):
        out = {}
        for sid in sids:
            days, start, end = self.rules.get(sid, ([0] * 7, "", ""))
            out[sid] = {"days": days, "start": start, "end": end,
                        "add": sorted(self.add.get(sid, ())), "remove": sorted(self.remove.get(sid, ()))}
        return out


def pick_weekday(cal):
    """A representative Tuesday with service (next Tuesday from today, within two weeks)."""
    d = dt.date.today()
    best = (0, d)
    for _ in range(21):
        if d.weekday() == 1:
            n = len(cal.active(d))
            if n:
                return d
        best = max(best, (len(cal.active(d)), d))
        d += dt.timedelta(days=1)
    return best[1]


def natural(s):
    digits = "".join(ch for ch in s if ch.isdigit())
    return (int(digits) if digits else math.inf, s)


def build(region, gtfs_path, pid_stops=None):
    zf = zipfile.ZipFile(gtfs_path)
    cal = Calendar(zf)
    day = pick_weekday(cal)
    active = cal.active(day)
    print(f"[{region}] representative weekday {day}, {len(active)} active services", file=sys.stderr)

    routes = {}
    for r in open_csv(zf, "routes.txt"):
        bit = mode_bit(r["route_type"], region)
        if bit:
            routes[r["route_id"]] = (r["route_short_name"].strip(), bit, (r.get("route_color") or "").lower())

    trips = {}       # trip_id -> (route_id, service_id, headsign)
    for t in open_csv(zf, "trips.txt"):
        if t["route_id"] in routes:
            trips[t["trip_id"]] = (t["route_id"], t["service_id"], t.get("trip_headsign", "").strip())
    print(f"[{region}] trips: {len(trips)}", file=sys.stderr)

    # town names for Prague from PID's stops.json (node -> municipality)
    node_town = {}
    if pid_stops:
        for g in json.load(open(pid_stops))["stopGroups"]:
            node_town[str(g.get("node"))] = g.get("municipality", "")

    # Group boarding stops into areas; stopRef is what the live API takes.
    areas = {}
    stop_area = {}
    parents = {}
    for s in open_csv(zf, "stops.txt"):
        lt = s.get("location_type") or "0"
        if lt == "1":
            parents[s["stop_id"]] = s
            continue
        if lt != "0":
            continue
        if region == "nl":
            ifopt = s.get("stop_ifopt", "")
            if not ifopt.startswith("NL:Q:"):
                continue
            ref, area_id = ifopt[5:], s["parent_station"] or ("solo:" + s["stop_id"])
            full = s["stop_name"]
            town, _, name = full.partition(", ")
            if not name:
                town, name = "", full
        elif region == "prague":
            node = s.get("asw_node_id") or ""
            if not node:
                continue
            # node + name: a node can hold differently named stops (Letiště / Schengenská,
            # Dejvická / Vítězné náměstí); each keeps its own name.
            ref, area_id = s["stop_id"], "node:" + node + ":" + s["stop_name"]
            name, town = s["stop_name"], node_town.get(node, "")
        else:  # bratislava
            code = (s.get("stop_code") or "").strip()
            if not code:
                continue
            ref, area_id = code, "code:" + code
            name, town = s["stop_name"], "Bratislava"
        a = areas.setdefault(area_id, {"refs": set(), "lat": [], "lon": [], "names": [], "town": town})
        a["refs"].add(ref)
        a["lat"].append(float(s["stop_lat"]))
        a["lon"].append(float(s["stop_lon"]))
        a["names"].append(name)
        stop_area[s["stop_id"]] = area_id

    # Busyness + lines; for bratislava also the offline timetable.
    deps = defaultdict(int)
    lines = defaultdict(dict)
    want_tt = region == "bratislava"
    tt = defaultdict(lambda: defaultdict(list))   # area -> (line, headsign, sid) -> [minutes]
    last_seq = {}
    rows = []
    n = 0
    for st in open_csv(zf, "stop_times.txt"):
        n += 1
        if n % 5_000_000 == 0:
            print(f"  stop_times: {n:,}", file=sys.stderr)
        trip = trips.get(st["trip_id"])
        if trip is None:
            continue
        area = stop_area.get(st["stop_id"])
        if area is None or st.get("pickup_type") == "1":
            continue
        rid, sid, headsign = trip
        short, bit, color = routes[rid]
        if short:
            lines[area][(short, bit)] = color
        if sid in active:
            deps[area] += 1
        if want_tt:
            seq = int(st["stop_sequence"])
            last_seq[st["trip_id"]] = max(seq, last_seq.get(st["trip_id"], -1))
            hh, mm, _ = (st["departure_time"] or st["arrival_time"]).split(":")
            rows.append((st["trip_id"], seq, area, short, headsign or "", sid, int(hh) * 60 + int(mm)))

    out = []
    for area_id, a in areas.items():
        name = max(set(a["names"]), key=a["names"].count)
        p = parents.get(area_id)
        lat = float(p["stop_lat"]) if p else sum(a["lat"]) / len(a["lat"])
        lon = float(p["stop_lon"]) if p else sum(a["lon"]) / len(a["lon"])
        modes = 0
        for (_, bit) in lines[area_id]:
            modes |= bit
        ls = sorted(lines[area_id].items(), key=lambda kv: (kv[0][1] != METRO, kv[0][1] != TRAM, natural(kv[0][0])))
        # Each platform's position, so distance is measured to the nearest one, not the centre
        # (big stops like Dejvická spread their platforms over 300+ m).
        plats = sorted({(round(la, 5), round(lo, 5)) for la, lo in zip(a["lat"], a["lon"])})
        out.append([name, a["town"], round(lat, 6), round(lon, 6), sorted(a["refs"]), modes,
                    deps.get(area_id, 0), [[l, b, c] for (l, b), c in ls[:20]],
                    [[la, lo] for la, lo in plats] if len(plats) > 1 else []])
    out.sort(key=lambda s: (s[2], s[3]))
    print(f"[{region}] stop areas: {len(out)}  (with departures: {sum(1 for s in out if s[6])})", file=sys.stderr)
    stops = {"v": 2, "region": region, "built": dt.date.today().isoformat(), "day": day.isoformat(), "stops": out}

    timetable = None
    if want_tt:
        for trip_id, seq, area, short, headsign, sid, minute in rows:
            if seq == last_seq[trip_id]:
                continue   # arrival at the terminus, not a departure
            code = area.split(":", 1)[1]
            tt[code][(short, headsign, sid)].append(minute)
        stops_tt = {}
        used = set()
        for code, groups in tt.items():
            entries = []
            for (short, headsign, sid), mins in sorted(groups.items()):
                mins.sort()
                deltas = [mins[0]] + [b - a for a, b in zip(mins, mins[1:])]   # delta-encoded
                entries.append([short, headsign, sid, deltas])
                used.add(sid)
            stops_tt[code] = entries
        timetable = {"v": 1, "built": dt.date.today().isoformat(), "timezone": "Europe/Bratislava",
                     "services": cal.export(used), "stops": stops_tt}
    return stops, timetable


def build_border(topo_path):
    """Extract the European Netherlands from Natural Earth TopoJSON (id 528)."""
    topo = json.load(open(topo_path))
    sx, sy = topo["transform"]["scale"]
    tx, ty = topo["transform"]["translate"]
    arcs = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx
            y += dy
            pts.append((x * sx + tx, y * sy + ty))
        arcs.append(pts)

    def ring(idxs):
        pts = []
        for i in idxs:
            a = arcs[i] if i >= 0 else list(reversed(arcs[~i]))
            pts.extend(a if not pts else a[1:])
        return pts

    geom = next(g for g in topo["objects"]["countries"]["geometries"] if g.get("id") == "528")
    polys = geom["arcs"] if geom["type"] == "MultiPolygon" else [geom["arcs"]]
    rings = []
    for poly in polys:
        outer = ring(poly[0])
        if min(p[1] for p in outer) < 45:  # skip Caribbean Netherlands
            continue
        rings.append([[round(lat, 5), round(lon, 5)] for lon, lat in outer])
    return {"rings": rings}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="prague", choices=["prague", "nl", "bratislava"],
                    help="Odjezdy only ships prague; the others are kept for forks")
    ap.add_argument("--gtfs", required=True)
    ap.add_argument("--border", help="Natural Earth TopoJSON (nl only)")
    ap.add_argument("--pid-stops", help="PID stops.json for town names (prague only)")
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)

    stops, timetable = build(args.region, args.gtfs, args.pid_stops)
    with open(os.path.join(OUT_DIR, f"stops_{args.region}.json"), "w") as f:
        json.dump(stops, f, ensure_ascii=False, separators=(",", ":"))
    if timetable:
        with open(os.path.join(OUT_DIR, f"timetable_{args.region}.json"), "w") as f:
            json.dump(timetable, f, ensure_ascii=False, separators=(",", ":"))
    if args.border:
        with open(os.path.join(OUT_DIR, "nl_border.json"), "w") as f:
            json.dump(build_border(args.border), f, separators=(",", ":"))
    print("Done.", file=sys.stderr)


if __name__ == "__main__":
    main()
