"""five_rule.py - replay the 'early goals in last 5 matches' tier rule on a finished slate.
Usage: python five_rule.py five_slate.csv [--old=reports/five/five_ranking_XXXX.csv] [--back=10] [--n=5]
Needs: the slate CSV, goals_cache.json (already built by five_min.py) and an old five_ranking_*.csv.
Network: only to find the teams on FotMob (same lookup five_min.py does).
"""
import csv, glob, sys
from five_min import read_slate, resolve_fixture, load_cache, actual_for, parse_dt

args = [a for a in sys.argv[1:] if not a.startswith("--")]
opts = {a.split("=")[0]: a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--") and "=" in a}
path = args[0] if args else "five_slate.csv"
N = int(opts.get("--n", 5))
BACK = int(opts.get("--back", 10))
old_path = opts.get("--old") or (sorted(glob.glob("reports/five/five_ranking_*.csv")) or [None])[-1]
if not old_path:
    sys.exit("no five_ranking_*.csv found: run five_min.py on this slate first (or pass --old=...)")
print("slate:", path, "| old ranking:", old_path, f"| last {N} matches, back-of-list goal <= {BACK}'")

cache = load_cache()
recs = []
for mid, r in cache.items():
    if r.get("ok") and r.get("d"):
        recs.append((parse_dt(r["d"]), r))

old_rank = {}
with open(old_path, newline="", encoding="utf-8") as f:
    for r in csv.DictReader(f):
        old_rank[(r["home"], r["away"])] = int(r["rank"])

def last_matches(tid, ko):
    rows = [(d, r) for d, r in recs if d < ko and tid in (r["h"], r["a"])]
    rows.sort(key=lambda x: x[0], reverse=True)
    return [[g[0] for g in r["goals"]] for d, r in rows[:N]]   # goal minutes (either side) per match

items, skipped = [], 0
for row in read_slate(path):
    key = (row["home"], row["away"])
    if key not in old_rank:
        skipped += 1
        continue
    try:
        res, why = resolve_fixture(row)
    except Exception as e:
        res = None
    if not res:
        skipped += 1
        continue
    fx = {**row, **res}
    hm, am = last_matches(fx["home_id"], fx["kickoff"]), last_matches(fx["away_id"], fx["kickoff"])
    allm = hm + am
    short = len(hm) < N or len(am) < N
    g30 = sum(1 for m in allm if any(x <= 30 for x in m))
    g20 = sum(1 for m in allm if any(x <= 20 for x in m))
    gback = sum(1 for m in allm if any(x <= BACK for x in m))
    if short:
        tier = 3
    elif g30 == 0:
        tier = 1
    elif gback == 0 and g20 <= 2:
        tier = 2
    else:
        tier = 3
    act = actual_for(fx, cache)
    won = None if act is None else (not act["early"])
    items.append({"fx": fx, "old": old_rank[key], "tier": tier, "g30": g30, "g20": g20,
                  "gback": gback, "short": short, "won": won, "odds": row["x"]})

print(f"matched {len(items)} fixtures to the old ranking ({skipped} skipped)\n")
new = sorted(items, key=lambda i: (i["tier"], i["old"]))

def tag(i):
    return "?" if i["won"] is None else ("won" if i["won"] else "LOST")

def show(title, lst):
    print(title)
    print(" #  old  tier  <=30 <=20 <=%d  draw   result  match" % BACK)
    for k, i in enumerate(lst, 1):
        fx = i["fx"]
        print(f"{k:>2}  {i['old']:>3}   {i['tier']}{'*' if i['short'] else ' '}    {i['g30']:>3}  {i['g20']:>3}  {i['gback']:>3}  "
              f"{i['odds']:<5}  {tag(i):6s}  {fx['home']} v {fx['away']}")
    print()

def record(name, lst):
    played = [i for i in lst if i["won"] is not None]
    if not played:
        print(f"  {name}: no played matches")
        return
    w = sum(i["won"] for i in played)
    pl = sum((i["odds"] - 1) if i["won"] else -1 for i in played)
    print(f"  {name}: won {w}/{len(played)} | flat 1-unit profit {pl:+.2f}")

old_top = sorted(items, key=lambda i: i["old"])[:15]
show("OLD TOP 15 (old model order)", old_top)
show("NEW TOP 15 (tiers first, then old order)  * = under %d matches of data" % N, new[:15])

print("tier sizes:", {t: sum(1 for i in items if i["tier"] == t) for t in (1, 2, 3)})
record("old top 15", old_top)
record("new top 15", new[:15])
record("tier 1 only", [i for i in items if i["tier"] == 1])
record("tier 2 only", [i for i in items if i["tier"] == 2])
record("tier 3 only", [i for i in items if i["tier"] == 3])
record("whole slate", items)
print("\nBoth top-15 lists come from one slate: a handful of games, so a difference of 1 or 2 results means nothing.")
print("The rule only counts if tiers 1-2 beat tier 3 over many slates, with prices.")