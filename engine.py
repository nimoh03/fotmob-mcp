"""
engine.py - shared machinery for every market check   (v3)

A check (see markets.py) is a Spec with two methods:
  judge(tg, og, side) -> {'p': model chance, 'grade': 'A'/'B'/None, 'fails': [...], 'detail': str, 'story': str} or None
  hit(record, side)   -> True / False / None (None = this finished match cannot be judged)
The engine only lets a match through when both teams play in the same league, uses league games only, and
for every match uses only games played BEFORE it, so a backtest is honest.
"""
from collections import Counter
from datetime import datetime

from market_data import load, CACHE, TEAMS

MIN_N = 5   # minimum earlier league games for the team and for the opponent


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


def league_games(by_team, tid, comp, before):
    return [(r, s) for d, r, s in by_team.get(tid, []) if d < before and r.get("comp") == comp]


class Spec:
    name = ""
    slug = ""
    cols = {}      # side (0 home, 1 away) -> options.csv column
    labels = {}    # side -> label shown in reports

    def judge(self, tg, og, side):
        raise NotImplementedError

    def hit(self, rec, side):
        raise NotImplementedError


def evaluate(spec, by_team, team, opp, side, before):
    comp = shared_comp(by_team, team, opp)
    if not comp:
        return None
    tg = league_games(by_team, team, comp, before)
    og = league_games(by_team, opp, comp, before)
    if len(tg) < MIN_N or len(og) < MIN_N:
        return None
    f = spec.judge(tg, og, side)
    if f is None:
        return None
    f["comp"] = comp
    return f


def collect(spec, cache, by_team, until=None):
    """Replay every past league match: {side: [(grade, model chance, hit 1/0), ...]} using only games before each match."""
    out = {s: [] for s in spec.cols}
    for rec in cache.values():
        d = parse_d(rec["d"])
        if until is not None and d >= until:
            continue
        for side in spec.cols:
            team, opp = (rec["h"], rec["a"]) if side == 0 else (rec["a"], rec["h"])
            if team not in by_team or opp not in by_team:
                continue
            if rec.get("comp") != shared_comp(by_team, team, opp):
                continue   # only test league matches between teams of the same league
            f = evaluate(spec, by_team, team, opp, side, d)
            if f is None:
                continue
            h = spec.hit(rec, side)
            if h is None:
                continue
            out[side].append((f["grade"], f["p"], 1 if h else 0))
    return out


def backtest(spec, until=None):
    cache = load(CACHE)
    by_team = build_index(cache)
    if not by_team:
        print("teams_index.json is empty or missing: run  python market_data.py options.csv  first.")
        return
    data = collect(spec, cache, by_team, until)
    print(f"\n{spec.name}")
    for side in spec.cols:
        items = data[side]
        if not items:
            print(f"  {spec.labels[side]}: not enough history yet")
            continue
        print(f"  {spec.labels[side].upper()}")
        print(f"    {'':<38}{'matches':>8}{'hit rate':>10}{'model said':>12}")
        groups = (("every match we could score", None),
                  ("passed all filters (grade A)", "A"),
                  ("failed exactly one filter (grade B)", "B"),
                  ("blocked by the filters", "X"))
        for title, g in groups:
            sel = [x for x in items if g is None or x[0] == g or (g == "X" and x[0] is None)]
            if not sel:
                print(f"    {title:<38}{0:>8}")
                continue
            n, k = len(sel), sum(x[2] for x in sel)
            print(f"    {title:<38}{n:>8}{k / n:>10.0%}{sum(x[1] for x in sel) / n:>12.0%}")