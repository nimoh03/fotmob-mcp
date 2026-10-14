"""Matchup view: expected shots for Home v Away from this season's league games.
Usage: python matchup.py "Cardiff City" "Sheffield United" [--k=4]
Reads shots_cache.json only (no network). Both teams must be in the same league.
Not adjusted for who each team has played yet (schedule strength)."""
import json
import sys
from collections import Counter
from pathlib import Path
from statistics import mean, pstdev

argv = sys.argv[1:]
args = [a for a in argv if not a.startswith("--")]
K = float(next((a.split("=")[1] for a in argv if a.startswith("--k=")), 4))
if len(args) < 2:
    sys.exit('usage: python matchup.py "Home Team" "Away Team" [--k=4]')

cache = json.loads(Path("shots_cache.json").read_text(encoding="utf-8"))


def num(x):
    try:
        return float(x)
    except Exception:
        return None


names = {}
for r in cache.values():
    names[r["home_id"]] = r["home"]
    names[r["away_id"]] = r["away"]


def find(q):
    ql = q.lower()
    hits = [(i, n) for i, n in names.items() if n and ql in n.lower()]
    exact = [h for h in hits if h[1].lower() == ql]
    hits = exact or hits
    if not hits:
        sys.exit(f"no team matching {q!r} in cache")
    if len(hits) > 1:
        print(f"note: {q!r} matches {[h[1] for h in hits]}, using {hits[0][1]}")
    return hits[0]


hid, hname = find(args[0])
aid, aname = find(args[1])


def parents(tid):
    return Counter(r["parent"] for r in cache.values()
                   if r.get("has_stats") and tid in (r["home_id"], r["away_id"])
                   and "friendl" not in str(r["league"]).lower())


ph = parents(hid).most_common(1)
pa = parents(aid).most_common(1)
if not ph or not pa:
    sys.exit("no cached matches for one of the teams; run league_cache.py first")
if ph[0][0] != pa[0][0]:
    sys.exit(f"different leagues (parent {ph[0][0]} vs {pa[0][0]}); only same-league matchups supported")
P = ph[0][0]

rows = [r for r in cache.values()
        if r.get("has_stats") and r["parent"] == P
        and "friendl" not in str(r["league"]).lower()
        and num(r["shots"][0]) is not None and num(r["shots"][1]) is not None]
mu_h = mean(num(r["shots"][0]) for r in rows)
mu_a = mean(num(r["shots"][1]) for r in rows)
print(f"league parent {P}: {len(rows)} matches | baseline home {mu_h:.1f} away {mu_a:.1f} | K={K:g}")


def profile(tid):
    recs = []
    for r in rows:
        if r["home_id"] == tid:
            recs.append((True, num(r["shots"][0]), num(r["shots"][1])))
        elif r["away_id"] == tid:
            recs.append((False, num(r["shots"][1]), num(r["shots"][0])))
    rf = [sf / (mu_h if h else mu_a) for h, sf, sa in recs]
    ra = [sa / (mu_a if h else mu_h) for h, sf, sa in recs]
    n = len(recs)
    return {
        "n": n,
        "att": (sum(rf) + K) / (n + K), "att_raw": mean(rf) if rf else None,
        "dfn": (sum(ra) + K) / (n + K), "dfn_raw": mean(ra) if ra else None,
        "sf_home": [sf for h, sf, sa in recs if h], "sf_away": [sf for h, sf, sa in recs if not h],
        "sa_home": [sa for h, sf, sa in recs if h], "sa_away": [sa for h, sf, sa in recs if not h],
        "sf_all": [sf for h, sf, sa in recs],
    }


H, A = profile(hid), profile(aid)


def mm(v):
    return f"{mean(v):.1f}({len(v)})" if v else "-"


print(f"\n{'team':<24}{'n':<4}{'attack':<9}{'(raw)':<8}{'defence':<9}{'(raw)':<8}")
for nm, p in ((hname, H), (aname, A)):
    print(f"{str(nm)[:23]:<24}{p['n']:<4}{p['att']:<9.2f}{p['att_raw']:<8.2f}{p['dfn']:<9.2f}{p['dfn_raw']:<8.2f}")
print("attack >1 = shoots more than average | defence >1 = concedes more than average")

eh = mu_h * H["att"] * A["dfn"]
ea = mu_a * A["att"] * H["dfn"]
print("\n" + "=" * 70)
print(f"EXPECTED  {hname} (home): {eh:.1f} shots   |   {aname} (away): {ea:.1f} shots   |   total {eh + ea:.1f}")
print(f"  {hname} attack {H['att']:.2f} x {aname} defence {A['dfn']:.2f} x home baseline {mu_h:.1f}")
print(f"  {aname} attack {A['att']:.2f} x {hname} defence {H['dfn']:.2f} x away baseline {mu_a:.1f}")

print("\nRAW CHECK (mean, n in brackets)")
print(f"  {hname} shots for AT HOME   {mm(H['sf_home'])}   | {aname} shots allowed AWAY {mm(A['sa_away'])}")
print(f"  {aname} shots for AWAY      {mm(A['sf_away'])}   | {hname} shots allowed HOME {mm(H['sa_home'])}")
print(f"  shots-for spread (sd): {hname} {pstdev(H['sf_all']):.1f} | {aname} {pstdev(A['sf_all']):.1f}")
small = [nm for nm, p in ((hname, H), (aname, A)) if min(len(p['sf_home']), len(p['sf_away'])) < 5]
if small:
    print(f"  WARNING: venue split under 5 matches for {small}: treat as rough")
print("\nNot adjusted for strength of schedule yet.")
print("DONE")