"""Walk-forward: how well can we predict a team's shots, and how far must a line sit
from the prediction to be safe?  Reads shots_cache_<season>.json (no network).
Usage: python calib.py [--file=shots_cache_2025-2026.json] [--k=4] [--min-n=4]
Models, using ONLY earlier matches in the same league:
  matchup  = venue baseline x team attack x opponent defence (shrunk by K)
  median5  = team's own median over its last 5 matches at the same venue
  league   = plain venue average (no team info)
Table: line = prediction + d (under) or prediction - d (over), hit rate and n."""
import json
import sys
from collections import defaultdict
from math import ceil, floor
from pathlib import Path
from statistics import mean, median

argv = sys.argv[1:]


def opt(name, default):
    return next((a.split("=", 1)[1] for a in argv if a.startswith(f"--{name}=")), default)


FILE = opt("file", "shots_cache_2025-2026.json")
K = float(opt("k", 4))
MIN_N = int(opt("min-n", 4))
MIN_LEAGUE = int(opt("min-league", 30))

data = json.loads(Path(FILE).read_text(encoding="utf-8"))


def num(x):
    try:
        return float(x)
    except Exception:
        return None


rows = sorted((r for r in data.values() if r.get("has_stats")
               and num(r["shots"][0]) is not None and num(r["shots"][1]) is not None),
              key=lambda r: str(r["date"]))
print(f"{FILE}: {len(rows)} matches | K={K:g} | min matches per team {MIN_N}")


def cell(hits, n):
    return f"{100 * hits / n:.0f}% ({n})" if n else "-"


for lg in sorted({str(r["parent"]) for r in rows}):
    L = [r for r in rows if str(r["parent"]) == lg]
    hist = defaultdict(list)
    sum_h = sum_a = 0.0
    cnt = 0
    preds = []
    for r in L:
        h, a = r["home_id"], r["away_id"]
        hs, as_ = num(r["shots"][0]), num(r["shots"][1])
        if cnt >= MIN_LEAGUE:
            mu_h, mu_a = sum_h / cnt, sum_a / cnt

            def prof(t):
                rec = hist[t]
                n = len(rec)
                if n < MIN_N:
                    return None
                rf = [sf / (mu_h if ih else mu_a) for ih, sf, sa in rec]
                ra = [sa / (mu_a if ih else mu_h) for ih, sf, sa in rec]
                return (sum(rf) + K) / (n + K), (sum(ra) + K) / (n + K)

            def last5(t, ih):
                v = [sf for ih2, sf, sa in hist[t] if ih2 == ih][-5:]
                return median(v) if len(v) >= 5 else None

            ph, pa = prof(h), prof(a)
            nh, na = last5(h, True), last5(a, False)
            if ph and pa and nh is not None and na is not None:
                preds.append(("H", mu_h * ph[0] * pa[1], nh, mu_h, hs))
                preds.append(("A", mu_a * pa[0] * ph[1], na, mu_a, as_))
        hist[h].append((True, hs, as_))
        hist[a].append((False, as_, hs))
        sum_h += hs
        sum_a += as_
        cnt += 1

    print("\n" + "=" * 90)
    print(f"LEAGUE parent {lg}: {len(L)} matches, {len(preds)} team-matches predicted")
    if not preds:
        continue
    print("average miss (shots), lower is better:")
    for name, i in (("matchup model", 1), ("own median, last 5", 2), ("league average", 3)):
        print(f"  {name:<20}{mean(abs(x[i] - x[4]) for x in preds):.2f}")

    print("\nUsing the matchup model's prediction p:")
    print(f"{'d':<4}{'UNDER p+d home':<18}{'UNDER p+d away':<18}{'OVER p-d home':<18}{'OVER p-d away':<18}")
    for d in range(0, 10):
        cells = []
        for venue in ("H", "A"):
            v = [x for x in preds if x[0] == venue]
            cells.append(cell(sum(1 for x in v if x[4] <= floor(x[1] + d)), len(v)))
        for venue in ("H", "A"):
            v = [x for x in preds if x[0] == venue and x[1] - d >= 3]
            cells.append(cell(sum(1 for x in v if x[4] >= ceil(x[1] - d)), len(v)))
        print(f"{d:<4}" + "".join(f"{c:<18}" for c in cells))

print("\nRead: 'UNDER p+d' = the team had at most p+d shots. A bookmaker under (p+d+0.5) wins the same way.")
print("Overs only counted when the line is at least 3 shots. Hit rates are what happened, not what the odds paid.")
print("DONE")