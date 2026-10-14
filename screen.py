"""Consistency screen: is a team's shot count STABLE at each venue, at ANY level?
Reads shots_cache.json (no network).
Usage:
  python screen.py --league=48                  (both sides, home/away separate)
  python screen.py --league=48 --side=against   (shots the team allows)
  python screen.py --league=48 --side=for --pass-only
Options:
  --side=for|against|both   default both
  --venue=each|all          default each (home and away judged separately)
  --pct=0.25 --min-band=2   a match is 'on pattern' if within max(min-band, pct*median) of the median
  --min-share=0.8           share of matches that must be on pattern
  --min-n=4                 minimum matches per row
  --pass-only               hide failing rows
'vs luck' = sd / sqrt(mean). About 1.0 is the normal random wobble of a shot count;
well under 1 is steadier than luck alone gives, over 1.3 is jumpy.
PASS = candidate only, untested on unseen matches."""
import json
import sys
from collections import Counter
from pathlib import Path
from statistics import mean, median, pstdev

argv = sys.argv[1:]


def opt(name, default):
    return next((a.split("=", 1)[1] for a in argv if a.startswith(f"--{name}=")), default)


SIDE = opt("side", "both")
VENUE = opt("venue", "each")
PCT = float(opt("pct", 0.25))
MIN_BAND = float(opt("min-band", 2))
MIN_SHARE = float(opt("min-share", 0.8))
MIN_N = int(opt("min-n", 4))
PASS_ONLY = "--pass-only" in argv

cache = json.loads(Path("shots_cache.json").read_text(encoding="utf-8"))


def num(x):
    try:
        return float(x)
    except Exception:
        return None


ok = [r for r in cache.values()
      if r.get("has_stats") and "friendl" not in str(r["league"]).lower()
      and num(r["shots"][0]) is not None and num(r["shots"][1]) is not None]
LEAGUE = opt("league", None)
if LEAGUE is None:
    LEAGUE = Counter(str(r["parent"]) for r in ok).most_common(1)[0][0]
ok = [r for r in ok if str(r["parent"]) == str(LEAGUE)]
venues = [("H", True), ("A", False)] if VENUE == "each" else [("ALL", None)]
print(f"league parent {LEAGUE}: {len(ok)} matches | on-pattern = within max({MIN_BAND:g}, {PCT * 100:.0f}% of median) "
      f"| need >= {MIN_SHARE * 100:.0f}% on pattern, n >= {MIN_N}")


def build(side):
    T = {}
    for r in ok:
        s = [num(r["shots"][0]), num(r["shots"][1])]
        for i in (0, 1):
            tid = r["home_id"] if i == 0 else r["away_id"]
            nm = r["home"] if i == 0 else r["away"]
            val = s[i] if side == "for" else s[1 - i]
            T.setdefault(tid, {"name": nm, "recs": []})["recs"].append((str(r["date"]), i == 0, val))
    out = []
    for tid, t in T.items():
        for label, flag in venues:
            recs = sorted(x for x in t["recs"] if flag is None or x[1] == flag)
            vals = [x[2] for x in recs]
            n = len(vals)
            if n == 0:
                continue
            med, mu, sd = median(vals), mean(vals), pstdev(vals)
            band = max(MIN_BAND, PCT * med)
            share = sum(1 for v in vals if abs(v - med) <= band) / n
            why = []
            if n < MIN_N:
                why.append("n")
            if share < MIN_SHARE:
                why.append("erratic")
            out.append({"name": t["name"], "v": label, "n": n, "med": med, "mean": mu, "sd": sd,
                        "band": band, "share": share, "luck": sd / max(mu, 0.1) ** 0.5,
                        "why": why, "series": [int(v) for v in vals]})
    out.sort(key=lambda r: (bool(r["why"]), -r["share"], r["luck"]))
    return out


for side in (["for", "against"] if SIDE == "both" else [SIDE]):
    rows = build(side)
    title = "SHOTS THE TEAM TAKES" if side == "for" else "SHOTS THE TEAM ALLOWS"
    print("\n" + "=" * 100)
    print(title)
    print(f"{'team':<24}{'v':<3}{'n':<3}{'level':<7}{'mean':<6}{'sd':<5}{'vs luck':<8}{'+-band':<8}{'on-pat':<8}{'result':<12}series (old->new)")
    shown = 0
    for r in rows:
        if PASS_ONLY and r["why"]:
            continue
        res = "PASS" if not r["why"] else "no:" + ",".join(r["why"])
        print(f"{str(r['name'])[:23]:<24}{r['v']:<3}{r['n']:<3}{r['med']:<7.1f}{r['mean']:<6.1f}{r['sd']:<5.1f}"
              f"{r['luck']:<8.2f}{r['band']:<8.1f}{str(round(r['share'] * 100)) + '%':<8}{res:<12}{r['series']}")
        shown += 1
    p = sum(1 for r in rows if not r["why"])
    print(f"{p} PASS of {len(rows)} rows")

print("\nPASS = steady so far, not proven. With 4-5 matches per venue some rows pass by luck.")
print("DONE")