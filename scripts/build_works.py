"""Road works in Bengaluru: government tenders (KPPP) + roads under construction (OpenStreetMap).

Runs in GitHub Actions. Writes data/works.json:
  {"generated", "tenders": [...], "construction": [...], "status": {...}}
Each tender is matched to road segments by road name + ward found in its title (best effort, marked as such).
"""
import json, os, re, sys, time, urllib.parse, urllib.request
from datetime import datetime, timezone

KPPP = "https://kppp.karnataka.gov.in/supplier-registration-service/v1/api/portal-service"
UA = "bengaluru-road-watch/0.3 (github.com/tones9/Bengaluru-road-watch; civic accountability map)"
DEPTS = {12603: "Central", 12607: "East", 12610: "North", 12608: "South", 12609: "West",
         12625: "GBA", 12290: "B-SMILE", 671: "BBMP"}
STATUSES = ["PUBLISHED", "CLOSED", "UNDER_EVALUATION", "EVALUATION_COMPLETED", "AWARDED"]
SINCE = datetime(2025, 1, 1)
ROADISH = re.compile(r"\b(road|roads|rd|asphalt|asphalting|white[- ]?top|whitetop|pothole|resurfac|carriageway|"
                     r"milling|footpath|cross|main|street|layout roads|junction|flyover|underpass|tendersure|"
                     r"black ?top|bt road|cc road|concrete road)\b", re.I)
NOT_ROAD = re.compile(r"\b(hospital|school building|samudhaya|bhavana|toilet|crematorium|lake|park development|"
                      r"street ?light|garbage|solid waste|computer|software|printing|vehicle hire)\b", re.I)


