"""
select_best.py - STEP D (v3): for every match, pick the best market using YOUR filters

For every club match in options.csv:
  1. judges every market that is on the slate for that match (both teams, same-league games BEFORE kickoff only)
  2. blocks legs that fail the hard rules or miss 2+ filters, grades the rest A (all filters) or B (one missed)
  3. turns the model chance into an honest estimate using how the filters did on matches before this slate
  4. picks per match: a grade-A leg with the highest estimate (a B leg only if no A leg exists)

Run:  python select_best.py options.csv
      python select_best.py options.csv --floor 1.05     (minimum odds for one leg, default 1.05)
      python select_best.py options.csv --all            (print every pick, default prints the top 40)
"""
import csv
import sys
from datetime import datetime, timedelta

from engine import build_index, shared_comp, evaluate, collect
from market_data import read_options, is_international, load, CACHE, RESOLVE, OUT_DIR
from markets import SPECS

SHRINK = 10   # weight of the model chance against the past evidence when making the estimate


def main():
    args = sys.argv[1:]
    floor, skip = 1.05, set()
    if "--floor" in args:
        i = args.index("--floor")
        floor, skip = float(args[i + 1]), {i + 1}
    path = next((a for i, a in enumerate(args) if not a.startswith("--") and i not in skip), "options.csv")
    show_all = "--all" in args

    cache, rc = load(CACHE), load(RESOLVE)
    by_team = build_index(cache)
    if not by_team:
        print("teams_index.json is empty or missing: run  python market_data.py options.csv  first.")
        return
    rows = [r for r in read_options(path) if not is_international(r)]
    if not rows:
        print("no club matches in the csv")
        return
    until = min(r["kickoff"] for r in rows) - timedelta(hours=2)

    evid = {}
    print("EVIDENCE: how the filters did on matches played BEFORE this slate (hits / matches)")
    for s in SPECS:
        data = collect(s, cache, by_team, until)
        for side, items in data.items():
            parts = []
            for g in ("A", "B"):
                sel = [x for x in items if x[0] == g]
                n, k = len(sel), sum(x[2] for x in sel)
                evid[(s.slug, side, g)] = (n, k)
                parts.append(f"grade {g}: {k}/{n}" + (f" ({k / n:.0%})" if n else ""))
            print(f"  {s.labels[side]:<24} " + " | ".join(parts))
    print(f"\nodds floor for a leg: {floor}\n")

    results, blocked_rows = [], []
    for r in rows:
        info = {"r": r, "cands": [], "reason": "", "best": None}
        results.append(info)
        res = rc.get(str(r["eventId"]))
        if not res or not res.get("verified"):
            info["reason"] = "not found on FotMob"
            continue
        hid, aid = res["home_id"], res["away_id"]
        if hid not in by_team or aid not in by_team:
            info["reason"] = "not enough season data"
            continue
        if not shared_comp(by_team, hid, aid):
            info["reason"] = "teams play in different leagues: no comparable stats"
            continue
        before = r["kickoff"] - timedelta(hours=2)
        on_slate = judged = 0
        for s in SPECS:
            for side, col in s.cols.items():
                o = (r.get(col) or "").strip()
                try:
                    odds = float(o)
                except ValueError:
                    continue
                if odds <= 1.0:        # a locked market is written as 0
                    continue
                on_slate += 1
                team, opp = (hid, aid) if side == 0 else (aid, hid)
                f = evaluate(s, by_team, team, opp, side, before)
                if f is None:
                    continue
                judged += 1
                if f["grade"] is None:
                    blocked_rows.append([r["match"], r["tournament"], s.labels[side], odds, " ; ".join(f["fails"])])
                    continue
                n, k = evid.get((s.slug, side, f["grade"]), (0, 0))
                est = (k + SHRINK * f["p"]) / (n + SHRINK)
                info["cands"].append({"label": s.labels[side], "odds": odds, "grade": f["grade"], "est": est,
                                      "p": f["p"], "n": n, "detail": f["detail"], "story": f["story"],
                                      "fails": " ; ".join(f["fails"]), "pick": False})
        if on_slate == 0:
            info["reason"] = "none of the markets we can judge is on the slate for this match"
        elif judged == 0:
            info["reason"] = "not enough league data to judge the markets on the slate"

    picks, nopick = [], []
    for info in results:
        elig = [c for c in info["cands"] if c["odds"] >= floor]
        pool = [c for c in elig if c["grade"] == "A"] or elig
        if not pool:
            if not info["reason"]:
                info["reason"] = ("every market was blocked by the filters" if not info["cands"]
                                  else f"only legs below the {floor} odds floor")
            nopick.append(info)
            continue
        best = max(pool, key=lambda c: (c["est"], c["odds"]))
        best["pick"] = True
        info["best"] = best
        picks.append(info)
    picks.sort(key=lambda i: (0 if i["best"]["grade"] == "A" else 1, -i["best"]["est"], -i["best"]["odds"]))

    na = sum(1 for i in picks if i["best"]["grade"] == "A")
    print(f"PICKS: {len(picks)} ({na} grade A, {len(picks) - na} grade B) | NO PICK: {len(nopick)} "
          f"(blocked legs: {len(blocked_rows)})\n")
    for k, i in enumerate(picks, 1):
        if not (show_all or k <= 40):
            break
        r, b = i["r"], i["best"]
        print(f"{k}. [{b['grade']}] {b['est']:.0%} | {b['label']} @{b['odds']} | {r['match']} ({r['tournament']})")
        print(f"      story: {b['story']}")
        print(f"      why: {b['detail']}")
        if b["fails"]:
            print(f"      missed: {b['fails']}")
        others = sorted((c for c in i["cands"] if c is not b), key=lambda c: -c["est"])[:4]
        if others:
            print("      others: " + "; ".join(f"{c['label']} @{c['odds']} [{c['grade']}] {c['est']:.0%}" for c in others))
    if nopick:
        print(f"\nNO PICK ({len(nopick)}) - bottom of the list:")
        for i in nopick:
            print(f"   {i['r']['match']} ({i['r']['tournament']}): {i['reason']}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    p_c, p_b = OUT_DIR / f"candidates_{stamp}.csv", OUT_DIR / f"blocked_{stamp}.csv"
    with open(p_c, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["eventId", "match", "tournament", "start", "market", "odds", "grade", "est_prob", "model_p",
                    "backed_by", "pick", "fails", "story", "detail"])
        for i in results:
            r = i["r"]
            for c in i["cands"]:
                w.writerow([r["eventId"], r["match"], r["tournament"], r["startTime"], c["label"], c["odds"], c["grade"],
                            f"{c['est']:.3f}", f"{c['p']:.3f}", c["n"], "Y" if c["pick"] else "N", c["fails"],
                            c["story"], c["detail"]])
    with open(p_b, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["match", "tournament", "market", "odds", "why it was blocked"])
        w.writerows(blocked_rows)
    print(f"\nsaved: {p_c}\n       {p_b}   (open this one to see why any leg was blocked)")


if __name__ == "__main__":
    main()