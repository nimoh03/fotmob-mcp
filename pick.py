"""pick.py - rank team-shots lines from a SportyBet CSV against the matchup model.
Usage: python pick.py sportybet_2026-10-13.csv [--league=Championship] [--top=3]
       [--haircut=0.03] [--mind_under=3] [--mind_over=5]
Calls matchup.py per fixture (no network). Team-shots markets only.
"""
import csv, re, subprocess, sys
from collections import defaultdict

args = sys.argv[1:]
path = next(a for a in args if not a.startswith("--"))
def opt(n, d):
    for a in args:
        if a.startswith(f"--{n}="):
            return a.split("=", 1)[1]
    return d
LEAGUE = opt("league", "Championship")
TOP = int(opt("top", "3"))
HAIRCUT = float(opt("haircut", "0.03"))   # safety cut on calibrated hit rate
MIND_U = float(opt("mind_under", "3"))    # min distance for unders
MIND_O = float(opt("mind_over", "5"))     # min distance for overs (only d=5 measured)

ALIAS = {  # CSV name -> name for matchup.py
    "Wrexham AFC": "Wrexham", "Millwall FC": "Millwall",
    "Middlesbrough FC": "Middlesbrough", "Wolves": "Wolverhampton Wanderers",
}

# calibration from calib.py findings (last season, walk-forward). line = p + d
UNDER = [(0, .50), (3, .735), (5, .87), (7, .935), (9, .97)]
OVER = [(0, .50), (3, .72), (5, .86), (7, .88)]   # thin evidence, only d=5 measured

def interp(pts, d):
    if d <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if d <= x1:
            return y0 + (y1 - y0) * (d - x0) / (x1 - x0)
    return pts[-1][1]

def hit_rate(side, d):
    if side == "Under":
        return interp(UNDER, d) if d >= 0 else 1 - interp(OVER, -d)
    return interp(OVER, d) if d >= 0 else 1 - interp(UNDER, -d)

def expect(home, away):
    h = ALIAS.get(home, home); a = ALIAS.get(away, away)
    r = subprocess.run([sys.executable, "matchup.py", h, a], capture_output=True, text=True)
    m = re.search(r"EXPECTED\s+(.+?) \(home\): ([\d.]+) shots\s+\|\s+(.+?) \(away\): ([\d.]+) shots", r.stdout)
    if not m:
        return None, (r.stdout + r.stderr).strip().splitlines()[-1:] or ["no output"]
    warn = "WARNING" in r.stdout
    return (float(m.group(2)), float(m.group(4)), warn), None

fx = defaultdict(list)
with open(path, newline="", encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        if r["tournament"] != LEAGUE:
            continue
        if r["market"] not in ("Home Team Shots Over/Under", "Away Team Shots Over/Under"):
            continue
        fx[(r["match"], r["homeTeam"], r["awayTeam"], r["kickoff"])].append(r)

allpicks = []
for (match, home, away, ko), rows in fx.items():
    print("=" * 78)
    print(f"{match}   {ko}")
    exp, err = expect(home, away)
    if exp is None:
        print("  NO DATA: matchup.py could not resolve a team:", err)
        continue
    ph, pa, warn = exp
    print(f"  expected shots: {home} {ph}  |  {away} {pa}" + ("   (venue split <5 matches, rough)" if warn else ""))
    cands = []
    for r in rows:
        side_team = home if r["market"].startswith("Home") else away
        p = ph if r["market"].startswith("Home") else pa
        m = re.match(r"(Over|Under) ([\d.]+)", r["selection"])
        if not m:
            continue
        side, line = m.group(1), float(m.group(2))
        odds = float(r["odds"])
        d = (line - p) if side == "Under" else (p - line)
        hr = max(0.0, hit_rate(side, d) - HAIRCUT)
        edge = hr * odds - 1
        ok = (d >= MIND_U and side == "Under") or (d >= MIND_O and side == "Over" and line >= 3)
        cands.append((edge, side_team, side, line, odds, d, hr, ok))
    good = sorted([c for c in cands if c[7] and c[0] > 0], reverse=True)
    if not good:
        print("  NO BET (no line far enough from expectation with a price above fair)")
    for e, t, s, l, o, d, hr, _ in good[:TOP]:
        tag = "  UNPROVEN (over d<5 not measured)" if (s == "Over" and d < 5) else ""
        print(f"  {t:24s} {s} {l:<5} @ {o:<5} d={d:4.1f}  hit~{hr*100:4.1f}%  implied {100/o:4.1f}%  edge {e*100:+5.1f}%{tag}")
        allpicks.append((e, match, t, s, l, o, d, hr))

print("\n" + "=" * 78)
print("RANKED (all fixtures, top 10 by edge)")
for e, m, t, s, l, o, d, hr in sorted(allpicks, reverse=True)[:10]:
    tag = "  UNPROVEN" if (s == "Over" and d < 5) else ""
    print(f"  {e*100:+5.1f}%  {t} {s} {l} @ {o}  (d={d:.1f}, hit~{hr*100:.0f}%){tag}  [{m}]")
print("\nCaveats: hit rates come from last season's calibration, minus the haircut;")
print("this season's samples are ~9 matches per team, so expectations are rough.")
print("Unders fail when the team falls behind early or the opponent gets a red card.")
print("Overs are barely calibrated (only d=5 measured). Bookmaker settlement source may differ by 1-2 shots.")