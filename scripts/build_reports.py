"""Collect public road reports and promises (GitHub issues) into data/reports.json.

Runs in GitHub Actions with GITHUB_TOKEN and GITHUB_REPOSITORY set.
"""
import json, os, re, time, urllib.request

REPO = os.environ["GITHUB_REPOSITORY"]
TOKEN = os.environ["GITHUB_TOKEN"]


def get(url):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def fields(body):
    """Parse a GitHub issue-form body: '### Label\n\nvalue' blocks."""
    out = {}
    for m in re.finditer(r"^### (.+?)\n\n(.*?)(?=\n### |\Z)", body or "", re.S | re.M):
        v = m.group(2).strip()
        out[m.group(1).strip().lower()] = "" if v == "_No response_" else v
    return out


def main():
    items = []
    page = 1
    while True:
        batch = get(f"https://api.github.com/repos/{REPO}/issues?state=all&per_page=100&page={page}")
        if not batch:
            break
        items += [i for i in batch if "pull_request" not in i]
        page += 1
    by_road = {}
    for i in items:
        labels = {l["name"] for l in i.get("labels", [])}
        kind = "promise" if "promise" in labels else "report" if "report" in labels else None
        if not kind:
            continue
        f = fields(i.get("body"))
        road = re.sub(r"\D", "", f.get("road id", ""))
        if not road:
            continue
        photos = re.findall(r"https://github\.com/user-attachments/assets/[\w-]+|https://[^\s)]+\.(?:png|jpe?g|webp)", i.get("body") or "")
        by_road.setdefault(road, []).append({
            "kind": kind,
            "n": i["number"],
            "url": i["html_url"],
            "state": "fixed" if "fixed" in labels else ("closed" if i["state"] == "closed" else "open"),
            "verified": "verified" in labels,
            "created": i["created_at"][:10],
            "problem": f.get("problem", ""),
            "since": f.get("broken since (approx.)", ""),
            "complaint": f.get("official complaint number", ""),
            "who": f.get("who made the promise", ""),
            "role": f.get("their role", ""),
            "promise": f.get("what was promised", ""),
            "deadline": f.get("promised deadline", ""),
            "source": f.get("source (link or where it was said)", ""),
            "details": (f.get("details", "") or "")[:400],
            "photos": photos[:3],
        })
    # Attach each reported road's geometry so the map can always draw it
    geom, want = {}, set(by_road)
    if want and os.path.isdir("data/roads"):
        for fn in os.listdir("data/roads"):
            if fn == "names.json":
                continue
            d = json.load(open(os.path.join("data/roads", fn)))
            for rec in (d["roads"] if isinstance(d, dict) else d):
                if str(rec[0]) in want:
                    geom[str(rec[0])] = rec
    json.dump({"generated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "roads": by_road, "geom": geom},
              open("data/reports.json", "w"), separators=(",", ":"))
    print(f"{sum(len(v) for v in by_road.values())} items on {len(by_road)} roads")


if __name__ == "__main__":
    main()
