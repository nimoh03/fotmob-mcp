"""
build_tickets.py - STEP E (v2): build the 3 tickets from the newest candidates_*.csv

  SAFE      total odds about 2.0   (1.90 - 2.20)  grade-A legs only, at most 5 legs
  MAIN      total odds 4.5 - 5.0                  grade-A legs only, at most 8 legs
  LONGSHOT  total odds about 20 (change with --longshot), grade A and B legs, at most 14 legs (lenient, for fun)
Each ticket uses at most ONE leg per match and is the combination with the highest chance that every leg lands.

Run:  python build_tickets.py
      python build_tickets.py --floor 1.05 --longshot 25
      python build_tickets.py --file reports/markets/candidates_xxx.csv
"""
import csv
import math
import sys
from datetime import datetime

from market_data import OUT_DIR

GAP_WARN = 0.15   # warn when our estimate is this far above what the odds imply


def arg(name, default, cast=float):
    if name in sys.argv:
        return cast(sys.argv[sys.argv.index(name) + 1])
    return default


def load_legs(path, floor):
    legs = []
    with open(path, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            try:
                odds, est = float(r["odds"]), float(r["est_prob"])
            except (ValueError, KeyError):
                continue
            if odds < floor:
                continue
            legs.append({"match": r["match"], "tournament": r["tournament"], "market": r["market"], "odds": odds,
                         "est": est, "grade": r["grade"], "n": r["backed_by"], "fails": r["fails"],
                         "story": r["story"], "detail": r["detail"], "time": r["start"][11:16]})
    return legs


def make_pool(legs, grades, min_prob, per_match=3, max_matches=80):
    """Per match keep the top legs by estimate plus its highest-odds leg (so odds can be built up)."""
    by = {}
    for l in legs:
        if l["grade"] in grades and l["est"] >= min_prob:
            by.setdefault(l["match"], []).append(l)
    pool = {}
    for m, ls in by.items():
        ls.sort(key=lambda l: (-l["est"], -l["odds"]))
        keep = ls[:per_match]
        top = max(ls, key=lambda l: l["odds"])
        if top not in keep:
            keep.append(top)
        pool[m] = keep
    return dict(sorted(pool.items(), key=lambda kv: -kv[1][0]["est"])[:max_matches])


def best_ticket(pool, lo, hi, max_legs):
    """Highest chance of all legs landing with total odds in [lo, hi], one leg per match."""
    states = {0: (1.0, 1.0, [])}   # odds bucket -> (chance, odds, legs)
    for _match, legs in pool.items():
        new = dict(states)
        for p, o, chosen in states.values():
            if len(chosen) >= max_legs:
                continue
            for leg in legs:
                o2 = o * leg["odds"]
                if o2 > hi:
                    continue
                p2 = p * leg["est"]
                k2 = round(math.log(o2) / 0.005)
                cur = new.get(k2)
                if cur is None or p2 > cur[0]:
                    new[k2] = (p2, o2, chosen + [leg])
        states = new
    ok = [s for s in states.values() if lo <= s[1] <= hi]
    return max(ok, key=lambda s: s[0]) if ok else None


def main():
    floor = arg("--floor", 1.05)
    longshot = arg("--longshot", 20.0)
    path = arg("--file", None, str)
    if path is None:
        files = sorted(OUT_DIR.glob("candidates_*.csv"))
        if not files:
            print("no candidates file found: run  python select_best.py options.csv  first.")
            return
        path = str(files[-1])
    legs = load_legs(path, floor)
    print(f"candidate file: {path}")
    print(f"usable legs (odds >= {floor}): {len(legs)} | grade A: {sum(1 for l in legs if l['grade'] == 'A')} | "
          f"grade B: {sum(1 for l in legs if l['grade'] == 'B')}\n")

    specs = [
        ("SAFE (about 2.0 odds, grade A legs only)", 1.90, 2.20, {"A"}, 0.80, 5),
        ("MAIN (4.5 - 5.0 odds, grade A legs only)", 4.50, 5.00, {"A"}, 0.78, 8),
        (f"LONGSHOT (about {longshot:g} odds, for fun, grade A and B legs)", longshot * 0.75, longshot * 1.25, {"A", "B"}, 0.55, 14),
    ]
    lines, csv_rows = [], []
    for title, lo, hi, grades, min_prob, max_legs in specs:
        res = best_ticket(make_pool(legs, grades, min_prob), lo, hi, max_legs)
        lines.append("=" * 74)
        if res is None:
            lines.append(f"{title}: not enough strong legs to build this ticket inside the odds range")
            lines.append("")
            continue
        p, o, chosen = res
        chosen = sorted(chosen, key=lambda l: l["time"])
        cautious = 1.0
        for l in chosen:
            cautious *= (l["est"] + 1 / l["odds"]) / 2
        lines.append(title)
        lines.append(f"total odds {o:.2f} | chance every leg lands: our estimate {p:.0%}, cautious (halfway to the bookmaker) {cautious:.0%} | {len(chosen)} legs")
        lines.append("=" * 74)
        for i, l in enumerate(chosen, 1):
            implied = 1 / l["odds"]
            flag = "  (!) our estimate is far above the price" if l["est"] - implied > GAP_WARN else ""
            lines.append(f"{i}. {l['time']} | {l['match']} ({l['tournament']})")
            lines.append(f"   {l['market']} @{l['odds']} [grade {l['grade']}] | our estimate {l['est']:.0%} | price implies {implied:.0%}{flag}")
            lines.append(f"   story: {l['story']}")
            lines.append(f"   why: {l['detail']}")
            if l["fails"]:
                lines.append(f"   missed: {l['fails']}")
            csv_rows.append([title, i, l["time"], l["match"], l["tournament"], l["market"], l["odds"], l["grade"],
                             f"{l['est']:.3f}", f"{implied:.3f}", l["story"], l["detail"]])
        lines.append("")
    lines.append("Kick-off times are Nigeria time. Estimates come from past matches and are likely a little optimistic.")
    text = "\n".join(lines)
    print(text)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    p_txt, p_csv = OUT_DIR / f"tickets_{stamp}.txt", OUT_DIR / f"tickets_{stamp}.csv"
    p_txt.write_text(text, encoding="utf-8")
    with open(p_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ticket", "leg", "kickoff_wat", "match", "tournament", "market", "odds", "grade",
                    "our_estimate", "price_implies", "story", "why"])
        w.writerows(csv_rows)
    print(f"\nsaved: {p_txt}\n       {p_csv}")


if __name__ == "__main__":
    main()