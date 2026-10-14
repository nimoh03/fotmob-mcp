import json
from pathlib import Path

from fotmob_mcp.client import FotMobClient

PLAYERS = {
    "Cristiano Ronaldo": 30893,
    "Dominic Calvert-Lewin": 612150,
    "Mikael Ishak": 200601,
}

client = FotMobClient()

# FotMob shot "situation" values. Free kicks are kept apart from corners and other set pieces.
SET_PIECE = {"SetPiece", "FromCorner", "ThrowInSetPiece"}
FREE_KICK = {"FreeKick"}


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def fmt(x, nd=2):
    return "n/a" if x is None else f"{x:.{nd}f}"


def pct(x):
    return "n/a" if x is None else f"{x * 100:.0f}%"


def per90(x):
    return "n/a" if x is None else f"{x:.2f}/90"


def season_stats(d):
    """Flatten FotMob's stat groups into {localizedTitleId: item}."""
    fss = d.get("firstSeasonStats") or {}
    out = {}
    for group in (fss.get("statsSection") or {}).get("items") or []:
        for it in group.get("items") or []:
            out[it.get("localizedTitleId")] = it
    for it in (fss.get("topStatCard") or {}).get("items") or []:
        out.setdefault(it.get("localizedTitleId"), it)
    return out


def stats_by_title(d):
    """Same stats, keyed by their English title (e.g. 'Aerials won')."""
    fss = d.get("firstSeasonStats") or {}
    out = {}
    for group in (fss.get("statsSection") or {}).get("items") or []:
        for it in group.get("items") or []:
            out[it.get("title")] = it
    for it in (fss.get("topStatCard") or {}).get("items") or []:
        out.setdefault(it.get("title"), it)
    return out


def sv(stats, key):
    it = stats.get(key)
    return num(it.get("statValue")) if it else None


def p90(stats, key):
    it = stats.get(key)
    return num(it.get("per90")) if it else None


def started(m):
    return m.get("playedInMatch") and m.get("lineupPositionId") is not None


def split_counts(games):
    return {
        "started": sum(1 for m in games if started(m)),
        "cameo": sum(1 for m in games if m.get("playedInMatch") and not started(m)),
        "unused": sum(1 for m in games if m.get("onBench") and not m.get("playedInMatch")),
        "minutes": sum(m.get("minutesPlayed") or 0 for m in games),
        "goals": sum(m.get("goals") or 0 for m in games),
    }


def last_date(games):
    """Date (YYYY-MM-DD) of the newest game in a newest-first list."""
    if not games:
        return None
    return ((games[0].get("matchDate") or {}).get("utcTime") or "")[:10] or None


def threat_profile(shotmap, tstats, traits, minutes):
    """How he is likely to score: headers, set pieces, shot location, position rank."""
    peers = {
        t.get("key"): num(t.get("value"))
        for t in (traits or {}).get("items") or []
    }
    n = len(shotmap)
    nineties = (minutes / 90) if minutes else None
    xg_total = sum(s.get("expectedGoals") or 0.0 for s in shotmap)
    heads = [s for s in shotmap if s.get("shotType") == "Header"]
    sp_xg = sum(
        s.get("expectedGoals") or 0.0 for s in shotmap if s.get("situation") in SET_PIECE
    )
    fk_xg = sum(
        s.get("expectedGoals") or 0.0 for s in shotmap if s.get("situation") in FREE_KICK
    )
    inside = sum(1 for s in shotmap if s.get("isFromInsideBox"))
    return {
        "shots": n,
        "inside_pct": (inside / n) if n else None,
        "headers": len(heads),
        "headers_p90": (len(heads) / nineties) if nineties else None,
        "xg_total": xg_total,
        "sp_xg": sp_xg,
        "sp_share": (sp_xg / xg_total) if xg_total > 0 else None,
        "fk_xg": fk_xg,
        "box_touches_p90": p90(tstats, "Touches in opposition box"),
        "aerials_p90": p90(tstats, "Aerials won"),
        "chances_p90": p90(tstats, "Chances created"),
        "peer_shots": peers.get("shot_attempts"),
        "peer_aerials": peers.get("aerials_won"),
        "peer_chances": peers.get("chances_created"),
        "peer_goals": peers.get("goals"),
    }


