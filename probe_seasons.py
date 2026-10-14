"""Probe: last-season fixtures, old-match data quality, league table payload.
Usage: python probe_seasons.py"""
import json

import five_min
from fotmob_mcp.client import FotMobClient

client = FotMobClient()


def collect_stats(o, out):
    if isinstance(o, dict):
        v = o.get("stats")
        if o.get("key") and isinstance(v, list) and len(v) == 2 \
                and not any(isinstance(x, (dict, list)) for x in v):
            out[o["key"]] = v
        for x in o.values():
            collect_stats(x, out)
    elif isinstance(o, list):
        for x in o:
            collect_stats(x, out)


for lid in (47, 48):
    print("\n" + "=" * 70)
    d = client.get_json("/api/data/leagues", {"id": str(lid), "season": "2025/2026"})
    print("league", lid, "selectedSeason:", (d.get("details") or {}).get("selectedSeason"))
    seasons = d.get("allAvailableSeasons")
    print("allAvailableSeasons:", json.dumps(seasons, ensure_ascii=False)[:300])

    ms = five_min.page_matches(d.get("fixtures") or {})
    fin = sorted((m for m in ms if m["finished"] and not m["cancelled"]), key=lambda m: m["date"])
    print("fixtures total:", len(ms), "finished:", len(fin))
    if not fin:
        continue
    print("date range:", fin[0]["date"].date(), "->", fin[-1]["date"].date())

    # table payload
    t = d.get("table")
    print("table type:", type(t).__name__)
    s = json.dumps(t, ensure_ascii=False)
    print("table sample:", s[:900], "..." if len(s) > 900 else "")

    # old-match data quality: first, middle, last finished
    for m in (fin[0], fin[len(fin) // 2], fin[-1]):
        try:
            md = client.get_json("/api/data/matchDetails", {"matchId": m["id"]}, use_cache=False)
        except Exception as e:
            print("  FAIL", m["id"], repr(e)[:100])
            continue
        c = md.get("content") or {}
        allst = {}
        collect_stats((((c.get("stats") or {}).get("Periods") or {}).get("All")), allst)
        shots = (c.get("shotmap") or {}).get("shots") or []
        print(f"  {m['date'].date()} {m['home']} {m['score']} {m['away']}: "
              f"stats={len(allst)} total_shots={allst.get('total_shots')} "
              f"xg={allst.get('expected_goals')} shotmap={len(shots)} "
              f"lineup={'Y' if c.get('lineup') else '-'} "
              f"events={'Y' if (c.get('matchFacts') or {}).get('events') else '-'}")

print("\nDONE")