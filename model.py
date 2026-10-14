"""
model.py - team profiles, a simple goals model and goal-run maths, shared by every market check.
"""
import math

FLOOR_L = 0.15   # an expected-goals figure never goes below this
MAXG = 10        # the goal grid goes up to this many goals per team


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def rate(flags):
    flags = list(flags)
    return sum(1 for f in flags if f) / len(flags) if flags else 0.0


def pois(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def runs(goals):
    """Longest run of goals in a row for [home, away]; goals = [[minute, 1 if home else 0], ...] in order."""
    best, last, cur = [0, 0], None, 0
    for _, h in goals:
        s = 0 if h == 1 else 1
        cur = cur + 1 if s == last else 1
        last = s
        if cur > best[s]:
            best[s] = cur
    return best


def basic(pairs):
    """pairs = [(goals for, goals against)] -> plain season numbers."""
    n = len(pairs)
    if not n:
        return None
    win = rate(a > b for a, b in pairs)
    draw = rate(a == b for a, b in pairs)
    return {
        "n": n, "gf": mean(a for a, b in pairs), "ga": mean(b for a, b in pairs),
        "win": win, "draw": draw, "loss": max(0.0, 1 - win - draw), "ppg": 3 * win + draw,
        "gd": mean(a - b for a, b in pairs),
        "cs": rate(b == 0 for a, b in pairs), "fts": rate(a == 0 for a, b in pairs),
        "g3": rate(a >= 3 for a, b in pairs),
        "dull": rate(a + b <= 1 for a, b in pairs), "nil": rate(a + b == 0 for a, b in pairs),
    }


def profile(games, side):
    """Everything we know about one team from its earlier league games.
    games = [(record, side_it_played)] oldest first. side = the side it plays on today (0 home, 1 away)."""
    pairs = [(r["score"][s], r["score"][1 - s]) for r, s in games]
    vpairs = [(r["score"][s], r["score"][1 - s]) for r, s in games if s == side]
    P = {"n": len(games), "all": basic(pairs), "ven": basic(vpairs), "l5": basic(pairs[-5:])}

    def bl(key):   # half venue-specific, half all games (venue only when there are 3+ games there)
        v = P["ven"]
        if v and v["n"] >= 3:
            return 0.5 * v[key] + 0.5 * P["all"][key]
        return P["all"][key]

    P["gf"], P["ga"] = bl("gf"), bl("ga")

    # goal timing and goal runs: only games whose goal timeline matches the score
    hg = [(r, s) for r, s in games if r.get("sides_ok")]
    gf2 = ga2 = gft = gat = 0
    r2f, r2a, o2, o3, a2, a3 = [], [], [], [], [], []
    for r, s in hg:
        mine = [m for m, h in r["goals"] if (h == 1) == (s == 0)]
        theirs = [m for m, h in r["goals"] if (h == 1) != (s == 0)]
        gf2 += sum(1 for m in mine if m >= 46)
        ga2 += sum(1 for m in theirs if m >= 46)
        gft += len(mine)
        gat += len(theirs)
        r2f.append(any(m >= 46 for m in mine))
        r2a.append(any(m >= 46 for m in theirs))
        rr = runs(r["goals"])
        o2.append(rr[s] >= 2)
        o3.append(rr[s] >= 3)
        a2.append(rr[1 - s] >= 2)
        a3.append(rr[1 - s] >= 3)
    P["h2"] = {"n": len(hg),
               "sh_for": gf2 / gft if gft >= 3 else None,        # share of its goals scored after half time
               "sh_against": ga2 / gat if gat >= 3 else None,    # share of the goals it concedes after half time
               "r_for": rate(r2f), "r_against": rate(r2a)}       # share of games with a 2nd-half goal for / against
    P["run"] = {"n": len(hg), "own2": rate(o2), "own3": rate(o3), "opp2": rate(a2), "opp3": rate(a3)}

    # cards (only games where FotMob has card data)
    cg = [(r, s) for r, s in games if r.get("cards") is not None]
    if cg:
        last = cg[-5:]
        P["cards"] = {
            "n": len(cg), "for": mean(r["cards"][s] for r, s in cg),
            "rate": rate(r["cards"][s] >= 1 for r, s in cg),
            "drawn": mean(r["cards"][1 - s] for r, s in cg),               # cards the other side gets in its games
            "drawn_rate": rate(r["cards"][1 - s] >= 1 for r, s in cg),
            "last5": sum(1 for r, s in last if r["cards"][s] >= 1), "last5_n": len(last),
        }
    else:
        P["cards"] = None

    # shots (only where FotMob has them)
    sg = [(r, s) for r, s in games if r.get("shots")]
    P["shots"] = ({"for": mean(r["shots"][s] for r, s in sg), "against": mean(r["shots"][1 - s] for r, s in sg)}
                  if len(sg) >= 4 else None)
    return P


def lam_pair(Pt, Po):
    """Expected goals (team, opponent): each side's attack averaged with the other side's defence."""
    lt = max(FLOOR_L, 0.5 * (Pt["gf"] + Po["ga"]))
    lo = max(FLOOR_L, 0.5 * (Po["gf"] + Pt["ga"]))
    return lt, lo


def grid(lt, lo):
    """(team wins, draw, team loses) from independent goal counts."""
    pw = pd = pl = 0.0
    for i in range(MAXG + 1):
        pi = pois(i, lt)
        for j in range(MAXG + 1):
            p = pi * pois(j, lo)
            if i > j:
                pw += p
            elif i == j:
                pd += p
            else:
                pl += p
    return pw, pd, pl


def run_prob(n, p, k, both):
    """Chance that n goals, each by side X with chance p, contain k goals in a row by X (or by either side if both)."""
    states = {(None, 0): 1.0}
    hit = 0.0
    for _ in range(n):
        new = {}
        for (side, run), pr in states.items():
            for s, ps in ((0, p), (1, 1 - p)):
                run2 = run + 1 if side == s else 1
                if run2 >= k and (s == 0 or both):
                    hit += pr * ps
                else:
                    key = (s, min(run2, k - 1))
                    new[key] = new.get(key, 0.0) + pr * ps
        states = new
    return hit


def p_run(lam_x, lam_y, k=3, both=False):
    """Chance side X scores k+ goals in a row (no goal from the other side in between), or either side if both."""
    lam = lam_x + lam_y
    p = lam_x / lam
    total = 0.0
    for n in range(0, 15):
        total += pois(n, lam) * run_prob(n, p, k, both)
    return total


def p_1up_lose(lt, lo):
    """Chance the team loses AND was never ahead at any moment (the only way a 1UP double chance loses)."""
    lam = lt + lo
    p = lt / lam
    total = 0.0
    for n in range(0, 15):
        st = {0: 1.0}
        for _ in range(n):
            new = {}
            for d, pr in st.items():
                if d + 1 <= 0:                       # team scores but is still not ahead
                    new[d + 1] = new.get(d + 1, 0.0) + pr * p
                new[d - 1] = new.get(d - 1, 0.0) + pr * (1 - p)   # (if the team gets ahead the bet is safe: dropped)
            st = new
        total += pois(n, lam) * sum(pr for d, pr in st.items() if d < 0)
    return total


def grade(rules):
    """rules = [(text, passed, hard)] -> (grade, failed texts).
    A = everything passed. B = exactly one soft rule failed. None = a hard rule failed or 2+ soft rules failed."""
    hard = [t for t, ok, h in rules if h and not ok]
    soft = [t for t, ok, h in rules if not h and not ok]
    if hard or len(soft) >= 2:
        return None, hard + soft
    return ("A" if not soft else "B"), soft