def threat_lines(t):
    if not t:
        return []
    if not t["shots"]:
        return ["threat        : no shots recorded this season"]
    n = t["shots"]
    lines = [
        f"threat        : {n} shot{'' if n == 1 else 's'}, {pct(t['inside_pct'])} inside box | "
        f"headers {t['headers']} ({per90(t['headers_p90'])}) | "
        f"set-piece xG {fmt(t['sp_xg'])} of {fmt(t['xg_total'])} ({pct(t['sp_share'])}) | "
        f"free-kick xG {fmt(t['fk_xg'])} | "
        f"box touches {per90(t['box_touches_p90'])} | aerials won {per90(t['aerials_p90'])}"
    ]
    if any(t[k] is not None for k in ("peer_shots", "peer_aerials", "peer_chances", "peer_goals")):
        lines.append(
            f"vs position   : shots {pct(t['peer_shots'])} | aerials {pct(t['peer_aerials'])} | "
            f"chances created {pct(t['peer_chances'])} | goals {pct(t['peer_goals'])}  "
            f"(100% = best in his position)"
        )
    return lines


def extract(pid):
    d = client.get_json(
        "/api/data/playerData", {"id": str(pid), "includeMarketValues": "true"}
    )
    raw_stats = season_stats(d)
    raw_tstats = stats_by_title(d)
    team = d.get("primaryTeam") or {}
    club_id = team.get("teamId")
    main = d.get("mainLeague") or {}
    league_id = main.get("leagueId")
    recent = d.get("recentMatches") or []
    shotmap = (d.get("firstSeasonStats") or {}).get("shotmap") or []

    # CLUB games only: the match was played for his current club
    club_games = [
        m for m in recent if club_id is not None and m.get("teamId") == club_id
    ]

    # his main-league games this season (newest first)
    league_played = [
        m for m in club_games
        if league_id is not None
        and m.get("leagueId") == league_id
        and m.get("playedInMatch")
    ]

    # Season stats are only trusted as CLUB stats if we can see club league rows.
    trusted = bool(league_played)
    stats = raw_stats if trusted else {}
    tstats = raw_tstats if trusted else {}

    # shots grouped per match + penalty count
    per_match, pens = {}, 0
    for s in shotmap:
        b = per_match.setdefault(s.get("matchId"), {"xg": 0.0, "shots": 0})
        b["xg"] += s.get("expectedGoals") or 0.0
        b["shots"] += 1
        if "pen" in str(s.get("situation", "")).lower():
            pens += 1

    n_season = int(sv(stats, "matches_uppercase") or 0)
    season_games = league_played[:n_season] if n_season else league_played[:5]
    last5 = season_games[:5]

    l5_min = sum(m.get("minutesPlayed") or 0 for m in last5)
    l5_xg = sum(per_match.get(m["id"], {}).get("xg", 0.0) for m in last5)
    l5_shots = sum(per_match.get(m["id"], {}).get("shots", 0) for m in last5)

    club5 = split_counts(club_games[:5])

    minutes = sv(stats, "minutes_played")
    matches = sv(stats, "matches_uppercase")
    xg90 = p90(stats, "expected_goals")
    has_xg = xg90 is not None

    threat = threat_profile(shotmap, tstats, d.get("traits"), minutes) if trusted else None

    nm = d.get("nextMatch") or {}
    has_next = bool(nm.get("matchId"))
    is_home = (nm.get("homeId") == club_id) if has_next else None
    next_opp = None
    if has_next:
        next_opp = nm.get("awayName") if is_home else nm.get("homeName")

    positions = sorted(
        (d.get("positionDescription") or {}).get("positions") or [],
        key=lambda p: p.get("occurences", 0),
        reverse=True,
    )

    if trusted and has_xg:
        tier = "FULL (club league rows + xG)"
    elif trusted:
        tier = "CLUB, NO XG (club league rows, no xG)"
    elif club_games:
        tier = "CLUB ROWS ONLY (no club league stats)"
    else:
        tier = "NO CLUB DATA - UNVERIFIED (FotMob has no club games for him)"

    return {
        "name": d.get("name"),
        "team": team.get("teamName"),
        "provider": d.get("dataProvider"),
        "tier": tier,
        "position": ((d.get("positionDescription") or {}).get("primaryPosition") or {}).get("label"),
        "positions_seen": [((p.get("strPos") or {}).get("label"), p.get("occurences")) for p in positions[:3]],
        "injury": d.get("injuryInformation"),
        "next_opp": next_opp,
        "next_home": is_home,
        "next_date": nm.get("matchDate") if has_next else None,
        "league": main.get("leagueName") if trusted else None,
        "league_id": league_id if trusted else None,
        "matches": matches,
        "starts_fotmob": sv(stats, "player_started_matches"),
        "starts_calc": sum(1 for m in season_games if started(m)) if (trusted and season_games) else None,
        "minutes": minutes,
        "avg_min": (minutes / matches) if minutes and matches else None,
        "goals": sv(stats, "goals"),
        "xg": sv(stats, "expected_goals"),
        "xg90": xg90,
        "npxg90": p90(stats, "non_penalty_xg"),
        "shots90": p90(stats, "shots"),
        "sot90": p90(stats, "ShotsOnTarget"),
        "pens_taken": pens if trusted else None,
        "l5_games": len(last5),
        "l5_minutes": l5_min,
        "l5_goals": sum(m.get("goals") or 0 for m in last5),
        "l5_xg": l5_xg if has_xg else None,
        "l5_xg90": (l5_xg / l5_min * 90) if (l5_min and has_xg) else None,
        "l5_shots": l5_shots if has_xg else None,
        "club5": club5,
        "club5_games": len(club_games[:5]),
        "last_club_date": last_date(club_games),
        "threat": threat,
        "n_recent": len(recent),
        "n_club": len(club_games),
        "n_shots": len(shotmap),
        "has_xg": has_xg,
    }


