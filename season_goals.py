"""season_goals.py - every cached match this season for given teams, with goal minutes.
Usage: python season_goals.py "Colo-Colo" "Coquimbo Unido" [--since=2026-01-01] [--before=2026-10-11]
Reads reports/five/goals_cache.json only (no network except the team-name lookup).
Opponents are shown as FotMob ids (the cache doesn't store names).
+ = goal by this team, - = goal against.
"""
import sys
from datetime import datetime, timezone
from five_min import candidates, load_cache, parse_dt

args = [a for a in sys.argv[1:] if not a.startswith("--")]
opts = {a.split("=")[0]: a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--") and "=" in a}
since = datetime.fromisoformat(opts.get("--since", "2026-01-01")).replace(tzinfo=timezone.utc)
before = datetime.fromisoformat(opts["--before"]).replace(tzinfo=timezone.utc) if "--before" in opts else None

cache = load_cache()
recs = [(parse_dt(r["d"]), r) for r in cache.values() if r.get("ok") and r.get("d")]

for name in args:
    c = candidates(name)
    if not c:
        print(f"no FotMob team found for {name}")
        continue
    tid, tname = str(c[0]["id"]), c[0].get("name", name)
    rows = sorted((d, r) for d, r in recs
                  if tid in (r["h"], r["a"]) and d >= since and (before is None or d < before))
    print("=" * 90)
    print(f"{tname} (id {tid}): {len(rows)} cached matches since {since:%Y-%m-%d}"
          + (f" before {before:%Y-%m-%d}" if before else ""))
    buckets = {"<=5": 0, "6-10": 0, "11-20": 0, "21-30": 0, "31+": 0, "no goal": 0}
    for d, r in rows:
        home = r["h"] == tid
        opp = r["a"] if home else r["h"]
        sc = r.get("score")
        scs = f"{sc[0]}-{sc[1]}" if sc else "?"
        goals = " ".join(f"{m}'{'+' if (ish == 1) == home else '-'}" for m, ish in r["goals"]) or "none"
        print(f"  {d:%Y-%m-%d} {'H' if home else 'A'} vs {opp:>8} {scs:5s} {goals}")
        f = r["goals"][0][0] if r["goals"] else None
        k = ("no goal" if f is None else "<=5" if f <= 5 else "6-10" if f <= 10
             else "11-20" if f <= 20 else "21-30" if f <= 30 else "31+")
        buckets[k] += 1
    n = len(rows)
    if n:
        print("  first goal of the match came in: " + " | ".join(f"{k} {v}" for k, v in buckets.items()))
        print(f"  early (<=5') {buckets['<=5']}/{n} | by 10' {buckets['<=5'] + buckets['6-10']}/{n} "
              f"| by 20' {buckets['<=5'] + buckets['6-10'] + buckets['11-20']}/{n}")
print("\nOnly matches already in the cache are shown. If a team looks short of matches, "
      "run five_min.py on a slate containing it, or the count is just what was cached.")cd /workspaces/fotmob-mcp && ls && echo "-----" && ls reports 2>/dev/null | head && echo "-----" && grep -n "def \|^import \|^from " probe_shots.py five_min.py 2>/dev/null | head -80