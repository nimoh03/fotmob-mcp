"""Background notes for each player: his role in numbers, last season, and his club's style.
These are notes, never flags."""
from fotmob_mcp.client import FotMobClient
from nts_opponent import fetch_stat, league_context, load_league, stat_urls

client = FotMobClient()
_STYLE_CACHE = {}

ROLE_MIN_MINUTES = 450  # below this many league minutes this season, his role is not read from the numbers

# name of the league team stat, and whether FotMob already gives it per game (True) or as a season total (False)
STYLE = {
    "possession": ("possession_percentage_team", True),
    "xg": ("expected_goals_team", False),
    "goals": ("goals_team_match", True),
    "box": ("touches_in_opp_box_team", False),
    "long": ("accurate_long_balls_team", True),
    "corners": ("corner_taken_team", False),
}


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def pct(x):
    return "n/a" if x is None else f"{x * 100:.0f}%"


def goals_word(n):
    return f"{n:.0f} goal{'' if n == 1 else 's'}"


# ---------- role in numbers ----------

def role_line(d, r):
    pdsc = d.get("positionDescription") or {}
    primary = (pdsc.get("primaryPosition") or {}).get("label") or "player"
    plural = primary.lower() + "s"
    is_mid = "midfielder" in primary.lower()
    seen_list = sorted(pdsc.get("positions") or [], key=lambda p: p.get("occurences", 0), reverse=True)
    seen = ", ".join(f"{(p.get('strPos') or {}).get('label')} {p.get('occurences')}" for p in seen_list[:3])
    seen_txt = f" Seen as: {seen}." if seen else ""

    peers = {t.get("key"): num(t.get("value")) for t in (d.get("traits") or {}).get("items") or []}
    shots, chances = peers.get("shot_attempts"), peers.get("chances_created")
    defend, touches = peers.get("defensive_actions"), peers.get("touches")
    if all(v is None for v in (shots, chances, defend)):
        return f"Role: {primary}. FotMob has no comparison with other {plural} for him.{seen_txt}"

    numbers = (f"Against other {plural}: defensive work {pct(defend)}, shots {pct(shots)}, "
               f"chances created {pct(chances)}, touches {pct(touches)} (100% = most).")

    mins = r.get("minutes")
    if mins is not None and mins < ROLE_MIN_MINUTES:
        return (f"Role: too few minutes this season ({mins:.0f}) to read his role from the numbers. "
                f"{numbers}{seen_txt}")

    def hi(x, v):
        return x is not None and x >= v

    def lo(x, v):
        return x is not None and x <= v

    if hi(shots, 0.8) or hi(chances, 0.8):
        if hi(defend, 0.7):
            lean = "covers ground both ways (lots of defensive work and attacking output)"
        else:
            lean = f"attack-minded for a {primary.lower()}"
    elif hi(defend, 0.8) and lo(shots, 0.4) and lo(chances, 0.4):
        if is_mid:
            lean = "holding role (lots of defensive work, little attacking output)"
        else:
            lean = "defensive-minded (lots of defensive work, little attacking output)"
    elif lo(shots, 0.3) and lo(chances, 0.3):
        lean = "stays out of the attack"
    else:
        lean = "no strong lean either way"
    return f"Role: {lean}. {numbers}{seen_txt}"


# ---------- last season ----------

def flat(payload):
    """title -> stat item, for a playerStats payload or a profile's firstSeasonStats."""
    out = {}
    for group in (payload.get("statsSection") or {}).get("items") or []:
        for it in group.get("items") or []:
            out[it.get("title")] = it
    for it in (payload.get("topStatCard") or {}).get("items") or []:
        out.setdefault(it.get("title"), it)
    return out


def val(items, title):
    it = items.get(title)
    return num(it.get("statValue")) if it else None


def per90(items, title):
    it = items.get(title)
    return num(it.get("per90")) if it else None


def previous_season(d):
    """The season before the current one, and up to 3 tournaments to try (his main league first)."""
    seasons = d.get("statSeasons") or []
    main = d.get("mainLeague") or {}
    current = main.get("season") or (seasons[0].get("seasonName") if seasons else None)
    for s in seasons:
        if s.get("seasonName") == current:
            continue
        tours = s.get("tournaments") or []
        if not tours:
            continue
        same = [t for t in tours if t.get("tournamentId") == main.get("leagueId")]
        rest = [t for t in tours if t not in same]
        return s.get("seasonName"), (same + rest)[:3]
    return None, []


def last_season(pid, d):
    season, tours = previous_season(d)
    if not tours:
        return None
    now = flat(d.get("firstSeasonStats") or {})
    error = None
    for tour in tours:
        if not tour.get("entryId"):
            continue
        try:
            data = client.get_json(
                "/api/data/playerStats", {"playerId": str(pid), "seasonId": tour["entryId"]}
            )
        except Exception as e:
            error = e
            continue
        if not isinstance(data, dict) or not (data.get("topStatCard") or data.get("statsSection")):
            continue
        items = flat(data)
        minutes, matches = val(items, "Minutes"), val(items, "Matches")
        if minutes and minutes == val(now, "Minutes") and matches == val(now, "Matches"):
            continue  # FotMob gave us this season again, so we don't trust it
        return {
            "season": season,
            "tournament": tour.get("name"),
            "matches": matches,
            "started": val(items, "Started"),
            "minutes": minutes,
            "goals": val(items, "Goals"),
            "xg90": per90(items, "xG"),
            "shots90": per90(items, "Shots"),
        }
    if error is not None:
        raise error
    return None


