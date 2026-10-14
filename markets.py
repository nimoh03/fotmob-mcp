"""
markets.py - every market check with your filters built in.   (all numbers you may want to tune are in T)

Run:  python markets.py          backtest: how each market's filters did on past matches (needs market_cache.json)

Each check returns a grade: A = passed every filter, B = missed exactly one, None = blocked.
"""
import math

from engine import Spec, MIN_N
from model import profile, lam_pair, grid, p_run, p_1up_lose, grade, runs, mean

T = {
    # double chance and 1UP: the team we back must be clearly stronger
    "dc_win_min": 0.38, "dc_win": 0.50, "dc_draw": 0.30, "dc_loss": 0.25, "dc_fts": 0.25,
    "dc_opp_win": 0.40, "dc_form": 1.4,
    # win to nil NO: the named side must be clearly weaker, the other side must reliably score
    "wtn_win_hard": 0.40, "wtn_win": 0.30, "wtn_s_fts": 0.20, "wtn_s_loss": 0.25, "wtn_s_lam": 1.4,
    "wtn_w_cs": 0.30, "wtn_w_ga": 1.1, "wtn_w_shots": 10,
    # team cards
    "card_rate": 0.85, "card_drawn_rate": 0.80, "card_lam": 1.7, "card_gf": 1.0, "card_l5": 4,
    # 2nd half over 0.5
    "h2_total": 2.4, "h2_dull": 0.2, "h2_lam": 1.3, "h2_one": 0.80, "h2_both": 0.60,
    # full match over 0.5
    "o05_total": 2.2, "o05_fts": 0.35, "o05_nil": 0.15,
    # no 3+ goals in a row
    "run_hard": 0.25, "run_any_hard": 0.40, "run_p": 0.12, "run_ga": 1.2, "run_fts": 0.30,
    "run_opp2": 0.40, "run_gf": 1.7, "run_g3": 0.20, "run_l5ga": 1.4,
}


def pct(x):
    return f"{x:.0%}"


def led(goals, side):
    """Was `side` (0 home, 1 away) ahead at any moment?"""
    mine = opp = 0
    for _, h in goals:
        if (h == 1) == (side == 0):
            mine += 1
        else:
            opp += 1
        if mine > opp:
            return True
    return False


# ---------------------------------------------------------------- Home or Draw / Draw or Away
class DoubleChance(Spec):
    name = "DOUBLE CHANCE (Home or Draw / Draw or Away)"
    slug = "dc"
    cols = {0: "Home or Draw", 1: "Draw or Away"}
    labels = {0: "Home or Draw", 1: "Draw or Away"}

    def prob(self, lt, lo, pw, pd):
        return pw + pd

    def judge(self, tg, og, side):
        Pt, Po = profile(tg, side), profile(og, 1 - side)
        lt, lo = lam_pair(Pt, Po)
        pw, pd, pl = grid(lt, lo)
        A, O, F = Pt["all"], Po["all"], Pt["l5"]
        rules = [
            (f"team is stronger on goal difference ({A['gd']:+.1f} vs {O['gd']:+.1f} per game)", A["gd"] > O["gd"], True),
            (f"model win chance at least {pct(T['dc_win_min'])} (is {pct(pw)})", pw >= T["dc_win_min"], True),
            (f"backed to win: model win chance at least {pct(T['dc_win'])} (is {pct(pw)})", pw >= T["dc_win"], False),
            (f"not a draw team: draws at most {pct(T['dc_draw'])} of games (is {pct(A['draw'])})", A["draw"] <= T["dc_draw"], False),
            (f"rarely loses: at most {pct(T['dc_loss'])} (is {pct(A['loss'])})", A["loss"] <= T["dc_loss"], False),
            (f"fights: scores in at least {pct(1 - T['dc_fts'])} of games (is {pct(1 - A['fts'])})", A["fts"] <= T["dc_fts"], False),
            (f"opponent wins at most {pct(T['dc_opp_win'])} of its games (is {pct(O['win'])})", O["win"] <= T["dc_opp_win"], False),
            (f"in form: last-5 points per game at least {T['dc_form']} (is {F['ppg']:.1f})", F["ppg"] >= T["dc_form"], False),
        ]
        g, fails = grade(rules)
        detail = (f"team: wins {pct(A['win'])}, draws {pct(A['draw'])}, loses {pct(A['loss'])}, goals {A['gf']:.1f}-{A['ga']:.1f} per game, "
                  f"last 5 {F['ppg']:.1f} pts/game | opponent: wins {pct(O['win'])}, goals {O['gf']:.1f}-{O['ga']:.1f} per game")
        story = f"expected goals {lt:.1f}-{lo:.1f}: team wins {pct(pw)}, draw {pct(pd)}, team loses {pct(pl)}"
        return {"p": self.prob(lt, lo, pw, pd), "grade": g, "fails": fails, "detail": detail, "story": story}

    def hit(self, rec, side):
        hs, as_ = rec["score"]
        return hs >= as_ if side == 0 else as_ >= hs


