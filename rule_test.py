"""rule_test.py - does 'goals before minute W in both teams' last 5 matches' predict a goal in the first 5'?
Walk-forward on reports/five/goals_cache.json (no network).
Usage: python rule_test.py [--n=5]
For each match: c = number of the last N matches (both teams combined) that had any goal
(scored or conceded) before minute W. Then compare the real early-goal rate by c.
"""
import json, sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

opts = {a.split("=")[0]: a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--") and "=" in a}
N = int(opts.get("--n", 5))
EARLY = 5
cache = json.loads(Path("reports/five/goals_cache.json").read_text(encoding="utf-8"))

def pdate(s):
    d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)

recs = sorted(((pdate(r["d"]), r) for r in cache.values() if r.get("ok") and r.get("d")), key=lambda x: x[0])
print(f"usable matches: {len(recs)}")

def run(W):
    hist = defaultdict(list)   # team -> first goal minute of each past match (999 = no goal)
    rows = []                  # (c, early goal?)
    for d, r in recs:
        first = r["goals"][0][0] if r["goals"] else 999
        hh, ha = hist[r["h"]][-N:], hist[r["a"]][-N:]
        if len(hh) == N and len(ha) == N:
            c = sum(1 for x in hh if x <= W) + sum(1 for x in ha if x <= W)
            rows.append((c, first <= EARLY))
        hist[r["h"]].append(first)
        hist[r["a"]].append(first)
    return rows

buckets = [("0 (clean)", 0, 0), ("1-2", 1, 2), ("3-4", 3, 4), ("5-6", 5, 6), ("7+", 7, 99)]
for W in (10, 15, 20, 30):
    rows = run(W)
    if not rows:
        print(f"\nW={W}: no testable matches"); continue
    base = sum(x[1] for x in rows) / len(rows)
    print(f"\nW = goal before minute {W} | testable {len(rows)} | overall early-goal rate {base:.1%}")
    print("  busy matches (of 10)   n    early-goal rate")
    for name, lo, hi in buckets:
        g = [x for x in rows if lo <= x[0] <= hi]
        if g:
            print(f"  {name:20s} {len(g):5d}   {sum(x[1] for x in g)/len(g):6.1%}")
print("\nGenk v Kortrijk scored 5 on this scale at W=30 (4 + 1).")
print("Real signal = early-goal rate clearly rising from 0 to 7+, with decent n in each row.")
print("Small n rows are noise. Rule only helps if clean games lose clearly less than the overall rate.")