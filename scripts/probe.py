"""One-off reachability probe for public road data sources. Writes probe/out.json."""
import json, re, urllib.request, urllib.parse
UA = "bengaluru-road-watch/0.3 (github.com/tones9/Bengaluru-road-watch)"
def get(url, n=4000):
    try:
        r = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(r, timeout=25) as resp:
            b = resp.read(400000)
            return {"status": resp.status, "final": resp.geturl(), "type": resp.headers.get("content-type"), "body": b[:n].decode("utf-8", "replace")}
    except Exception as e:
        return {"error": repr(e)[:300]}
out = {}
for u in ["https://marcs.bbmp.gov.in/", "http://marcs.bbmp.gov.in/", "https://bbmp.gov.in/marcs", "https://marcs.gba.karnataka.gov.in/",
          "https://site.bbmp.gov.in/departmentwebsites/BBMPIT/MARCS.html", "https://gba.karnataka.gov.in/", "https://bbmp.gov.in/",
          "https://roadcutting.bbmp.gov.in/", "https://bbmpgov.com/marcs", "https://site.bbmp.gov.in/Citizenview.html"]:
    out[u] = get(u)
    if "body" in out[u]:
        out[u]["links"] = sorted(set(re.findall(r'href=["\']([^"\']+)', out[u]["body"])))[:80]
        out[u]["body"] = out[u]["body"][:1500]
ck = "https://data.opencity.in/api/3/action/package_search?rows=50&q="
for q in ["pothole", "road cutting", "road history", "patch work", "asphalting", "MARCS"]:
    r = get(ck + urllib.parse.quote(q), n=400000)
    if "body" in r:
        try:
            d = json.loads(r["body"])["result"]
            out["opencity:" + q] = [{"name": p["name"], "title": p["title"], "modified": p.get("metadata_modified"),
                                     "resources": [{"name": x.get("name"), "format": x.get("format"), "url": x.get("url")} for x in p.get("resources", [])][:30]}
                                    for p in d["results"]]
        except Exception as e:
            out["opencity:" + q] = {"parse_error": repr(e), "head": r["body"][:300]}
    else:
        out["opencity:" + q] = r
import os; os.makedirs("probe", exist_ok=True)
json.dump(out, open("probe/out.json", "w"), indent=1)
print("done")
