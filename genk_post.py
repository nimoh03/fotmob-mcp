"""genk_post.py - post-mortem of one failed 5-minute match.
Usage: python genk_post.py "Genk" --date=2026-10-10 [--n=5] [--id=TEAMID]
Shows the target match's goal minutes, then each team's last N finished matches before it.
Reuses five_min.py helpers and reports/five/goals_cache.json.
"""
import sys
from datetime import datetime, timedelta, timezone
from five_min import (candidates, team_page, page_matches, fetch_record,
                      load_cache, save_cache, EARLY)

args = [a for a in sys.argv[1:] if not a.startswith("--")]
opts = {a.split("=")[0]: a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--") and "=" in a}
name = args[0] if args else "Genk"
N = int(opts.get("--n", 5))
day = datetime.fromisoformat(opts["--date"]).replace(tzinfo=timezone.utc, hour=12)

cache = load_cache()

def rec_for(m):
    if m["id"] not in cache:
        r = fetch_record(m)
        if r is None:
            return None
        cache[m["id"]] = r
    return cache[m["id"]]

def describe(tid, m, rec):
    home = rec["h"] == str(tid)
    opp = m["away"] if home else m["home"]
    sc = rec.get("score")
    scs = f"{sc[0]}-{sc[1]}" if sc else "?"
    goals = []
    first_for = first_ag = None
    for minute, ish in rec["goals"]:
        mine = (ish == 1) == home
        goals.append(f"{minute}'{'+' if mine else '-'}")
        if mine and first_for is None:
            first_for = minute
        if not mine and first_ag is None:
            first_ag = minute
    early_for = first_for is not None and first_for <= EARLY
    early_ag = first_ag is not None and first_ag <= EARLY
    tag = ("EARLY GOAL FOR" if early_for else "") + (" EARLY GOAL AGAINST" if early_ag else "")
    ok = "" if rec.get("ok") else "  (goals do not add up to score, unreliable)"
    print(f"  {m['date']:%Y-%m-%d} {'H' if home else 'A'} vs {opp:24s} {scs:5s} goals: {' '.join(goals) or 'none':28s} {tag.strip()}{ok}")
    return early_for, early_ag, (first_for is not None and EARLY < first_for <= 10), (first_ag is not None and EARLY < first_ag <= 10)

def find_team(nm):
    if "--id" in opts and nm == name:
        return opts["--id"], nm
    c = candidates(nm)
    if not c:
        print(f"no FotMob team found for {nm}")
        sys.exit()
    return str(c[0]["id"]), c[0].get("name", nm)

def finished(tid):
    page = team_page(tid)
    return [m for m in (page_matches(page) if page else []) if m["finished"] and not m["cancelled"]]

tid, tname = find_team(name)
ms = finished(tid)
target = min((m for m in ms if abs((m["date"] - day).total_seconds()) <= 36 * 3600),
             key=lambda m: abs((m["date"] - day).total_seconds()), default=None)
if target is None:
    print(f"{tname} ({tid}): no finished match within 36h of {opts.get('--date')}. Matches seen:")
    for m in sorted(ms, key=lambda m: m["date"], reverse=True)[:8]:
        print("  ", f"{m['date']:%Y-%m-%d}", m["home"], "v", m["away"])
    sys.exit()

print(f"TARGET MATCH: {target['date']:%Y-%m-%d} {target['home']} v {target['away']}")
trec = rec_for(target)
if trec:
    sides = {trec["h"]: target["home"], trec["a"]: target["away"]}
    print("  goal minutes (minute, home=1):", trec["goals"], "| score:", trec.get("score"))
    first = trec["goals"][0][0] if trec["goals"] else None
    print(f"  first goal: {first}'  -> {'EARLY GOAL (draw lost)' if first is not None and first <= EARLY else 'no early goal'}")
opp_id = target["away_id"] if target["home_id"] == tid else target["home_id"]
opp_name = target["away"] if target["home_id"] == tid else target["home"]

for t_id, t_name in ((tid, tname), (opp_id, opp_name)):
    prior = sorted((m for m in finished(t_id) if m["date"] < target["date"]),
                   key=lambda m: m["date"], reverse=True)[:N]
    print(f"\n{t_name}: last {len(prior)} matches before the target")
    tot = [0, 0, 0, 0]
    for m in prior:
        r = rec_for(m)
        if not r:
            print("  could not read", m["id"])
            continue
        for i, v in enumerate(describe(t_id, m, r)):
            tot[i] += int(v)
    print(f"  early (<=5') scored {tot[0]}, conceded {tot[1]} | minutes 6-10: scored {tot[2]}, conceded {tot[3]}  (of {len(prior)} matches)")

save_cache(cache)
print("\nReading it: 5 matches is tiny. If neither team showed early goals and one still came, that is luck,")
print("not a flaw the Genk rule could have caught. If the signs were there (early goals or 6-10' goals), say so.")