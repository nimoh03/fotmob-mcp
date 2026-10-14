"""
check_dc.py - STEP C2: Home or Draw / Draw or Away (team + opponent)   (v2)

v2: a match is only scored when BOTH teams play in the same league (their most common competition),
and every rate is built from games in that league only. Cross-league ties (Champions League, cups between
different tiers, youth vs senior sides) are listed as 'not comparable' instead of being guessed.

Run:  python check_dc.py --backtest        does the score predict 'not lose'?
      python check_dc.py options.csv       score today's matches (only markets present in the csv)
      python check_dc.py options.csv --all print every pick (default prints the top 40)
"""
import csv
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from market_data import read_options, is_international, load, CACHE, RESOLVE, TEAMS, OUT_DIR

MIN_N = 5        # minimum earlier LEAGUE games for the team and for the opponent
PRIOR = 3        # pull toward the league-wide rate
VENUE_MIN = 3    # use home/away split only with at least this many games there


def parse_d(s):
    return datetime.fromisoformat(s)


def build_index(cache):
    """team id -> [(date, record, side)] using only teams with a complete season download."""
    by_team = {}
    for tid, ids in load(TEAMS).items():
        games = []
        for mid in ids:
            rec = cache.get(mid)
            if rec is None:
                continue
            side = 0 if rec["h"] == tid else 1
            games.append((parse_d(rec["d"]), rec, side))
        games.sort(key=lambda x: x[0])
        by_team[tid] = games
    return by_team


_hc = {}


def home_comp(by_team, tid):
    """The competition a team plays most often = its league."""
    if tid not in _hc:
        c = Counter(rec.get("comp") or "" for _, rec, _ in by_team.get(tid, []))
        c.pop("", None)
        _hc[tid] = c.most_common(1)[0][0] if c else ""
    return _hc[tid]


def shared_comp(by_team, a, b):
    ca, cb = home_comp(by_team, a), home_comp(by_team, b)
    return ca if ca and ca == cb else ""


def base_rates(cache):
    """League-wide P(home does not lose), P(away does not lose)."""
    n = h = a = 0
    for rec in cache.values():
        hs, as_ = rec["score"]
        n += 1
        h += 1 if hs >= as_ else 0
        a += 1 if as_ >= hs else 0
    return (h / n, a / n) if n else (0.70, 0.55)


def smooth(xs, base):
    return (sum(xs) + PRIOR * base) / (len(xs) + PRIOR)


def blend(venue, allg, base):
    r_all = smooth(allg, base)
    if len(venue) >= VENUE_MIN:
        return 0.5 * smooth(venue, base) + 0.5 * r_all
    return r_all


def features(by_team, team, opp, team_side, before, base):
    """Back `team` (playing on team_side: 0 home, 1 away) to not lose. Uses only league games before `before`."""
    comp = shared_comp(by_team, team, opp)
    if not comp:
        return None
    tg = [(r, s) for d, r, s in by_team.get(team, []) if d < before and r.get("comp") == comp]
    og = [(r, s) for d, r, s in by_team.get(opp, []) if d < before and r.get("comp") == comp]
    if len(tg) < MIN_N or len(og) < MIN_N:
        return None
    t_all = [1 if r["score"][s] >= r["score"][1 - s] else 0 for r, s in tg]
    t_ven = [1 if r["score"][s] >= r["score"][1 - s] else 0 for r, s in tg if s == team_side]
    o_all = [1 if r["score"][s] <= r["score"][1 - s] else 0 for r, s in og]          # opponent did NOT win
    o_ven = [1 if r["score"][s] <= r["score"][1 - s] else 0 for r, s in og if s == 1 - team_side]
    t_rate, o_rate = blend(t_ven, t_all, base), blend(o_ven, o_all, base)
    return {
        "comp": comp, "t_rate": t_rate, "o_rate": o_rate, "score": 0.5 * t_rate + 0.5 * o_rate,
        "t_all": t_all, "t_ven": t_ven, "o_all": o_all, "o_ven": o_ven,
        "gd_t": sum(r["score"][s] - r["score"][1 - s] for r, s in tg) / len(tg),
        "gd_o": sum(r["score"][s] - r["score"][1 - s] for r, s in og) / len(og),
    }


ORDER = ["below 0.60", "0.60-0.70", "0.70-0.80", "0.80-0.90", "0.90 and up"]


def bucket(s):
    if s < 0.60:
        return ORDER[0]
    if s < 0.70:
        return ORDER[1]
    if s < 0.80:
        return ORDER[2]
    if s < 0.90:
        return ORDER[3]
    return ORDER[4]