class DoubleChance1UP(DoubleChance):
    """Same filters (it is the safer cousin). ASSUMED RULE: wins if the team does not lose OR was ahead at any moment."""
    name = "DOUBLE CHANCE 1UP (assumed rule)"
    slug = "dc1up"
    cols = {0: "Home or Draw 1UP", 1: "Draw or Away 1UP"}
    labels = {0: "Home or Draw 1UP", 1: "Draw or Away 1UP"}

    def prob(self, lt, lo, pw, pd):
        return 1 - p_1up_lose(lt, lo)

    def hit(self, rec, side):
        if not rec.get("sides_ok"):
            return None
        lost = rec["score"][side] < rec["score"][1 - side] and not led(rec["goals"], side)
        return not lost


# ---------------------------------------------------------------- Win to Nil: No
class WinToNilNo(Spec):
    """We back NO on the named side (the clearly weaker one). Home Win to Nil No = home must not win to nil."""
    name = "WIN TO NIL: NO (weaker named side)"
    slug = "wtn_no"
    cols = {0: "Home Win to Nil No", 1: "Away Win to Nil No"}
    labels = {0: "Home Win to Nil: No", 1: "Away Win to Nil: No"}

    def judge(self, tg, og, side):
        Pw, Ps = profile(tg, side), profile(og, 1 - side)      # named (weaker) side, stronger side
        lw, ls = lam_pair(Pw, Ps)
        pw, pd, pl = grid(lw, ls)
        p_yes = math.exp(-ls) * (1 - math.exp(-lw))
        Aw, Sa, S5 = Pw["all"], Ps["all"], Ps["l5"]
        rules = [
            (f"named team is the weaker side on goal difference ({Aw['gd']:+.1f} vs {Sa['gd']:+.1f})", Aw["gd"] < Sa["gd"], True),
            (f"named team win chance at most {pct(T['wtn_win_hard'])} (is {pct(pw)})", pw <= T["wtn_win_hard"], True),
            (f"named team clearly weaker: win chance at most {pct(T['wtn_win'])} (is {pct(pw)})", pw <= T["wtn_win"], False),
            (f"stronger side blanks at most {pct(T['wtn_s_fts'])} of games (is {pct(Sa['fts'])}) and in its last 5 ({pct(S5['fts'])})",
             Sa["fts"] <= T["wtn_s_fts"] and S5["fts"] <= T["wtn_s_fts"], False),
            (f"stronger side rarely loses: at most {pct(T['wtn_s_loss'])} (is {pct(Sa['loss'])})", Sa["loss"] <= T["wtn_s_loss"], False),
            (f"stronger side expected goals at least {T['wtn_s_lam']} (is {ls:.1f})", ls >= T["wtn_s_lam"], False),
            (f"named team keeps clean sheets at most {pct(T['wtn_w_cs'])} of games (is {pct(Aw['cs'])})", Aw["cs"] <= T["wtn_w_cs"], False),
            (f"named team concedes at least {T['wtn_w_ga']} per game (is {Aw['ga']:.1f})", Aw["ga"] >= T["wtn_w_ga"], False),
        ]
        if Pw["shots"]:
            rules.append((f"named team allows at least {T['wtn_w_shots']} shots per game (is {Pw['shots']['against']:.0f})",
                          Pw["shots"]["against"] >= T["wtn_w_shots"], False))
        g, fails = grade(rules)
        detail = (f"named (weaker) team: wins {pct(Aw['win'])}, clean sheets {pct(Aw['cs'])}, concedes {Aw['ga']:.1f} per game | "
                  f"stronger side: scores in {pct(1 - Sa['fts'])} of games, loses {pct(Sa['loss'])}, goals {Sa['gf']:.1f}-{Sa['ga']:.1f} per game")
        story = f"expected goals {lw:.1f}-{ls:.1f} (named team first): named team wins {pct(pw)}, wins to nil {pct(p_yes)}"
        return {"p": 1 - p_yes, "grade": g, "fails": fails, "detail": detail, "story": story}

    def hit(self, rec, side):
        return not (rec["score"][side] >= 1 and rec["score"][1 - side] == 0)


