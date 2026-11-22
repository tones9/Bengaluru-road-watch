"""Download Bengaluru (GBA) roads from OpenStreetMap and tag each with its ward.

Runs in GitHub Actions. Writes:
  data/roads/major.json        main roads (motorway..tertiary), always loaded
  data/roads/c_<i>_<j>.json    side roads, one file per grid cell, loaded when zoomed in
  data/roads/names.json        search index: [name, id, cell, ward_key, lat, lng]
Road record: [osm_id, name, highway, ref, ward_key, [lng,lat,...], cell]
"""
import json, os, sys, time, urllib.parse, urllib.request
from shapely.geometry import shape, LineString
from shapely.strtree import STRtree

S, W, N, E = 12.83, 77.455, 13.145, 77.79
CELL = 0.04
TYPES = "motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street|motorway_link|trunk_link|primary_link|secondary_link|tertiary_link"
MAJOR = {"motorway", "trunk", "primary", "secondary", "tertiary"}
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
UA = "bengaluru-road-watch/0.2 (github.com/tones9/Bengaluru-road-watch)"


def fetch(s, w, n, e):
    q = f'[out:json][timeout:240];way["highway"~"^({TYPES})$"]({s},{w},{n},{e});out tags geom;'
    body = urllib.parse.urlencode({"data": q}).encode()
    for attempt in range(4):
        for url in ENDPOINTS:
            try:
                req = urllib.request.Request(url, data=body, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=300) as r:
                    return json.load(r)["elements"]
            except Exception as ex:
                print(f"{url} failed: {ex}", file=sys.stderr)
        time.sleep(30 * (attempt + 1))
    raise SystemExit("All Overpass endpoints failed")


def cell_of(lng, lat):
    return f"{int((lat - S) / CELL)}_{int((lng - W) / CELL)}"


def main():
    wards = json.load(open("data/wards.geojson"))["features"]
    polys = [shape(w["geometry"]) for w in wards]
    tree = STRtree(polys)
    # 2x2 quadrants keep each Overpass request small
    ms, mw = (S + N) / 2, (W + E) / 2
    elements, seen = [], set()
    for q in [(S, W, ms, mw), (S, mw, ms, E), (ms, W, N, mw), (ms, mw, N, E)]:
        for el in fetch(*q):
            if el.get("type") == "way" and el["id"] not in seen:
                seen.add(el["id"]); elements.append(el)
        time.sleep(5)
    major, cells, names = [], {}, []
    for el in elements:
        if "geometry" not in el:
            continue
        pts = [(round(p["lon"], 5), round(p["lat"], 5)) for p in el["geometry"]]
        if len(pts) < 2:
            continue
        mid = LineString(pts).interpolate(0.5, normalized=True)
        key = None
        for i in tree.query(mid):
            if polys[i].contains(mid):
                key = wards[i]["properties"]["key"]; break
        if key is None:
            continue  # outside GBA limits
        t = el.get("tags", {})
        hw = t.get("highway", "")
        c = cell_of(mid.x, mid.y)
        rec = [el["id"], t.get("name", ""), hw, t.get("ref", ""), key, [v for p in pts for v in p], c]
        (major if hw.replace("_link", "") in MAJOR else cells.setdefault(c, [])).append(rec)
        if rec[1]:
            names.append([rec[1], el["id"], c, key, round(mid.y, 5), round(mid.x, 5)])
    os.makedirs("data/roads", exist_ok=True)
    for f in os.listdir("data/roads"):
        os.remove(os.path.join("data/roads", f))
    dump = lambda p, o: json.dump(o, open(p, "w"), separators=(",", ":"))
    dump("data/roads/major.json", {"generated": time.strftime("%Y-%m-%d"), "cell": CELL, "origin": [S, W], "roads": major})
    for c, rs in cells.items():
        dump(f"data/roads/c_{c}.json", rs)
    uniq = {}
    for n in names:
        uniq.setdefault((n[0], n[3]), n)
    dump("data/roads/names.json", list(uniq.values()))
    print(f"{len(major)} main + {sum(map(len, cells.values()))} side road segments in {len(cells)} cells; {len(uniq)} named")


if __name__ == "__main__":
    main()