def last_season_line(ls, r):
    if not ls:
        return "Last season: no data found on FotMob."
    bits = []
    if ls["matches"] is not None:
        bits.append(f"{ls['matches']:.0f} games")
    if ls["minutes"] is not None:
        bits.append(f"{ls['minutes']:.0f} min")
    if ls["goals"] is not None:
        bits.append(goals_word(ls["goals"]))
    if ls["xg90"] is not None:
        bits.append(f"xG {ls['xg90']:.2f} per 90")
    if ls["shots90"] is not None:
        bits.append(f"{ls['shots90']:.1f} shots per 90")
    s = f"Last season ({ls['season']}, {ls['tournament']}): " + ", ".join(bits) + "."
    if (ls["minutes"] or 0) < 300:
        s += " Very little playing time, so not much to go on."
    a, b = ls.get("shots90"), r.get("shots90")
    if (a is not None and b is not None and (ls["minutes"] or 0) >= 600 and (r.get("minutes") or 0) >= 600
            and abs(a - b) >= 0.7 and (a >= 2 * b or b >= 2 * a)):
        s += f" His shot volume has changed a lot ({a:.1f} then, {b:.1f} now per 90), so his role may have changed."
    return s + " Background only: he may have played a different role then."


# ---------- club style ----------

def style_tables(league_id):
    """{style key: {team id: value per game}} for one league, loaded once per run."""
    key = str(league_id)
    if key in _STYLE_CACHE:
        return _STYLE_CACHE[key]
    out = {}
    try:
        urls = stat_urls(load_league(league_id))
    except Exception:
        urls = {}
    for k, (name, per_match) in STYLE.items():
        if name not in urls:
            continue
        try:
            table = fetch_stat(urls[name])
        except Exception:
            continue
        vals = {}
        for tid, row in table.items():
            v, mp = num(row.get("StatValue")), row.get("MatchesPlayed") or 0
            if v is None:
                continue
            if per_match:
                vals[tid] = v
            elif mp:
                vals[tid] = v / mp
        if vals:
            out[k] = vals
    if "xg" not in out and "goals" not in out:
        # leagues with no team stats: goals scored per game from the league table
        try:
            tables, _ = league_context(league_id)
            vals = {}
            for tid, row in (tables.get("_league_table") or {}).items():
                mp, gf = row.get("MatchesPlayed") or 0, num(row.get("SubStatValue"))
                if mp and gf is not None:
                    vals[str(tid)] = gf / mp
            if vals:
                out["goals"] = vals
        except Exception:
            pass
    _STYLE_CACHE[key] = out
    return out


def team_formation(club_id):
    for params in ({"id": str(club_id)}, {"id": str(club_id), "ccode3": "NGA"}):
        try:
            t = client.get_json("/api/data/teams", params)
            return ((t.get("overview") or {}).get("lastLineupStats") or {}).get("formation")
        except Exception:
            continue
    return None


def club_style_line(d):
    team = d.get("primaryTeam") or {}
    club_id, name = team.get("teamId"), team.get("teamName") or "his club"
    league_id = (d.get("mainLeague") or {}).get("leagueId")
    if club_id is None or league_id is None:
        return None
    tabs = style_tables(league_id)
    if not tabs:
        return "Club style: no team style stats for this league."
    tid = str(club_id)

    def look(key):
        vals = tabs.get(key) or {}
        if tid not in vals:
            return None
        v = vals[tid]
        return v, 1 + sum(1 for x in vals.values() if x > v), len(vals)

    bits = []
    unit = "xG"
    got = look("xg")
    if not got:
        got, unit = look("goals"), "goals"
    if got:
        v, rk, n = got
        if rk <= n / 4:
            label = "attacking side"
        elif rk > n * 3 / 4:
            label = "low-scoring side"
        else:
            label = "average attack"
        bits.append(f"{label} ({v:.2f} {unit} a game, rank {rk} of {n})")

    def extreme(key, high, low, show):
        g = look(key)
        if not g:
            return
        v, rk, n = g
        if rk <= n / 4:
            bits.append(f"{high} ({show(v)}, rank {rk} of {n})")
        elif rk > n * 3 / 4:
            bits.append(f"{low} ({show(v)}, rank {rk} of {n})")

    extreme("possession", "keeps the ball", "doesn't keep the ball", lambda v: f"{v:.0f}% possession")
    extreme("long", "plays direct", "plays short", lambda v: f"{v:.0f} long balls a game")
    extreme("box", "lives in the opposition box", "rarely gets into the box", lambda v: f"{v:.1f} box touches a game")
    extreme("corners", "wins lots of corners", "wins few corners", lambda v: f"{v:.1f} corners a game")

    if not bits:
        bits.append("average on everything we track")
    form = team_formation(club_id)
    tail = f" Last match formation: {form}." if form else ""
    return f"Club style ({name}, against its league): " + "; ".join(bits) + "." + tail


# ---------- the one function the report calls ----------

def context_lines(pid, r):
    """Return the background lines for one player. r is the dict from nts_extract.extract()."""
    try:
        d = client.get_json(
            "/api/data/playerData", {"id": str(pid), "includeMarketValues": "true"}
        )
    except Exception:
        return []
    out = []
    try:
        line = role_line(d, r)
        if line:
            out.append(line)
    except Exception:
        pass
    try:
        out.append(last_season_line(last_season(pid, d), r))
    except Exception as e:
        why = str(getattr(e, "__cause__", None) or e)[:80]
        out.append(f"Last season: could not load from FotMob ({why}).")
    try:
        line = club_style_line(d)
        if line:
            out.append(line)
    except Exception:
        pass
    return out