# ---------------------------------------------------------------- Team cards
class TeamCards(Spec):
    name = "TEAM CARDS OVER 0.5"
    slug = "cards"
    cols = {0: "Home Card Over 0.5", 1: "Away Card Over 0.5"}
    labels = {0: "Home Card Over 0.5", 1: "Away Card Over 0.5"}

    def judge(self, tg, og, side):
        Pt, Po = profile(tg, side), profile(og, 1 - side)
        Ct, Co = Pt["cards"], Po["cards"]
        if not Ct or not Co or Ct["n"] < MIN_N or Co["n"] < MIN_N:
            return None
        lam = 0.5 * (Ct["for"] + Co["drawn"])
        rules = [
            (f"team booked in at least {pct(T['card_rate'])} of games (is {pct(Ct['rate'])})", Ct["rate"] >= T["card_rate"], False),
            (f"opponent's games see the other side booked in at least {pct(T['card_drawn_rate'])} (is {pct(Co['drawn_rate'])})",
             Co["drawn_rate"] >= T["card_drawn_rate"], False),
            (f"expected cards at least {T['card_lam']} (is {lam:.1f})", lam >= T["card_lam"], False),
            (f"active team: scores at least {T['card_gf']} per game (is {Pt['gf']:.1f})", Pt["gf"] >= T["card_gf"], False),
            (f"booked in at least {T['card_l5']} of its last 5 (is {Ct['last5']} of {Ct['last5_n']})", Ct["last5"] >= T["card_l5"], False),
        ]
        g, fails = grade(rules)
        detail = (f"team: {Ct['for']:.1f} cards per game, booked in {pct(Ct['rate'])} of games, {Pt['gf']:.1f} goals per game | "
                  f"opponent's games: other side gets {Co['drawn']:.1f} cards per game, booked in {pct(Co['drawn_rate'])}")
        story = f"expected cards for the team {lam:.1f}"
        return {"p": 1 - math.exp(-lam), "grade": g, "fails": fails, "detail": detail, "story": story}

    def hit(self, rec, side):
        c = rec.get("cards")
        return None if c is None else c[side] >= 1