def req(url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                               headers={"User-Agent": UA, "Accept": "application/json", "Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=60) as resp:
        return json.load(resp), dict(resp.headers)


def dt(s):
    if not s:
        return None
    if isinstance(s, (int, float)):
        return datetime.fromtimestamp(s / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    for f in ("%d-%m-%Y %H:%M:%S", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, f).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def kppp_tenders(log):
    rows = {}
    for dept, corp in DEPTS.items():
        for st in STATUSES:
            page = 0
            while True:
                try:
                    data, _ = req(f"{KPPP}/works/search-eproc-tenders?page={page}&size=500",
                                  {"category": "WORKS", "status": st, "tenderType": "OPEN", "deptId": dept})
                except Exception as e:
                    log.append(f"search {corp} {st} p{page}: {e}")
                    break
                if not data:
                    break
                stop = False
                for r in data:
                    pub = r.get("publishedDate")
                    try:
                        if pub and datetime.strptime(pub[:10], "%d-%m-%Y") < SINCE:
                            stop = True
                            continue
                    except ValueError:
                        pass
                    t = (r.get("title") or "") + " " + (r.get("workCategoryName") or "")
                    if not ROADISH.search(t) or NOT_ROAD.search(r.get("title") or ""):
                        continue
                    rows[r["nitId"]] = {
                        "nit": r["nitId"], "no": r.get("tenderNumber"), "title": (r.get("title") or "").strip(),
                        "status": r.get("status"), "corp": corp, "office": r.get("locationName"),
                        "category": r.get("workCategoryName"), "ecv": r.get("ecv"),
                        "published": dt(pub), "bids_close": dt(r.get("tenderClosureDate")),
                    }
                if stop or len(data) < 500:
                    break
                page += 1
                time.sleep(1)
            time.sleep(1)
    return rows


def enrich(rows, log, limit=1200):
    """Award date + winning contractor for awarded/selected tenders (newest first)."""
    todo = sorted([r for r in rows.values() if r["status"] in ("AWARDED", "EVALUATION_COMPLETED")],
                  key=lambda r: r["published"] or "", reverse=True)[:limit]
    for r in todo:
        try:
            full, _ = req(f"{KPPP}/{r['nit']}/works-tender-full-view")
            r["awarded"] = dt((full.get("tenderAwardDatesDTO") or {}).get("awardedDates"))
            r["desc"] = ((full.get("tenderSchedule") or {}).get("description") or "")[:400]
        except Exception as e:
            log.append(f"full {r['nit']}: {e}")
        try:
            a, _ = req(f"{KPPP}/bids/{r['nit']}/tender-category/WORKS/get-selected-bid-for-lumpsum")
            a = a[0] if isinstance(a, list) and a else a
            if isinstance(a, dict):
                r["contractor"] = a.get("supplierName") or a.get("bidderName") or a.get("name")
                r["bid"] = a.get("bidValue")
        except Exception as e:
            log.append(f"award {r['nit']}: {e}")
        time.sleep(0.7)


def osm_construction(log):
    q = ('[out:json][timeout:180];(way["highway"="construction"](12.83,77.455,13.145,77.79);'
         'way["construction"]["highway"](12.83,77.455,13.145,77.79););out tags geom;')
    for url in ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]:
        try:
            r = urllib.request.Request(url, data=urllib.parse.urlencode({"data": q}).encode(), headers={"User-Agent": UA})
            with urllib.request.urlopen(r, timeout=240) as resp:
                els = json.load(resp)["elements"]
            out = []
            for el in els:
                t = el.get("tags", {})
                if "geometry" not in el:
                    continue
                out.append({"id": el["id"], "name": t.get("name", ""), "kind": t.get("construction") or t.get("highway"),
                            "start": t.get("start_date", ""), "opening": t.get("opening_date", ""),
                            "geom": [v for p in el["geometry"] for v in (round(p["lon"], 5), round(p["lat"], 5))]})
            return out
        except Exception as e:
            log.append(f"overpass {url}: {e}")
    return []


def norm(s):
    s = s.lower()
    s = re.sub(r"\b(rd)\b", "road", s)
    s = re.sub(r"\b(main road|main rd)\b", "main road", s)
    return re.sub(r"[^a-z0-9 ]", " ", s)


def match_roads(rows):
    """Link tenders to road segments: road name appears in the title and the ward number/name matches."""
    names = json.load(open("data/roads/names.json"))
    wards = {f["properties"]["key"]: f["properties"] for f in json.load(open("data/wards.geojson"))["features"]}
    by_corp = {}
    for n in names:
        if len(n[0]) < 8 or not re.search(r"(road|main|cross|street|avenue|marg|layout|circle)", n[0], re.I):
            continue
        by_corp.setdefault(n[3].split("-")[0], []).append(n)
    for r in rows.values():
        t = norm(r["title"] + " " + r.get("desc", ""))
        wards_hit = set()
        for m in re.finditer(r"ward\s*(?:no\.?|number)?\s*[-:.]?\s*(\d{1,3})", t):
            if r["corp"] in ("Central", "East", "North", "South", "West"):
                wards_hit.add(f"{r['corp']}-{int(m.group(1))}")
        for k, w in wards.items():
            if r["corp"] in ("Central", "East", "North", "South", "West") and not k.startswith(r["corp"]):
                continue
            if len(w["ward_name"]) > 5 and norm(w["ward_name"]).replace(" ward", "").strip() in t:
                wards_hit.add(k)
        hits = []
        pool = by_corp.get(r["corp"]) or [n for v in by_corp.values() for n in v]
        for n in pool:
            nn = norm(n[0]).strip()
            if nn and f" {nn} " in f" {t} ":
                if not wards_hit or n[3] in wards_hit:
                    hits.append(n)
        r["wards"] = sorted(wards_hit)
        # names.json has one entry per (name, ward); keep the matched ones (cap to avoid generic names flooding)
        r["roads"] = [[h[1], h[2], h[0], h[3]] for h in hits[:12]]
        r["match"] = "road+ward" if hits and wards_hit else ("road name only" if hits else ("ward only" if wards_hit else "none"))


def main():
    log, status = [], {}
    rows = kppp_tenders(log)
    status["kppp_tenders"] = len(rows)
    if rows:
        enrich(rows, log)
        match_roads(rows)
    cons = osm_construction(log)
    status["osm_construction"] = len(cons)
    status["log"] = log[:40]
    out = {"generated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "status": status,
           "tenders": sorted(rows.values(), key=lambda r: r.get("published") or "", reverse=True),
           "construction": cons}
    if not rows and os.path.exists("data/works.json"):
        old = json.load(open("data/works.json"))
        out["tenders"] = old.get("tenders", [])  # keep last good copy if the portal was unreachable
        status["kept_previous_tenders"] = True
    json.dump(out, open("data/works.json", "w"), separators=(",", ":"))
    print(json.dumps(status, indent=1))


if __name__ == "__main__":
    main()
