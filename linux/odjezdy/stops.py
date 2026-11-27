"""The bundled PID stop list, nearby ranking and search. Mirrors StopIndex.swift."""

from __future__ import annotations

import json
import math
import unicodedata

from .models import (FERRY, METRO, TRAM, LineInfo, Stop, StopGroup, distance)


def parse_stops(data: bytes | str) -> list[Stop]:
    try:
        rows = json.loads(data)["stops"]
    except (ValueError, KeyError, TypeError):
        return []
    out = []
    for r in rows:
        try:
            name, town, lat, lon, tpcs, modes, deps, lines = r[:8]
            platforms = [(p[0], p[1]) for p in r[8]] if len(r) >= 9 else []
            out.append(Stop(name, town, float(lat), float(lon), list(tpcs), int(modes), int(deps),
                            [LineInfo(l[0], int(l[1]), l[2]) for l in lines], platforms))
        except (ValueError, TypeError, IndexError):
            continue
    return out


def group(candidates: list[tuple[Stop, float]], preferred: str | None = None) -> list[StopGroup]:
    """Merge same-named areas within 150 m (e.g. bus vs tram side of a hub) and score them:
    log(1 + weekday departures) × mode bonus / (1 + walk/250 m) × preference boost."""
    clusters: list[list[tuple[Stop, float]]] = []
    for c in candidates:
        for g in clusters:
            first = g[0][0]
            if (first.name == c[0].name and first.town == c[0].town
                    and distance(first.lat, first.lon, c[0].lat, c[0].lon) < 150):
                g.append(c)
                break
        else:
            clusters.append([c])
    result = []
    for g in clusters:
        first = g[0][0]
        deps = sum(s.weekday_departures for s, _ in g)
        modes = 0
        lines, seen, tpcs = [], set(), set()
        for s, _ in g:
            modes |= s.modes
            tpcs.update(s.tpcs)
            for l in s.lines:
                if (l.mode, l.number) not in seen:
                    seen.add((l.mode, l.number))
                    lines.append(l)
        dist = min(d for _, d in g)
        bonus = 1.3 if modes & METRO else 1.15 if modes & TRAM else 1.1 if modes & FERRY else 1.0
        score = math.log(1 + deps) * bonus / (1 + dist / 250)
        sg = StopGroup(first.name, first.town, dist, first.lat, first.lon, sorted(tpcs),
                       modes, deps, lines, score)
        if sg.id == preferred:
            sg.score *= 1.6
        result.append(sg)
    return result


def normalize(s: str) -> str:
    """Case- and accent-insensitive form: "Hlavní nádraží" → "hlavni nadrazi"."""
    decomposed = unicodedata.normalize("NFKD", s.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c)).replace("-", " ")


class StopIndex:
    def __init__(self, stops: list[Stop]):
        self.stops = stops
        self._names: list[tuple[str, str]] | None = None

    @classmethod
    def load(cls, path: str) -> "StopIndex":
        try:
            with open(path, "rb") as f:
                return cls(parse_stops(f.read()))
        except OSError:
            return cls([])

    def has_stop_near(self, lat: float, lon: float, metres: float) -> bool:
        return any(
            s.weekday_departures > 0 and abs(s.lat - lat) < 0.01 and abs(s.lon - lon) < 0.015
            and s.distance_from(lat, lon) <= metres
            for s in self.stops)

    def search(self, query: str, loc: tuple[float, float] | None = None,
               limit: int = 8) -> list[StopGroup]:
        """Name starts with the query, then a word starts with it, then contains; busier first."""
        q = normalize(query.strip())
        if len(q) < 2:
            return []
        if self._names is None:
            self._names = [(normalize(s.name), normalize(f"{s.town} {s.name}")) for s in self.stops]
        hits: list[tuple[Stop, int]] = []
        for s, (name, full) in zip(self.stops, self._names):
            if s.weekday_departures <= 0:
                continue
            if name.startswith(q):
                rank = 0
            elif " " + q in name or "," + q in name:
                rank = 1
            elif q in name or q in full:
                rank = 2
            else:
                continue
            hits.append((s, rank))
        rank_of: dict[str, int] = {}
        for s, rank in hits:
            key = f"{s.town}|{s.name}"
            rank_of[key] = min(rank, rank_of.get(key, 9))
        groups = group([(s, s.distance_from(*loc) if loc else 0) for s, _ in hits])
        # Busy stops first, a better text match is worth ~4.5× the departures, nearer a bit better.
        groups.sort(key=lambda g: math.log(1 + g.weekday_departures)
                    - 1.5 * rank_of.get(g.id, 3) - g.distance / 20_000, reverse=True)
        return groups[:limit]

    def best_stops(self, lat: float, lon: float, limit: int = 3,
                   preferred: str | None = None) -> list[StopGroup]:
        """Everything within 600 m, or the nearest 15 if that's too few; best score first."""
        near = [(s, s.distance_from(lat, lon)) for s in self.stops
                if s.weekday_departures > 0
                and abs(s.lat - lat) <= 0.025 and abs(s.lon - lon) <= 0.04]  # cheap box first
        near.sort(key=lambda c: c[1])
        within = [c for c in near if c[1] <= 600]
        candidates = within if len(within) >= 3 else near[:15]
        return sorted(group(candidates, preferred), key=lambda g: g.score, reverse=True)[:limit]
