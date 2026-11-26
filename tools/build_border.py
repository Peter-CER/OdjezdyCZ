#!/usr/bin/env python3
"""Extract a country's border from Natural Earth TopoJSON into Odjezdy/Resources/<name>_border.json.

Usage:
  curl -o /tmp/countries-10m.json https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-10m.json
  python3 tools/build_border.py --topo /tmp/countries-10m.json            # Czechia (ISO numeric 203)
"""
import argparse
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build_border(topo_path, country_id):
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

    geom = next(g for g in topo["objects"]["countries"]["geometries"] if g.get("id") == country_id)
    polys = geom["arcs"] if geom["type"] == "MultiPolygon" else [geom["arcs"]]
    return {"rings": [[[round(lat, 5), round(lon, 5)] for lon, lat in ring(p[0])] for p in polys]}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--topo", required=True)
    ap.add_argument("--id", default="203", help="ISO 3166 numeric country code (203 = Czechia)")
    ap.add_argument("--name", default="cz")
    a = ap.parse_args()
    out = os.path.join(ROOT, "Odjezdy", "Resources", f"{a.name}_border.json")
    data = build_border(a.topo, a.id)
    json.dump(data, open(out, "w"), separators=(",", ":"))
    print(f"Wrote {out}: {[len(r) for r in data['rings']]} points")
