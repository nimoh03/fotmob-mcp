"""Cache a past season's matches into its own file (compact, resumable).
Usage: python season_cache.py 47
       python season_cache.py 48 --season=2025/2026
Writes shots_cache_<season>.json (e.g. shots_cache_2025-2026.json). Never touches shots_cache.json."""
import json
import sys
import time
from pathlib import Path

import five_min
from fotmob_mcp.client import FotMobClient

client = FotMobClient()
argv = sys.argv[1:]
args = [a for a in argv if not a.startswith("--")]
LID = int(args[0])
SEASON = next((a.split("=", 1)[1] for a in argv if a.startswith("--season=")), "2025/2026")
OUT = Path(f"shots_cache_{SEASON.replace('/', '-')}.json")
cache = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}


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


def period_stats(per, name):
    out = {}
    collect_stats((per or {}).get(name), out)
    return out


def compact(d):
    gen = d.get("general") or {}
    c = d.get("content") or {}
    per = (c.get("stats") or {}).get("Periods") or {}
    a, h1, h2 = period_stats(per, "All"), period_stats(per, "FirstHalf"), period_stats(per, "SecondHalf")
    g = lambda k, s: (s.get(k) or [None, None])
    hid = str((gen.get("homeTeam") or {}).get("id"))
    aid = str((gen.get("awayTeam") or {}).get("id"))
    shots = (c.get("shotmap") or {}).get("shots") or []
    shot_mins = [[0 if str(s.get("teamId")) == hid else 1, s.get("min"), s.get("minAdded")] for s in shots]
    evs = ((c.get("matchFacts") or {}).get("events") or {}).get("events") or []
    goals = [[e.get("time"), 0 if e.get("isHome") else 1, bool(e.get("ownGoal"))]
             for e in evs if e.get("type") == "Goal"]
    cards = [[e.get("time"), 0 if e.get("isHome") else 1, e.get("card")]
             for e in evs if e.get("type") == "Card" and e.get("card") in ("Red", "YellowRed")]
    return {
        "date": gen.get("matchTimeUTCDate"),
        "league": gen.get("leagueName"),
        "parent": gen.get("parentLeagueId"),
        "home_id": hid, "away_id": aid,
        "home": (gen.get("homeTeam") or {}).get("name"),
        "away": (gen.get("awayTeam") or {}).get("name"),
        "shots": g("total_shots", a), "sot": g("ShotsOnTarget", a),
        "blocked": g("blocked_shots", a), "xg": g("expected_goals", a),
        "red": g("red_cards", a),
        "shots_1h": g("total_shots", h1), "shots_2h": g("total_shots", h2),
        "shotmap_n": len(shots),
        "has_stats": bool(a),
        "shot_mins": shot_mins, "goals": goals, "red_cards": cards,
    }


league = client.get_json("/api/data/leagues", {"id": str(LID), "season": SEASON})
sel = (league.get("details") or {}).get("selectedSeason")
print("league", LID, "selectedSeason:", sel)
if sel != SEASON:
    sys.exit(f"WRONG SEASON returned ({sel} != {SEASON}); not fetching")

ms = [m for m in five_min.page_matches(league.get("fixtures") or {}) if m["finished"] and not m["cancelled"]]
todo = [m for m in ms if str(m["id"]) not in cache]
print(f"{len(ms)} finished | {len(todo)} to fetch | {len(ms) - len(todo)} cached -> {OUT}")

failed = []
t0 = time.time()
for i, m in enumerate(todo, 1):
    mid = str(m["id"])
    d = None
    for attempt in range(3):
        try:
            d = client.get_json("/api/data/matchDetails", {"matchId": mid}, use_cache=False)
            break
        except Exception:
            time.sleep(1 + attempt * 2)
    if d is None:
        failed.append(mid)
        continue
    cache[mid] = compact(d)
    if i % 20 == 0:
        OUT.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        print(f"  {i}/{len(todo)}  ({time.time() - t0:.0f}s)")
    time.sleep(0.2)
OUT.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

rows = [cache[str(m["id"])] for m in ms if str(m["id"]) in cache]
nostats = sum(1 for r in rows if not r["has_stats"])
mismatch = sum(1 for r in rows if r["has_stats"] and r["shots"][0] is not None
               and r["shotmap_n"] != sum(float(x) for x in r["shots"]))
print(f"\ncached {len(rows)} | failed {len(failed)} {failed[:5]} | no stats {nostats} | shotmap!=total {mismatch}")
print("DONE")