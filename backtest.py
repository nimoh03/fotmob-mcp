"""Walk-forward backtest on a past season (no network).
Usage: python backtest.py
       python backtest.py --league=47 --w=5 --pct=0.25 --min-band=2 --min-share=0.8
For each match it uses ONLY that team's previous W matches at the same venue:
  steady = >= min-share of them within max(min-band, pct*median) of their median.
Then checks the next match. hit = actual <= line for unders, actual >= line for overs
(a bookmaker 'under 6.5' is the same as actual <= 6)."""
import json
import sys
from collections import Counter, defaultdict
from math import sqrt
from pathlib import Path
from statistics import median

argv = sys.argv[1:]


def opt(name, default):
    return next((a.split("=", 1)[1] for a in argv if a.startswith(f"--{name}=")), default)


FILE = opt("file", "shots_cache_2025-2026.json")
ONLY = opt("league", None)
W = int(opt("w", 5))
PCT = float(opt("pct", 0.25))
MIN_BAND = float(opt("min-band", 2))
MIN_SHARE = float(opt("min-share", 0.8))
CUSH = [0, 1, 2, 3]

data = json.loads(Path(FILE).read_text(encoding="utf-8"))


def num(x):
    try:
        return float(x)
    except Exception:
        return None


rows = [r for r in data.values() if r.get("has_stats")
        and num(r["shots"][0]) is not None and num(r["shots"][1]) is not None]
print(f"{FILE}: {len(rows)} matches | window W={W} | steady = >={MIN_SHARE * 100:.0f}% within "
      f"max({MIN_BAND:g}, {PCT * 100:.0f}% of median)")

# ---------------------------------------------------------------- shotmap vs stats gap
bad = 0
side_diff = Counter()
for r in rows:
    s = [num(r["shots"][0]), num(r["shots"][1])]
    if r.get("shotmap_n") != s[0] + s[1]:
        bad += 1
    sm = r.get("shot_mins") or []
    for side in (0, 1):
        side_diff[int(sum(1 for x in sm if x[0] == side) - s[side])] += 1
print(f"\nSHOTMAP vs STATS: {bad}/{len(rows)} matches differ in total")
print("  per team (shotmap count - stats shots):", dict(sorted(side_diff.items())))


def pct(x, n):
    return f"{100 * x / n:.0f}%" if n else "-"


leagues = sorted({str(r["parent"]) for r in rows})
if ONLY:
    leagues = [l for l in leagues if l == str(ONLY)]

for lg in leagues:
    lrows = [r for r in rows if str(r["parent"]) == lg]
    seqs = defaultdict(list)
    for r in lrows:
        f0, f1 = num(r["shots"][0]), num(r["shots"][1])
        seqs[(r["home_id"], "H")].append((str(r["date"]), f0, f1))
        seqs[(r["away_id"], "A")].append((str(r["date"]), f1, f0))
    print("\n" + "=" * 96)
    print(f"LEAGUE parent {lg}: {len(lrows)} matches")

    for title, idx in (("SHOTS THE TEAM TAKES", 1), ("SHOTS THE TEAM ALLOWS", 2)):
        acc = {g: {"n": 0, "inb": 0, "err": 0.0,
                   "under": {c: [0, 0.0] for c in CUSH}, "over": {c: [0, 0.0] for c in CUSH}}
               for g in ("steady", "other")}
        for key, seq in seqs.items():
            seq.sort()
            vals = [x[idx] for x in seq]
            for k in range(W, len(vals)):
                win, actual = vals[k - W:k], vals[k]
                med = median(win)
                band = max(MIN_BAND, PCT * med)
                steady = sum(1 for v in win if abs(v - med) <= band) / W >= MIN_SHARE
                a = acc["steady" if steady else "other"]
                a["n"] += 1
                a["inb"] += abs(actual - med) <= band
                a["err"] += abs(actual - med)
                for c in CUSH:
                    t = max(win) + c
                    a["under"][c][0] += actual <= t
                    a["under"][c][1] += t - med
                    t = min(win) - c
                    a["over"][c][0] += actual >= t
                    a["over"][c][1] += med - t

        print("\n" + "-" * 96)
        print(title)
        print(f"{'group':<8}{'n':<7}{'next match in band':<20}{'avg miss from median':<22}")
        for g in ("steady", "other"):
            a = acc[g]
            n = a["n"]
            if n:
                print(f"{g:<8}{n:<7}{pct(a['inb'], n):<20}{a['err'] / n:<22.1f}")
        s, o = acc["steady"], acc["other"]
        if s["n"] and o["n"]:
            p1, p2 = s["inb"] / s["n"], o["inb"] / o["n"]
            se = sqrt(p1 * (1 - p1) / s["n"] + p2 * (1 - p2) / o["n"])
            print(f"  gap (steady - other) in band: {100 * (p1 - p2):+.1f} points  (+-{196 * se:.1f}, rough; matches overlap)")

        for kind, label in (("under", "UNDER  line = highest of last W + cushion  ->  hit% (line above median)"),
                            ("over", "OVER   line = lowest of last W - cushion   ->  hit% (line below median)")):
            print("\n" + label)
            print(f"{'group':<8}" + "".join(f"{'cushion +' + str(c):<18}" for c in CUSH))
            for g in ("steady", "other"):
                a = acc[g]
                n = a["n"]
                if not n:
                    continue
                cells = "".join(f"{pct(a[kind][c][0], n) + ' (' + format(a[kind][c][1] / n, '.1f') + ')':<18}" for c in CUSH)
                print(f"{g:<8}{cells}")

print("\nHow to read: if 'steady' beats 'other' on next-match-in-band by a clear gap, the filter carries information.")
print("If the gap is small, steady-looking teams were just lucky windows. This tests hit rates only, not profit (no past odds).")
print("DONE")