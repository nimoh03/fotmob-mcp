"""Probe: what team stats does FotMob give? Usage: python probe_stats.py "Team Name" """
import json, sys
from pathlib import Path
from fotmob_mcp.client import FotMobClient
from five_min import candidates, team_page, page_matches

client = FotMobClient()
name = sys.argv[1]

cands = candidates(name)
print("candidates:", [(c["id"], c.get("name")) for c in cands[:5]])
tid = str(cands[0]["id"])
page = team_page(tid)

done = sorted((m for m in page_matches(page) if m["finished"] and not m["cancelled"]),
              key=lambda m: m["date"], reverse=True)
print(f"\nfinished matches on team page: {len(done)}")
for m in done[:5]:
    print(" ", m["id"], m["date"].date(), m["home"], m["score"], m["away"])

# top-level keys of the team page (to spot any season stats sections)
print("\nteam page keys:", list(page.keys()))
print("team page 'stats' keys:", list((page.get("stats") or {}).keys()))

mid = done[0]["id"]
data = client.get_json("/api/data/matchDetails", {"matchId": mid}, use_cache=False)
Path("probe_match.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

content = data.get("content") or {}
print("\nmatch content keys:", list(content.keys()))
stats = (content.get("stats") or {})
print("stats keys:", list(stats.keys()))

def walk(o, depth=0):
    """print every stat entry: title/key and values"""
    if isinstance(o, dict):
        if "stats" in o and isinstance(o["stats"], list) and o.get("title") is not None:
            print("\n== group:", o.get("title"))
        if "title" in o and "stats" in o and not isinstance(o["stats"], list):
            pass
        if o.get("key") and "stats" in o and isinstance(o["stats"], list) and all(
                not isinstance(x, (dict, list)) for x in o["stats"]):
            print(f"   {o.get('title')!s:<28} key={o['key']:<26} {o['stats']}")
        for v in o.values():
            walk(v, depth + 1)
    elif isinstance(o, list):
        for v in o:
            walk(v, depth + 1)

walk(stats)
print("\nraw match saved to probe_match.json")