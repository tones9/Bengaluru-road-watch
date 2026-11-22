"""Road works in Bengaluru: government tenders (KPPP) + roads under construction (OpenStreetMap).

Runs in GitHub Actions. Writes data/works.json:
  {"generated", "tenders": [...], "construction": [...], "status": {...}}
Each tender is matched to road segments by road name + ward found in its title (best effort, marked as such).
"""
import json, os, re, sys, time, urllib.parse, urllib.request, functools
print = functools.partial(print, flush=True)
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
    with urllib.request.urlopen(r, timeout=25) as resp:
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
    # Reachability check first: GitHub's servers may be blocked by the portal
    try:
        t0 = time.time()
        data, _ = req(f"{KPPP}/works/search-eproc-tenders?page=0&size=5",
                      {"category": "WORKS", "status": "AWARDED", "tenderType": "OPEN", "deptId": 12608})
        print(f"KPPP reachable: {len(data)} rows in {time.time()-t0:.1f}s")
    except Exception as e:
        print(f"KPPP unreachable: {e!r}")
        log.append(f"KPPP unreachable from runner: {e!r}")
        return rows
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
                print(f"{corp} {st} page {page}: {len(data)} rows")
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
    print(f"enriching {len(todo)} tenders")
    for i, r in enumerate(todo):
        if i % 100 == 0:
            print(f"  {i}/{len(todo)}")
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


CT = "https://civictrace-bengaluru.vercel.app/data"
BUYER = {"BCCC": "Central", "BECC": "East", "BNCC": "North", "BSCC": "South", "BWCC": "West",
         "GBA": "GBA", "BSMILE": "B-SMILE", "BBMP": "BBMP"}


def civictrace_tenders(log):
    """Fallback: the KPPP portal blocks GitHub's servers, so read CivicTrace's public export of the same
    KPPP records (github.com/dakshinsiva/civic-trace, MIT)."""
    rows = {}
    try:
        idx, _ = req(f"{CT}/tenders-index.json")
    except Exception as e:
        log.append(f"civictrace index: {e!r}")
        return rows
    print(f"CivicTrace index: {len(idx)} tenders")
    keep = {}
    for t in idx:
        if t.get("buyer") not in BUYER:
            continue
        title = t.get("title") or ""
        if not ROADISH.search(title + " " + (t.get("work_category") or "")) or NOT_ROAD.search(title):
            continue
        keep[int(t["nit_id"])] = t
    buckets = sorted({f"b{n % 256:03d}" for n in keep})
    print(f"{len(keep)} road-related; fetching {len(buckets)} detail files")
    for b in buckets:
        try:
            docs, _ = req(f"{CT}/tenders/{b}.json")
        except Exception as e:
            log.append(f"civictrace {b}: {e!r}")
            continue
        for k, d in docs.items():
            n = int(k)
            if n not in keep:
                continue
            pub = (d.get("published_at") or "")[:10]
            if pub and pub < "2025-01-01":
                continue
            sel = d.get("selection") or {}
            loc = d.get("location") or None
            rows[n] = {
                "nit": n, "no": d.get("tender_number"), "title": (d.get("title") or "").strip(),
                "status": d.get("status"), "corp": BUYER[d.get("buyer_code") or keep[n]["buyer"]],
                "office": d.get("division"), "category": d.get("work_category"), "ecv": d.get("ecv"),
                "published": pub or None, "bids_close": (d.get("close_at_final") or "")[:10] or None,
                "awarded": (d.get("award_date") or "")[:10] or None,
                "contractor": sel.get("supplier_name"), "bid": sel.get("bid_value"),
                "site": [loc["lat"], loc["lon"], loc.get("name")] if loc else None,
                "via": "CivicTrace",
            }
        time.sleep(0.3)
    print(f"{len(rows)} road tenders since 2025")
    return rows


SKIP_KINDS = {"service", "footway", "path", "cycleway", "steps", "track", "pedestrian", "bridleway", "corridor", "platform"}


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
                if "geometry" not in el or (t.get("construction") or "") in SKIP_KINDS:
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
    by_corp, name_wards = {}, {}
    for n in names:
        name_wards[n[0]] = name_wards.get(n[0], 0) + 1
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
        site = r.get("site")
        if site and not hits:
            import math
            for n in pool:
                if abs(n[4] - site[0]) < 0.002 and abs(n[5] - site[1]) < 0.002 and norm(n[0]).strip() in t:
                    hits.append(n)
        if not wards_hit and not (site and hits):
            # without a ward, only trust distinctive names (not "2nd Cross Road") that exist in few wards
            hits = [h for h in hits if not re.match(r"^\s*\d+\s*(st|nd|rd|th)?\b", h[0], re.I) and name_wards.get(h[0], 0) <= 3]
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
    else:
        rows = civictrace_tenders(log)
        status["civictrace_tenders"] = len(rows)
    if rows:
        match_roads(rows)
    # geometry for matched road segments so the map can draw them without loading every tile
    want = {}
    for r in rows.values():
        for rid, cell, *_ in r.get("roads", []):
            want.setdefault(cell, set()).add(rid)
    geom = {}
    for cell, ids in want.items():
        for fn in ("data/roads/major.json", f"data/roads/c_{cell}.json"):
            if not os.path.exists(fn):
                continue
            d = json.load(open(fn))
            for rec in (d["roads"] if isinstance(d, dict) else d):
                if rec[0] in ids:
                    geom[str(rec[0])] = rec
    cons = osm_construction(log)
    if not cons and os.path.exists("data/works.json"):
        cons = json.load(open("data/works.json")).get("construction", [])
        status["kept_previous_construction"] = True
    status["osm_construction"] = len(cons)
    status["log"] = log[:40]
    out = {"generated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "status": status,
           "tenders": sorted(rows.values(), key=lambda r: r.get("published") or "", reverse=True),
           "construction": cons, "geom": geom}
    if not rows and os.path.exists("data/works.json"):
        old = json.load(open("data/works.json"))
        out["tenders"] = old.get("tenders", [])  # keep last good copy if the portal was unreachable
        status["kept_previous_tenders"] = True
    json.dump(out, open("data/works.json", "w"), separators=(",", ":"))
    print(json.dumps(status, indent=1))


if __name__ == "__main__":
    main()