def start_check(r):
    if r["starts_fotmob"] is None or r["starts_calc"] is None:
        return "n/a (not enough data to check)"
    return "OK" if r["starts_fotmob"] == r["starts_calc"] else "MISMATCH"


def show(r):
    print(f"\n=== {r['name']} | {r['team']} | {r['position']} | {r['provider']} ===")
    print(f"DATA TIER      : {r['tier']}")
    print(f"positions seen : {r['positions_seen']}")
    print(f"injury         : {r['injury']}")
    if r["next_opp"]:
        where = "home" if r["next_home"] else "away"
        print(f"next club match: vs {r['next_opp']} ({where}) {r['next_date']}")
    else:
        print("next club match: none listed")
    print(f"data rows      : {r['n_recent']} recent | {r['n_club']} club | "
          f"{r['n_shots']} shots | xG data: {'yes' if r['has_xg'] else 'NO'}")

    if r["league"]:
        print(f"{r['league']} season: {fmt(r['matches'],0)} apps, "
              f"{fmt(r['minutes'],0)} min, avg {fmt(r['avg_min'],0)} min/app")
        print(f"starts         : fotmob {fmt(r['starts_fotmob'],0)} vs calc "
              f"{fmt(r['starts_calc'],0)}  [{start_check(r)}]")
        print(f"season shooting: goals {fmt(r['goals'],0)} | xG {fmt(r['xg'])} | xG/90 {fmt(r['xg90'])} "
              f"| npxG/90 {fmt(r['npxg90'])} | shots/90 {fmt(r['shots90'])} | SoT/90 {fmt(r['sot90'])}")
        pens = "n/a" if r["pens_taken"] is None else r["pens_taken"]
        print(f"penalties taken: {pens}")
        print(f"last {r['l5_games']} league  : {r['l5_goals']} goals | xG {fmt(r['l5_xg'])} "
              f"| xG/90 {fmt(r['l5_xg90'])} | {fmt(r['l5_shots'],0)} shots in {r['l5_minutes']} min")
    else:
        print("club league    : no trusted club league stats (season numbers hidden on purpose)")

    c = r["club5"]
    print(f"last {r['club5_games']} club     : {c['started']} started, {c['cameo']} cameo, "
          f"{c['unused']} unused sub | {c['minutes']} min | {c['goals']} goals "
          f"| last game {r['last_club_date'] or 'n/a'}")
    for line in threat_lines(r["threat"]):
        print(line)


if __name__ == "__main__":
    results = []
    for name, pid in PLAYERS.items():
        try:
            r = extract(pid)
            results.append(r)
            show(r)
        except Exception as e:
            print(f"\n=== {name} === !! failed: {e} | {getattr(e, '__cause__', None)}")
    Path("probe_out").mkdir(exist_ok=True)
    Path("probe_out/summary.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")