# ---------------------------------------------------------------- 2nd half over 0.5
class SecondHalfOver05(Spec):
    name = "2ND HALF OVER 0.5"
    slug = "h2_05"
    cols = {0: "2nd Half Over 0.5"}
    labels = {0: "2nd Half Over 0.5"}

    def judge(self, tg, og, side):
        Ph, Pa = profile(tg, 0), profile(og, 1)
        if Ph["h2"]["n"] < MIN_N or Pa["h2"]["n"] < MIN_N:
            return None
        lh, la = lam_pair(Ph, Pa)

        def s2(own, opp):   # expected share of this side's goals that come after half time
            vals = [v for v in (own["h2"]["sh_for"], opp["h2"]["sh_against"]) if v is not None]
            return mean(vals) if vals else 0.5

        lam2 = lh * s2(Ph, Pa) + la * s2(Pa, Ph)
        rh, ra = Ph["h2"]["r_for"], Pa["h2"]["r_for"]
        dh, da = Ph["l5"]["dull"], Pa["l5"]["dull"]
        rules = [
            (f"not a low-scoring game: expected goals at least {T['h2_total']} (is {lh + la:.1f})", lh + la >= T["h2_total"], False),
            (f"no 1-0 type teams: at most 1 game with 1 goal or fewer in the last 5 of each (home {dh * 5:.0f}, away {da * 5:.0f})",
             dh <= T["h2_dull"] and da <= T["h2_dull"], False),
            (f"expected 2nd-half goals at least {T['h2_lam']} (is {lam2:.1f})", lam2 >= T["h2_lam"], False),
            (f"2nd-half scorers: one team scores after half time in {pct(T['h2_one'])}+ of games, or both in {pct(T['h2_both'])}+ "
             f"(home {pct(rh)}, away {pct(ra)})", max(rh, ra) >= T["h2_one"] or min(rh, ra) >= T["h2_both"], False),
        ]
        g, fails = grade(rules)
        detail = (f"home: scores in the 2nd half in {pct(rh)} of games, {Ph['gf']:.1f} goals per game | "
                  f"away: scores in the 2nd half in {pct(ra)} of games, {Pa['gf']:.1f} goals per game")
        story = f"expected goals {lh:.1f}-{la:.1f}, expected 2nd-half goals {lam2:.1f}"
        return {"p": 1 - math.exp(-lam2), "grade": g, "fails": fails, "detail": detail, "story": story}

    def hit(self, rec, side):
        if not rec.get("timeline_ok"):
            return None
        return any(m >= 46 for m, h in rec["goals"])


# ---------------------------------------------------------------- Full match over 0.5
class FullOver05(Spec):
    name = "FULL MATCH OVER 0.5"
    slug = "full05"
    cols = {0: "Full Match Over 0.5"}
    labels = {0: "Full Match Over 0.5"}

    def judge(self, tg, og, side):
        Ph, Pa = profile(tg, 0), profile(og, 1)
        lh, la = lam_pair(Ph, Pa)
        Ah, Aa = Ph["all"], Pa["all"]
        rules = [
            (f"expected goals at least {T['o05_total']} (is {lh + la:.1f})", lh + la >= T["o05_total"], False),
            (f"both teams score: blank at most {pct(T['o05_fts'])} of games (home {pct(Ah['fts'])}, away {pct(Aa['fts'])})",
             Ah["fts"] <= T["o05_fts"] and Aa["fts"] <= T["o05_fts"], False),
            (f"0-0 draws at most {pct(T['o05_nil'])} of games (home {pct(Ah['nil'])}, away {pct(Aa['nil'])})",
             Ah["nil"] <= T["o05_nil"] and Aa["nil"] <= T["o05_nil"], False),
        ]
        g, fails = grade(rules)
        detail = (f"home: {Ah['gf']:.1f} goals scored, 0-0 in {pct(Ah['nil'])} | away: {Aa['gf']:.1f} goals scored, 0-0 in {pct(Aa['nil'])}")
        return {"p": 1 - math.exp(-(lh + la)), "grade": g, "fails": fails, "detail": detail,
                "story": f"expected goals {lh:.1f}-{la:.1f}"}

    def hit(self, rec, side):
        return rec["score"][0] + rec["score"][1] >= 1


# ---------------------------------------------------------------- No 3+ goals in a row
def side_rules(who, N, R, prun):
    """N = the side that must NOT score 3 in a row, R = the side that must hold and respond."""
    return [
        (f"{who}: model chance of a 3-goal run at most {pct(T['run_p'])} (is {pct(prun)})", prun <= T["run_p"], False),
        (f"{who}: other side concedes at most {T['run_ga']} per game (is {R['ga']:.1f})", R["ga"] <= T["run_ga"], False),
        (f"{who}: other side can respond, scores in at least {pct(1 - T['run_fts'])} of games (is {pct(1 - R['all']['fts'])})",
         R["all"]["fts"] <= T["run_fts"], False),
        (f"{who}: other side rarely lets a 2-goal run happen, at most {pct(T['run_opp2'])} of games (is {pct(R['run']['opp2'])})",
         R["run"]["opp2"] <= T["run_opp2"], False),
        (f"{who}: not a shooting side, at most {T['run_gf']} goals per game (is {N['gf']:.1f}) and 3+ goals in at most "
         f"{pct(T['run_g3'])} of games (is {pct(N['all']['g3'])})", N["gf"] <= T["run_gf"] and N["all"]["g3"] <= T["run_g3"], False),
        (f"{who}: other side defends well lately, last-5 conceded at most {T['run_l5ga']} per game (is {R['l5']['ga']:.1f})",
         R["l5"]["ga"] <= T["run_l5ga"], False),
    ]


