"""
check_cards.py - Home / Away team cards Over 0.5 (team + opponent)

score = average of: how often the team gets 1+ card | how often the opponent's games saw the OTHER side get 1+ card
(each blends home/away split with all league games). Matches with no card data in FotMob are ignored.

Run:  python check_cards.py --backtest
      python check_cards.py options.csv
"""
from engine import Spec, blend, ratio, run, MIN_N


class TeamCards(Spec):
    name = "TEAM CARDS OVER 0.5"
    slug = "cards"
    cols = {0: "Home Card Over 0.5", 1: "Away Card Over 0.5"}
    labels = {0: "Home Card Over 0.5", 1: "Away Card Over 0.5"}
    edges = [0.70, 0.80, 0.90]

    def bases(self, cache):
        n = k = 0
        for rec in cache.values():
            c = rec.get("cards")
            if c is None:
                continue
            for x in c:
                n += 1
                k += 1 if x >= 1 else 0
        return {"b": k / n if n else 0.7}

    def score(self, tg, og, side, bases):
        b = bases["b"]
        tg = [(r, s) for r, s in tg if r.get("cards") is not None]
        og = [(r, s) for r, s in og if r.get("cards") is not None]
        if len(tg) < MIN_N or len(og) < MIN_N:
            return None
        own_all = [1 if r["cards"][s] >= 1 else 0 for r, s in tg]
        own_ven = [1 if r["cards"][s] >= 1 else 0 for r, s in tg if s == side]
        ind_all = [1 if r["cards"][1 - s] >= 1 else 0 for r, s in og]          # card for the side facing the opponent
        ind_ven = [1 if r["cards"][1 - s] >= 1 else 0 for r, s in og if s == 1 - side]
        t_rate, o_rate = blend(own_ven, own_all, b), blend(ind_ven, ind_all, b)
        last6 = [r["cards"][s] for r, s in tg][-6:]
        detail = (f"team got 1+ card in {ratio(own_all)} games (last 6 card counts: {last6}) | "
                  f"opponent's games: the other side got 1+ card in {ratio(ind_all)}")
        return {"score": 0.5 * t_rate + 0.5 * o_rate, "detail": detail}

    def hit(self, rec, side):
        c = rec.get("cards")
        return None if c is None else c[side] >= 1


if __name__ == "__main__":
    run(TeamCards())