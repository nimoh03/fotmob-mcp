import json
import sys
from collections import Counter

from fotmob_mcp.client import FotMobClient
from nts_opponent import fetch_stat, load_league, stat_urls
from nts_resolve import resolve

client = FotMobClient()
entry = sys.argv[1] if len(sys.argv) > 1 else "Bruno Leyes | Tigre"

status, pool = resolve(entry)
if not pool:
    sys.exit(f"could not resolve {entry}")
pid = str(pool[0]["id"])
d = client.get_json("/api/data/playerData", {"id": pid, "includeMarketValues": "true"})
team = d.get("primaryTeam") or {}
club_id = team.get("teamId")
main = d.get("mainLeague") or {}
print("player:", d.get("name"), "|", team.get("teamName"), "| id", pid,
      "| league", main.get("leagueName"), main.get("leagueId"))

# ---------- 1. role: where has he played? ----------
print("\n--- positions seen (label, appearances) ---")
for p in (d.get("positionDescription") or {}).get("positions") or []:
    print("  ", (p.get("strPos") or {}).get("label"), p.get("occurences"),
          "| main" if p.get("isMainPosition") else "")

rows = [m for m in (d.get("recentMatches") or [])
        if m.get("teamId") == club_id and m.get("playedInMatch")]
print("\nlineupPositionId in his recent club games (newest first):")
print("  ", [m.get("lineupPositionId") for m in rows[:20]])
print("  counts:", dict(Counter(m.get("lineupPositionId") for m in rows)))


def card(fss):
    return {it.get("title"): it.get("statValue")
            for it in (fss.get("topStatCard") or {}).get("items") or []}


# ---------- 2. last season ----------
seasons = d.get("statSeasons") or []
print(f"\n--- statSeasons: {len(seasons)} seasons listed. First three: ---")
for s in seasons[:3]:
    print(" ", json.dumps(s, ensure_ascii=False)[:600])

cands = []


def walk(o):
    if isinstance(o, dict):
        for k, v in o.items():
            if isinstance(v, (str, int)) and any(w in k.lower() for w in ("season", "entry")):
                cands.append((k, str(v)))
            walk(v)
    elif isinstance(o, list):
        for v in o:
            walk(v)


if len(seasons) > 1:
    walk(seasons[1])
seen, ids = set(), []
for k, v in cands:
    if v not in seen and "/" not in v and len(v) >= 3:
        seen.add(v)
        ids.append((k, v))
print("\ncandidate season ids in the second entry:", ids[:8])
print("current season card:", card(d.get("firstSeasonStats") or {}))

attempts = []
for k, v in ids[:3]:
    attempts += [
        ("/api/data/playerStats", {"playerId": pid, "seasonId": v}),
        ("/api/data/playerData", {"id": pid, "seasonId": v}),
        ("/api/data/playerData", {"id": pid, "season": v}),
    ]
print(f"\ntrying {len(attempts)} ways to ask for an older season ...")
for path, params in attempts:
    try:
        data = client.get_json(path, params, use_cache=False)
    except Exception as e:
        print(f"  FAIL {path} {params}: {getattr(e, '__cause__', e)}")
        continue
    keys = list(data.keys())[:12] if isinstance(data, dict) else type(data).__name__
    fss = (data.get("firstSeasonStats") if isinstance(data, dict) else None) or {}
    print(f"  OK   {path} {params}")
    print(f"       keys: {keys}")
    print(f"       has firstSeasonStats: {bool(fss)} | card: {card(fss)}")

# ---------- 3. the club's last lineup and formation ----------
print("\n--- team page: last lineup and formation ---")
t = None
for params in ({"id": str(club_id)}, {"id": str(club_id), "ccode3": "NGA"}):
    try:
        t = client.get_json("/api/data/teams", params)
        break
    except Exception:
        continue
if t:
    lu = (t.get("overview") or {}).get("lastLineupStats") or {}
    print("formation:", lu.get("formation"), "| starters:", len(lu.get("starters") or []))
    for s in (lu.get("starters") or [])[:3]:
        print("  ", json.dumps(s, ensure_ascii=False)[:400])
else:
    print("could not load the team page")

# ---------- 4. the club's style against its league ----------
# True = FotMob already gives it per match, False = season total (we divide by matches played)
STYLE = {
    "possession_percentage_team": True,
    "goals_team_match": True,
    "ontarget_scoring_att_team": True,
    "accurate_cross_team": True,
    "accurate_long_balls_team": True,
    "expected_goals_team": False,
    "big_chance_team": False,
    "touches_in_opp_box_team": False,
    "corner_taken_team": False,
}
print("\n--- club style vs the league (rank 1 = most of it) ---")
try:
    urls = stat_urls(load_league(main.get("leagueId")))
except Exception as e:
    urls = {}
    print("league stats not available:", e)
for name, per_match in STYLE.items():
    if name not in urls:
        print(f"  {name}: not in this league")
        continue
    try:
        table = fetch_stat(urls[name])
    except Exception as e:
        print(f"  {name}: failed ({getattr(e, '__cause__', e)})")
        continue
    vals = {}
    for tid, r in table.items():
        v, mp = r.get("StatValue"), r.get("MatchesPlayed") or 0
        if v is None:
            continue
        if per_match:
            vals[tid] = v
        elif mp:
            vals[tid] = v / mp
    mine = vals.get(str(club_id))
    if mine is None:
        print(f"  {name}: club not found")
        continue
    rank = 1 + sum(1 for x in vals.values() if x > mine)
    print(f"  {name}: {mine:.2f} per game, rank {rank} of {len(vals)}")