def backtest():
    cache = load(CACHE)
    by_team = build_index(cache)
    if not by_team:
        print("teams_index.json is empty or missing: run  python market_data.py options.csv  first (v3).")
        return
    bh, ba = base_rates(cache)
    stats = {0: defaultdict(lambda: [0, 0]), 1: defaultdict(lambda: [0, 0])}
    tot = {0: [0, 0], 1: [0, 0]}
    for rec in cache.values():
        d = parse_d(rec["d"])
        hs, as_ = rec["score"]
        for side in (0, 1):
            team, opp = (rec["h"], rec["a"]) if side == 0 else (rec["a"], rec["h"])
            if team not in by_team or opp not in by_team:
                continue
            if rec.get("comp") != shared_comp(by_team, team, opp):
                continue   # only test on league matches between teams of the same league
            f = features(by_team, team, opp, side, d, bh if side == 0 else ba)
            if not f:
                continue
            hit = (hs >= as_) if side == 0 else (as_ >= hs)
            b = bucket(f["score"])
            stats[side][b][0] += 1
            stats[side][b][1] += 1 if hit else 0
            tot[side][0] += 1
            tot[side][1] += 1 if hit else 0
    print(f"match records in cache: {len(cache)} | complete teams: {len(by_team)}")
    print(f"league-wide: home does not lose {bh:.0%} | away does not lose {ba:.0%}")
    for side, name in ((0, "HOME OR DRAW"), (1, "DRAW OR AWAY")):
        n, k = tot[side]
        if not n:
            print(f"\n{name}: not enough history yet")
            continue
        print(f"\n{name}  ({n} league matches scored using only earlier league games, overall hit rate {k / n:.0%})")
        print(f"{'score bucket':<14}{'matches':>8}{'hit rate':>10}")
        for b in ORDER:
            n2, k2 = stats[side][b]
            if n2:
                print(f"{b:<14}{n2:>8}{k2 / n2:>10.0%}")
    print("\nIf higher buckets do not hit more often than lower ones, the score adds nothing yet.")


def detail(f, side):
    tw, ow = ("home", "away") if side == 0 else ("away", "home")
    return (f"team unbeaten {sum(f['t_all'])}/{len(f['t_all'])} overall, {sum(f['t_ven'])}/{len(f['t_ven'])} {tw} | "
            f"opponent failed to win {sum(f['o_all'])}/{len(f['o_all'])} overall, {sum(f['o_ven'])}/{len(f['o_ven'])} {ow} | "
            f"goal diff per game: team {f['gd_t']:+.1f}, opponent {f['gd_o']:+.1f}")


def live(path, show_all):
    rows = [r for r in read_options(path) if not is_international(r)]
    cache, rc = load(CACHE), load(RESOLVE)
    by_team = build_index(cache)
    if not by_team:
        print("teams_index.json is empty or missing: run  python market_data.py options.csv  first (v3).")
        return
    bh, ba = base_rates(cache)
    before = datetime.now(timezone.utc) + timedelta(days=2)
    out, cross = [], []
    no_market = no_data = unresolved = 0
    for r in rows:
        res = rc.get(str(r["eventId"]))
        if not res or not res.get("verified"):
            unresolved += 1
            continue
        hid, aid = res["home_id"], res["away_id"]
        if hid not in by_team or aid not in by_team:
            no_data += 1
            continue
        if not shared_comp(by_team, hid, aid):
            cross.append((r, home_comp(by_team, hid), home_comp(by_team, aid)))
            continue
        for label, col, team, opp, side in (
            ("Home or Draw", "Home or Draw", hid, aid, 0),
            ("Draw or Away", "Draw or Away", aid, hid, 1),
        ):
            odds = (r.get(col) or "").strip()
            if not odds:
                no_market += 1
                continue
            f = features(by_team, team, opp, side, before, bh if side == 0 else ba)
            if not f:
                no_data += 1
                continue
            out.append((f["score"], label, r, odds, f, side))
    out.sort(key=lambda x: -x[0])
    print(f"scored: {len(out)} | different leagues (not comparable): {len(cross)} | market not on slate: {no_market} | "
          f"not enough league games: {no_data} | not resolved: {unresolved}\n")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path_out = OUT_DIR / f"dc_{datetime.now().strftime('%Y-%m-%d_%H%M')}.csv"
    with open(path_out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["score", "market", "match", "tournament", "league", "odds", "team_rate", "opp_rate", "detail"])
        for i, (score, label, r, odds, f, side) in enumerate(out):
            w.writerow([f"{score:.3f}", label, r["match"], r["tournament"], f["comp"], odds,
                        f"{f['t_rate']:.3f}", f"{f['o_rate']:.3f}", detail(f, side)])
            if show_all or i < 40:
                print(f"{score:.2f} | {label} | {r['match']} ({r['tournament']})")
                print(f"       {detail(f, side)}")
    if cross:
        print(f"\nNOT COMPARABLE - the two teams play in different leagues, so no stats comparison ({len(cross)}):")
        for r, ca, cb in cross:
            print(f"   {r['match']} ({r['tournament']}): {r['homeTeam']} plays in {ca or '?'}, "
                  f"{r['awayTeam']} plays in {cb or '?'}")
    print("\nsaved:", path_out)


if __name__ == "__main__":
    if "--backtest" in sys.argv:
        backtest()
    else:
        arg = next((a for a in sys.argv[1:] if not a.startswith("--")), "options.csv")
        live(arg, "--all" in sys.argv)