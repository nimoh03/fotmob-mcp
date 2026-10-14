"""
check_streak.py - "No 3+ in a row" markets (Home no 3+ / Away no 3+ / No team 3+)

ASSUMED RULE (confirm with the (i) text on SportyBet): a side "scores 3 in a row" when it scores 3 goals with no
goal from the other side in between, at any point in the match. "No" wins when that never happens.
If the real rule differs, only has_run() below changes.

P(a side does it) = average of: how often that team does it | how often the opponent's games let the other side do it
score = 1 - P (for 'No team': (1 - P home) x (1 - P away)). Matches with unreliable goal timelines are ignored.

Run:  python check_streak.py --backtest
      python check_streak.py options.csv
"""
from engine import Spec, blend, ratio, run, MIN_N


def longest_runs(goals):
    """Longest run of consecutive goals by [home, away]. goals = [[minute, 1 if home else 0], ...] sorted."""
    best = [0, 0]
    last, run_len = None, 0
    for _, h in goals:
        side = 0 if h == 1 else 1
        run_len = run_len + 1 if side == last else 1
        last = side
        if run_len > best[side]:
            best[side] = run_len
    return best


def has_run(rec, side, n=3):
    if not rec.get("sides_ok"):
        return None
    return longest_runs(rec["goals"])[side] >= n


def base_rate(cache):
    n = k = 0
    for rec in cache.values():
        if not rec.get("sides_ok"):
            continue
        for side in (0, 1):
            n += 1
            k += 1 if has_run(rec, side) else 0
    return {"b": k / n if n else 0.08}


def p_run(tg, og, side, base):
    """Chance the team (games tg, playing on `side` today) scores 3+ in a row against the opponent (games og)."""
    tg = [(r, s) for r, s in tg if r.get("sides_ok")]
    og = [(r, s) for r, s in og if r.get("sides_ok")]
    if len(tg) < MIN_N or len(og) < MIN_N:
        return None
    own_all = [1 if has_run(r, s) else 0 for r, s in tg]
    own_ven = [1 if has_run(r, s) else 0 for r, s in tg if s == side]
    alw_all = [1 if has_run(r, 1 - s) else 0 for r, s in og]
    alw_ven = [1 if has_run(r, 1 - s) else 0 for r, s in og if s == 1 - side]
    p = 0.5 * blend(own_ven, own_all, base) + 0.5 * blend(alw_ven, alw_all, base)
    return p, own_all, alw_all


class NoRunSide(Spec):
    name = "NO 3+ IN A ROW - HOME / AWAY"
    slug = "norun_side"
    cols = {0: "Home no 3+ in row", 1: "Away no 3+ in row"}
    labels = {0: "Home no 3+ in row", 1: "Away no 3+ in row"}
    edges = [0.90, 0.94, 0.97]

    def bases(self, cache):
        return base_rate(cache)

    def score(self, tg, og, side, bases):
        r = p_run(tg, og, side, bases["b"])
        if r is None:
            return None
        p, own, alw = r
        detail = (f"chance this side scores 3+ in a row {p:.2f}: team did it in {ratio(own)} games, "
                  f"opponent's games let the other side do it in {ratio(alw)}")
        return {"score": 1 - p, "detail": detail}

    def hit(self, rec, side):
        v = has_run(rec, side)
        return None if v is None else not v


class NoRunAny(Spec):
    name = "NO TEAM 3+ IN A ROW"
    slug = "norun_any"
    cols = {0: "No team 3+ in row"}
    labels = {0: "No team 3+ in row"}
    edges = [0.80, 0.86, 0.92]

    def bases(self, cache):
        return base_rate(cache)

    def score(self, tg, og, side, bases):
        b = bases["b"]
        h = p_run(tg, og, 0, b)      # home team: its games tg, opponent games og
        a = p_run(og, tg, 1, b)      # away team: its games og, opponent games tg
        if h is None or a is None:
            return None
        detail = (f"home does it {h[0]:.2f} (team {ratio(h[1])}, opponent allows {ratio(h[2])}) | "
                  f"away does it {a[0]:.2f} (team {ratio(a[1])}, opponent allows {ratio(a[2])})")
        return {"score": (1 - h[0]) * (1 - a[0]), "detail": detail}

    def hit(self, rec, side):
        v0, v1 = has_run(rec, 0), has_run(rec, 1)
        if v0 is None or v1 is None:
            return None
        return not (v0 or v1)


if __name__ == "__main__":
    for spec in (NoRunSide(), NoRunAny()):
        run(spec)
        print("\n" + "=" * 70 + "\n")