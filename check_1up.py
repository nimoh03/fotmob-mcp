"""
check_1up.py - Home or Draw 1UP / Draw or Away 1UP

ASSUMED RULE (confirm with the (i) text on SportyBet): the bet wins if the team does not lose at full time,
OR the team was ahead at any moment during the match (early payout). It only LOSES when the team loses
and was never in front. If the real rule differs, only led(), bet_lost() and opp_won_unled() below change.

score = average of: how often the team avoids that losing outcome | how often the opponent's games did NOT end with
the opponent winning while the other side never led. Uses the goal timeline (matches where it is unreliable are ignored).

Run:  python check_1up.py --backtest
      python check_1up.py options.csv
"""
from engine import Spec, blend, ratio, run, MIN_N


def led(goals, side):
    """Was `side` (0 home, 1 away) ahead at any moment? goals = [[minute, 1 if home else 0], ...]"""
    mine = opp = 0
    for _, h in goals:
        if (h == 1) == (side == 0):
            mine += 1
        else:
            opp += 1
        if mine > opp:
            return True
    return False


def bet_lost(rec, side):
    """The 1UP bet on `side` loses: the side lost the match and was never ahead."""
    return rec["score"][side] < rec["score"][1 - side] and not led(rec["goals"], side)


def opp_won_unled(rec, s):
    """Side `s` won the match while the other side was never ahead."""
    return rec["score"][s] > rec["score"][1 - s] and not led(rec["goals"], 1 - s)


class DoubleChance1UP(Spec):
    name = "DOUBLE CHANCE 1UP (assumed rule)"
    slug = "dc1up"
    cols = {0: "Home or Draw 1UP", 1: "Draw or Away 1UP"}
    labels = {0: "Home or Draw 1UP", 1: "Draw or Away 1UP"}
    edges = [0.70, 0.78, 0.86]

    def bases(self, cache):
        out = {}
        for side in (0, 1):
            n = k = 0
            for rec in cache.values():
                if not rec.get("sides_ok"):
                    continue
                n += 1
                k += 0 if bet_lost(rec, side) else 1
            out[side] = k / n if n else 0.8
        return out

    def score(self, tg, og, side, bases):
        base = bases[side]
        tg = [(r, s) for r, s in tg if r.get("sides_ok")]
        og = [(r, s) for r, s in og if r.get("sides_ok")]
        if len(tg) < MIN_N or len(og) < MIN_N:
            return None
        t_all = [0 if bet_lost(r, s) else 1 for r, s in tg]
        t_ven = [0 if bet_lost(r, s) else 1 for r, s in tg if s == side]
        o_all = [0 if opp_won_unled(r, s) else 1 for r, s in og]
        o_ven = [0 if opp_won_unled(r, s) else 1 for r, s in og if s == 1 - side]
        detail = (f"team avoided 'lost without ever leading' in {ratio(t_all)} games | "
                  f"opponent avoided 'won without ever trailing' in {ratio(o_all)} games")
        return {"score": 0.5 * blend(t_ven, t_all, base) + 0.5 * blend(o_ven, o_all, base), "detail": detail}

    def hit(self, rec, side):
        if not rec.get("sides_ok"):
            return None
        return not bet_lost(rec, side)


if __name__ == "__main__":
    run(DoubleChance1UP())