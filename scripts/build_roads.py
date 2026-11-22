"""Download South Bengaluru roads from OpenStreetMap and tag each with its GBA ward.

Runs in GitHub Actions (needs internet). Output: data/roads.json
Each road: [osm_way_id, name, highway, ref, ward_no, [lng,lat,lng,lat,...]]
"""
import json, sys, time, urllib.parse, urllib.request
from shapely.geometry import shape, LineString, Point
from shapely.strtree import STRtree

BBOX = (12.83, 77.485, 12.955, 77.705)  # south, west, north, east
TYPES = "motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street|motorway_link|trunk_link|primary_link|secondary_link|tertiary_link"
QUERY = f"""[out:json][timeout:180];
way["highway"~"^({TYPES})$"]({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});
out tags geom;"""
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]


def fetch():
    body = urllib.parse.urlencode({"data": QUERY}).encode()
    for attempt in range(3):
        for url in ENDPOINTS:
            try:
                req = urllib.request.Request(url, data=body, headers={"User-Agent": "bengaluru-road-watch/0.1 (github.com/tones9/Bengaluru-road-watch)"})
                with urllib.request.urlopen(req, timeout=240) as r:
                    return json.load(r)
            except Exception as e:  # try next mirror
                print(f"{url} failed: {e}", file=sys.stderr)
        time.sleep(30)
    raise SystemExit("All Overpass endpoints failed")


def main():
    wards = json.load(open("data/wards-south.geojson"))["features"]
    polys = [shape(w["geometry"]) for w in wards]
    tree = STRtree(polys)
    data = fetch()
    roads = []
    for el in data.get("elements", []):
        if el.get("type") != "way" or "geometry" not in el:
            continue
        pts = [(round(p["lon"], 5), round(p["lat"], 5)) for p in el["geometry"]]
        if len(pts) < 2:
            continue
        line = LineString(pts)
        mid = line.interpolate(0.5, normalized=True)
        ward = None
        for i in tree.query(mid):
            if polys[i].contains(mid):
                ward = wards[i]["properties"]["ward_no"]
                break
        if ward is None:
            continue  # outside South City Corporation
        t = el.get("tags", {})
        flat = [c for p in pts for c in p]
        roads.append([el["id"], t.get("name", ""), t.get("highway", ""), t.get("ref", ""), ward, flat])
    json.dump({"generated": time.strftime("%Y-%m-%d"), "source": "OpenStreetMap contributors (ODbL)", "roads": roads},
              open("data/roads.json", "w"), separators=(",", ":"))
    print(f"{len(roads)} road segments written")


if __name__ == "__main__":
    main()
