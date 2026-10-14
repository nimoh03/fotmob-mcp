"""
grade.py - check the picks and tickets against what really happened

Run (after the matches have finished):
  python market_data.py options.csv     first, this downloads the finished matches
  python grade.py                       uses the newest candidates_*.csv and tickets_*.csv
"""
import csv
from collections import defaultdict
from datetime import datetime, timedelta

from market_data import load, CACHE, RESOLVE, OUT_DIR
from markets import SPECS

LABEL = {}
for _s in SPECS:
    for _side, _lab in _s.labels.items():
        LABEL[_lab] = (_s, _side)


def newest(pattern):
    files = sorted(OUT_DIR.glob(pattern))
    return files[-1] if files else None


def goals_text(rec):
    return " ".join(f"{m}'{'H' if h else 'A'}" for m, h in rec["goals"])


def main():
    cand_path, tick_path = newest("candidates_*.csv"), newest("tickets_*.csv")
    if not cand_path:
        print("no candidates file: run select_best.py first")
        return
    cache, rc = load(CACHE), load(RESOLVE)
    idx = defaultdict(list)
    for rec in cache.values():
        idx[(rec["h"], rec["a"])].append(rec)

    def find(event_id, start_iso):
        res = rc.get(str(event_id))
        if not res:
            return None
        ko = datetime.fromisoformat(start_iso.replace("Z", "+00:00")) - timedelta(hours=1)
        for rec in idx.get((res["home_id"], res["away_id"]), []):
            if abs((datetime.fromisoformat(rec["d"]) - ko).total_seconds()) <= 86400:
                return rec
        return None

    graded = {}    # (match, market) -> (hit True/False/None, record)
    rows = []
    with open(cand_path, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            rec = find(r["eventId"], r["start"])
            spec_side = LABEL.get(r["market"])
            hit = spec_side[0].hit(rec, spec_side[1]) if rec and spec_side else None
            graded[(r["match"], r["market"])] = (hit, rec)
            rows.append((r, hit, rec))

    def show(hit):
        return {True: "WIN ", False: "LOSS", None: "??  "}[hit]

    if tick_path:
        print(f"TICKETS ({tick_path.name})\n")
        tickets = defaultdict(list)
        with open(tick_path, encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                tickets[r["ticket"]].append(r)
        for title, legs in tickets.items():
            res = [graded.get((l["match"], l["market"]), (None, None)) for l in legs]
            lost = sum(1 for h, _ in res if h is False)
            unk = sum(1 for h, _ in res if h is None)
            verdict = "LOST" if lost else ("WON" if not unk else "UNKNOWN (some results missing)")
            print(f"{title}: {verdict} ({len(legs) - lost - unk} of {len(legs)} legs won)")
            for l, (h, rec) in zip(legs, res):
                score = f"{rec['score'][0]}-{rec['score'][1]}" if rec else "no result"
                print(f"   {show(h)} {l['match']} | {l['market']} @{l['odds']} [{l['grade']}] | final {score}")
            print()

    print("EVERY LEG WE JUDGED, by market and grade")
    print(f"{'market':<26}{'grade':>6}{'legs':>6}{'wins':>6}{'rate':>7}")
    agg = defaultdict(lambda: [0, 0])
    for r, hit, _ in rows:
        if hit is None:
            continue
        for key in ((r["market"], r["grade"]), ("ALL LEGS", r["grade"]), ("BEST PICK PER MATCH", r["grade"]) if r["pick"] == "Y" else None):
            if key:
                agg[key][0] += 1
                agg[key][1] += 1 if hit else 0
    for (m, g), (n, k) in sorted(agg.items()):
        print(f"{m:<26}{g:>6}{n:>6}{k:>6}{k / n:>7.0%}")

    losses = [(r, rec) for r, hit, rec in rows if hit is False and r["grade"] == "A"]
    if losses:
        print(f"\nGRADE-A LEGS THAT LOST ({len(losses)}): what happened")
        for r, rec in losses[:25]:
            print(f"   {r['match']} | {r['market']} @{r['odds']} | final {rec['score'][0]}-{rec['score'][1]} | goals: {goals_text(rec)}")
            print(f"      we said: {r['story']}")
    print("\n?? means the result or the needed data (cards, goal times) is not in the cache yet.")


if __name__ == "__main__":
    main()