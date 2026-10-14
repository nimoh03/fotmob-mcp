"""Probe: what shot/event/lineup/odds data does FotMob give per match?
Usage:
  python probe_shots.py "Arsenal"
  python probe_shots.py "Arsenal" --n=5
Saves raw JSON per match in probe_raw/ so we never refetch to inspect.
"""
import json
import sys
from collections import Counter
from pathlib import Path

from fotmob_mcp.client import FotMobClient
from five_min import candidates, team_page, page_matches

client = FotMobClient()
args = [a for a in sys.argv[1:] if not a.startswith("--")]
N = int(next((a.split("=")[1] for a in sys.argv[1:] if a.startswith("--n=")), 5))
MAX_FETCH = 20  # cap on matches fetched while hunting for N non-friendlies

name = args[0]
OUT = Path("probe_raw")
OUT.mkdir(exist_ok=True)


def sg(d, *keys, default=None):
    """safe nested get"""
    for k in keys:
        if isinstance(d, dict):
            d = d.get(k)
        else:
            return default
        if d is None:
            return default
    return d


def collect_stats(o, out):
    """every {key, title, stats:[home, away]} entry anywhere in the stats block"""
    if isinstance(o, dict):
        vals = o.get("stats")
        if o.get("key") and isinstance(vals, list) and len(vals) == 2 \
                and not any(isinstance(x, (dict, list)) for x in vals):
            out[o["key"]] = (o.get("title"), vals)
        for v in o.values():
            collect_stats(v, out)
    elif isinstance(o, list):
        for v in o:
            collect_stats(v, out)


def find_odds(o, path="", out=None, depth=0):
    """paths of any key that smells like odds/betting"""
    if out is None:
        out = []
    if depth > 8 or len(out) >= 15:
        return out
    if isinstance(o, dict):
        for k, v in o.items():
            p = f"{path}.{k}" if path else k
            if any(s in str(k).lower() for s in ("odd", "bet", "bookmaker")):
                out.append(p)
            find_odds(v, p, out, depth + 1)
    elif isinstance(o, list):
        for i, v in enumerate(o[:3]):
            find_odds(v, f"{path}[{i}]", out, depth + 1)
    return out


# ---------------------------------------------------------------- team + match list
cands = candidates(name)
print("candidates:", [(c["id"], c.get("name")) for c in cands[:5]])
tid = str(cands[0]["id"])
print("using team id:", tid, cands[0].get("name"))
page = team_page(tid)

done = sorted((m for m in page_matches(page) if m["finished"] and not m["cancelled"]),
              key=lambda m: m["date"], reverse=True)
print(f"finished matches on team page: {len(done)}")

# ---------------------------------------------------------------- per-match probe
rows = []
kept = 0
fetched = 0
for m in done:
    if kept >= N or fetched >= MAX_FETCH:
        break
    fetched += 1
    mid = m["id"]
    try:
        data = client.get_json("/api/data/matchDetails", {"matchId": mid}, use_cache=False)
    except Exception as e:
        print(f"\n!! {mid} fetch failed: {e}")
        continue
    (OUT / f"{mid}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    gen = data.get("general") or {}
    league = gen.get("leagueName") or gen.get("parentLeagueName") or "?"
    if "friendl" in str(league).lower() or "friendl" in str(gen.get("parentLeagueName", "")).lower():
        print(f"\n-- skip {mid} {m['home']} v {m['away']} ({league}) friendly")
        continue
    kept += 1

    home_id = str(sg(gen, "homeTeam", "id", default=""))
    side = "H" if home_id == tid else "A"
    content = data.get("content") or {}

    print("\n" + "=" * 78)
    print(f"{m['date'].date()}  {m['home']} {m['score']} {m['away']}   [{league}]  team was {side}")
    print("content keys:", list(content.keys()))

    # ---- 1) team stats block
    stats_obj = content.get("stats") or {}
    print("stats keys:", list(stats_obj.keys()))
    periods = sg(stats_obj, "Periods", default={})
    print("stats periods:", list(periods.keys()) if isinstance(periods, dict) else periods)
    found = {}
    collect_stats(stats_obj, found)
    print(f"stat entries found: {len(found)}")
    for k in ("total_shots", "ShotsOnTarget", "ShotsOffTarget", "blocked_shots",
              "expected_goals", "shots_inside_box", "shots_outside_box",
              "big_chance", "BallPossesion", "yellow_cards", "red_cards"):
        if k in found:
            print(f"   {k:<22} {found[k][1]}")
    other = [k for k in found if any(s in k.lower() for s in ("shot", "xg", "expected", "chance"))]
    print("   all shot/xG-ish keys:", other)

    # ---- 2) shotmap
    shots = sg(content, "shotmap", "shots", default=None)
    has_shotmap = isinstance(shots, list) and len(shots) > 0
    shot_min = False
    if has_shotmap:
        by_team = Counter(str(s.get("teamId")) for s in shots)
        shot_min = all(s.get("min") is not None for s in shots)
        print(f"shotmap: {len(shots)} shots, by teamId={dict(by_team)}")
        print("   shot fields:", list(shots[0].keys()))
        print("   eventTypes:", dict(Counter(s.get("eventType") for s in shots)))
        print("   minute range:", min(s.get("min", 0) for s in shots), "-",
              max(s.get("min", 0) for s in shots))
        print("   has xG:", any(s.get("expectedGoals") is not None for s in shots))
    else:
        print("shotmap: NONE")

    # ---- 3) events (goals, cards, subs)
    evs = sg(content, "matchFacts", "events", "events", default=None)
    has_events = isinstance(evs, list) and len(evs) > 0
    has_cards = False
    if has_events:
        types = Counter(e.get("type") for e in evs)
        print("events:", dict(types))
        print("   event fields:", list(evs[0].keys()))
        cards = [e for e in evs if e.get("type") == "Card"]
        has_cards = len(cards) > 0
        for e in cards:
            print(f"   card: min {e.get('time')} {e.get('card')} isHome={e.get('isHome')}")
        goals = [e for e in evs if e.get("type") == "Goal"]
        print("   goals:", [(g.get("time"), g.get("isHome"), g.get("homeScore"), g.get("awayScore")) for g in goals])