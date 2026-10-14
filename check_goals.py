"""
check_goals.py - STEP C4: Full Match Over 0.5 and 2nd Half Over 0.5 (team + opponent)

For both markets:  P(at least one goal) = 1 - (1 - P(home side scores)) x (1 - P(away side scores))
  P(a side scores) = average of: how often that team scores | how often the other team concedes
  (2nd half = goals from minute 46 on; full match = any goal)

Run:  python check_goals.py --backtest
      python check_goals.py options.csv        (add --all to print every pick)
"""
from engine import Spec, blend, ratio, run


def scored_full(rec, side):
    """Did `side` (0 home, 1 away) score at least once in the match?"""
    return rec["score"][side] >= 1


def scored_2h(rec, side):
    """Did `side` score in the 2nd half (minute 46 on)? Goals are [minute, 1 if home else 0]."""
    want = 1 if side == 0 else 0
    return any(m >= 46 and h == want for m, h in rec["goals"])


class GoalMarket(Spec):
    scored = None

    def bases(self, cache):
        n = k = 0
        for rec in cache.values():
            for side in (0, 1):
                n += 1
                k += 1 if self.scored(rec, side) else 0
        return {"b": k / n if n else 0.7}

    def score(self, tg, og, side, bases):
        b, f = bases["b"], self.scored
        # the team's own games: s = side it played on
        t_sc_all = [1 if f(r, s) else 0 for r, s in tg]
        t_sc_ven = [1 if f(r, s) else 0 for r, s in tg if s == side]
        t_cn_all = [1 if f(r, 1 - s) else 0 for r, s in tg]
        t_cn_ven = [1 if f(r, 1 - s) else 0 for r, s in tg if s == side]
        # the opponent's games: it plays on the other side today
        o_sc_all = [1 if f(r, s) else 0 for r, s in og]
        o_sc_ven = [1 if f(r, s) else 0 for r, s in og if s == 1 - side]
        o_cn_all = [1 if f(r, 1 - s) else 0 for r, s in og]
        o_cn_ven = [1 if f(r, 1 - s) else 0 for r, s in og if s == 1 - side]
        p_t = 0.5 * blend(t_sc_ven, t_sc_all, b) + 0.5 * blend(o_cn_ven, o_cn_all, b)
        p_o = 0.5 * blend(o_sc_ven, o_sc_all, b) + 0.5 * blend(t_cn_ven, t_cn_all, b)
        detail = (f"home side scores {p_t:.2f} (team scored {ratio(t_sc_all)}, opponent conceded {ratio(o_cn_all)}) | "
                  f"away side scores {p_o:.2f} (opponent scored {ratio(o_sc_all)}, team conceded {ratio(t_cn_all)})")
        return {"score": 1 - (1 - p_t) * (1 - p_o), "detail": detail}


class FullOver05(GoalMarket):
    name = "FULL MATCH OVER 0.5"
    slug = "full05"
    cols = {0: "Full Match Over 0.5"}
    labels = {0: "Full Match Over 0.5"}
    edges = [0.88, 0.92, 0.96]
    scored = staticmethod(scored_full)

    def hit(self, rec, side):
        return rec["score"][0] + rec["score"][1] >= 1


class SecondHalfOver05(GoalMarket):
    name = "2ND HALF OVER 0.5"
    slug = "h2_05"
    cols = {0: "2nd Half Over 0.5"}
    labels = {0: "2nd Half Over 0.5"}
    edges = [0.70, 0.80, 0.88]
    scored = staticmethod(scored_2h)

    def hit(self, rec, side):
        return any(m >= 46 for m, h in rec["goals"])


if __name__ == "__main__":
    for spec in (FullOver05(), SecondHalfOver05()):
        run(spec)
        print("\n" + "=" * 70 + "\n")