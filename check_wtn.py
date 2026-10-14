"""
check_wtn.py - STEP C3: Win to Nil, both ways

  WinToNil    (Yes): the team wins and the opponent scores nothing
  WinToNilNo  (No) : anything else -> the side you actually use (back the weaker team NOT to win to nil)

P(win to nil) = P(clean sheet) x P(team scores)
  P(clean sheet) = average of: how often the team does not concede | how often the opponent fails to score
  P(team scores) = average of: how often the team scores          | how often the opponent concedes
  "No" score = 1 - P(win to nil)
Each rate blends home/away split with all league games and is pulled toward the league-wide rate.

Run:  python check_wtn.py --backtest
      python check_wtn.py options.csv        (add --all to print every pick)
"""
from engine import Spec, blend, ratio, run


class WinToNil(Spec):
    name = "WIN TO NIL"
    slug = "wtn"
    cols = {0: "Home Win to Nil", 1: "Away Win to Nil"}
    labels = {0: "Home Win to Nil", 1: "Away Win to Nil"}
    edges = [0.15, 0.25, 0.35, 0.45]

    def bases(self, cache):
        out = {}
        for side in (0, 1):
            n = cs = sc = 0
            for rec in cache.values():
                n += 1
                cs += 1 if rec["score"][1 - side] == 0 else 0
                sc += 1 if rec["score"][side] >= 1 else 0
            out[side] = {"cs": cs / n if n else 0.30, "sc": sc / n if n else 0.70}
        return out

    def score(self, tg, og, side, bases):
        b = bases[side]
        # team games: s = side the team played on, score[s] = its goals, score[1-s] = goals against
        t_cs_all = [1 if r["score"][1 - s] == 0 else 0 for r, s in tg]
        t_cs_ven = [1 if r["score"][1 - s] == 0 else 0 for r, s in tg if s == side]
        t_sc_all = [1 if r["score"][s] >= 1 else 0 for r, s in tg]
        t_sc_ven = [1 if r["score"][s] >= 1 else 0 for r, s in tg if s == side]
        # opponent games: it plays on the other side today
        o_bl_all = [1 if r["score"][s] == 0 else 0 for r, s in og]
        o_bl_ven = [1 if r["score"][s] == 0 else 0 for r, s in og if s == 1 - side]
        o_cn_all = [1 if r["score"][1 - s] >= 1 else 0 for r, s in og]
        o_cn_ven = [1 if r["score"][1 - s] >= 1 else 0 for r, s in og if s == 1 - side]
        cs = 0.5 * blend(t_cs_ven, t_cs_all, b["cs"]) + 0.5 * blend(o_bl_ven, o_bl_all, b["cs"])
        sc = 0.5 * blend(t_sc_ven, t_sc_all, b["sc"]) + 0.5 * blend(o_cn_ven, o_cn_all, b["sc"])
        detail = (f"clean sheet {cs:.2f}: team kept {ratio(t_cs_all)}, opponent failed to score {ratio(o_bl_all)} | "
                  f"scores {sc:.2f}: team scored in {ratio(t_sc_all)}, opponent conceded in {ratio(o_cn_all)}")
        return {"score": cs * sc, "detail": detail}

    def hit(self, rec, side):
        return rec["score"][side] >= 1 and rec["score"][1 - side] == 0


class WinToNilNo(WinToNil):
    """The 'No' side: wins whenever the team does NOT win to nil."""
    name = "WIN TO NIL - NO"
    slug = "wtn_no"
    cols = {0: "Home Win to Nil No", 1: "Away Win to Nil No"}
    labels = {0: "Home Win to Nil: No", 1: "Away Win to Nil: No"}
    edges = [0.65, 0.75, 0.85]

    def score(self, tg, og, side, bases):
        f = WinToNil.score(self, tg, og, side, bases)
        p_yes = f["score"]
        return {"score": 1 - p_yes, "detail": f"chance of a win to nil {p_yes:.2f} | {f['detail']}"}

    def hit(self, rec, side):
        return not WinToNil.hit(self, rec, side)


if __name__ == "__main__":
    for spec in (WinToNilNo(), WinToNil()):
        run(spec)
        print("\n" + "=" * 70 + "\n")