class NoRunSide(Spec):
    name = "HOME / AWAY NO 3+ GOALS IN A ROW (assumed rule)"
    slug = "norun_side"
    cols = {0: "Home no 3+ in row", 1: "Away no 3+ in row"}
    labels = {0: "Home no 3+ in row", 1: "Away no 3+ in row"}

    def judge(self, tg, og, side):
        Pn, Pr = profile(tg, side), profile(og, 1 - side)
        if Pn["run"]["n"] < MIN_N or Pr["run"]["n"] < MIN_N:
            return None
        ln, lr = lam_pair(Pn, Pr)
        prun = p_run(ln, lr)
        rules = [(f"model chance of a 3-goal run at most {pct(T['run_hard'])} (is {pct(prun)})", prun <= T["run_hard"], True)]
        rules += side_rules("this side", Pn, Pr, prun)
        g, fails = grade(rules)
        detail = (f"this side: {Pn['gf']:.1f} goals per game, 2-goal runs in {pct(Pn['run']['own2'])} of games, 3-goal runs in "
                  f"{pct(Pn['run']['own3'])} | other side: concedes {Pr['ga']:.1f} per game, lets 2-goal runs happen in {pct(Pr['run']['opp2'])}")
        story = f"expected goals {ln:.1f}-{lr:.1f}: chance this side scores 3 in a row {pct(prun)}"
        return {"p": 1 - prun, "grade": g, "fails": fails, "detail": detail, "story": story}

    def hit(self, rec, side):
        if not rec.get("sides_ok"):
            return None
        return runs(rec["goals"])[side] < 3


class NoRunAny(Spec):
    name = "NO TEAM 3+ GOALS IN A ROW (assumed rule)"
    slug = "norun_any"
    cols = {0: "No team 3+ in row"}
    labels = {0: "No team 3+ in row"}

    def judge(self, tg, og, side):
        Ph, Pa = profile(tg, 0), profile(og, 1)
        if Ph["run"]["n"] < MIN_N or Pa["run"]["n"] < MIN_N:
            return None
        lh, la = lam_pair(Ph, Pa)
        ph, pa, pe = p_run(lh, la), p_run(la, lh), p_run(lh, la, both=True)
        rules = [(f"model chance of a 3-goal run by either side at most {pct(T['run_any_hard'])} (is {pct(pe)})", pe <= T["run_any_hard"], True)]
        rules += side_rules("home", Ph, Pa, ph) + side_rules("away", Pa, Ph, pa)
        g, fails = grade(rules)
        detail = (f"home: {Ph['gf']:.1f} scored / {Ph['ga']:.1f} conceded per game | away: {Pa['gf']:.1f} scored / {Pa['ga']:.1f} conceded per game | "
                  f"chance home runs {pct(ph)}, away runs {pct(pa)}")
        story = f"expected goals {lh:.1f}-{la:.1f}: chance of a 3-goal run by either side {pct(pe)}"
        return {"p": 1 - pe, "grade": g, "fails": fails, "detail": detail, "story": story}

    def hit(self, rec, side):
        if not rec.get("sides_ok"):
            return None
        r = runs(rec["goals"])
        return r[0] < 3 and r[1] < 3


SPECS = [DoubleChance(), DoubleChance1UP(), WinToNilNo(), TeamCards(),
         SecondHalfOver05(), FullOver05(), NoRunSide(), NoRunAny()]


if __name__ == "__main__":
    from engine import backtest
    print("BACKTEST: every market scored on past matches, using only the games before each one")
    for spec in SPECS:
        backtest(spec)
    print("\nRead it like this: if 'passed all filters' hits clearly more often than 'every match' and 'blocked', the filters help.")