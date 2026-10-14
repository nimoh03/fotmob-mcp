import sys

from fotmob_mcp.client import FotMobClient
from nts_extract import fmt, pct
from nts_resolve import norm

client = FotMobClient()

# League team stats we use. Each file has one row per team in the league.
WANT = [
    "expected_goals_conceded_team",    # StatValue = xG conceded (season total), SubStatValue = goals conceded
    "_set_piece_goals_conceded_team",  # StatValue = set-piece goals conceded (season total)
    "penalty_conceded_team",
    "clean_sheet_team",
]

_CACHE = {}


def collect(o, rows):
    if isinstance(o, dict):
        if "StatValue" in o:
            rows.append(o)
        for v in o.values():
            collect(v, rows)
    elif isinstance(o, list):
        for v in o:
            collect(v, rows)


def load_league(league_id):
    for params in ({"id": str(league_id)}, {"id": str(league_id), "ccode3": "NGA"}):
        try:
            return client.get_json("/api/data/leagues", params)
        except Exception:
            continue
    raise RuntimeError(f"could not load league {league_id}")


def stat_urls(league):
    """Return {stat name: url} for the league's team stats."""
    urls = {}

    def walk(o, path=""):
        if isinstance(o, dict):
            if "fetchAllUrl" in o and "name" in o and path.startswith("stats.teams"):
                urls[o["name"]] = o["fetchAllUrl"]
            for k, v in o.items():
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, f"{path}[{i}]")

    walk(league)
    return urls


def table_rows(league):
    """Fallback for leagues with no team stats: goals scored/conceded from the league table.
    Rows look like stat rows so the rest of the code can treat them the same way."""
    out = {}

    def walk(o, path=""):
        if isinstance(o, dict):
            if "scoresStr" in o and "played" in o and o.get("id") is not None and ".all[" in path:
                tid = str(o["id"])
                if tid not in out:
                    try:
                        gf, ga = [int(x) for x in str(o["scoresStr"]).split("-")]
                    except ValueError:
                        gf = ga = None
                    if ga is not None:
                        out[tid] = {
                            "ParticipantName": o.get("name"),
                            "TeamId": o["id"],
                            "MatchesPlayed": o.get("played"),
                            "StatValue": ga,
                            "SubStatValue": gf,
                        }
            for k, v in o.items():
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, f"{path}[{i}]")

    walk(league.get("table"), "table")
    return out


def fetch_stat(url):
    data = client.get_json(url, {})
    rows = []
    collect(data, rows)
    return {str(r.get("TeamId")): r for r in rows if r.get("TeamId") is not None}


def league_tables(league_id):
    league = load_league(league_id)
    urls = stat_urls(league)
    tables = {}
    for name in WANT:
        if name not in urls:
            continue
        try:
            tables[name] = fetch_stat(urls[name])
        except Exception as e:
            print(f"  failed to fetch {name}: {getattr(e, '__cause__', e)}")
    if "expected_goals_conceded_team" not in tables:
        rows = table_rows(league)
        if rows:
            tables["_league_table"] = rows
    return tables


def team_names(tables):
    out = {}
    for table in tables.values():
        for tid, r in table.items():
            if r.get("ParticipantName"):
                out[tid] = r["ParticipantName"]
    return out


def league_context(league_id):
    """Load a league's team tables once per run: (tables, teams)."""
    key = str(league_id)
    if key not in _CACHE:
        tables = league_tables(league_id)
        _CACHE[key] = (tables, team_names(tables))
    return _CACHE[key]


def find_team(side, teams):
    """Match a fixture side like 'CA Tigre' to a FotMob team name like 'Tigre'.
    Exact name first, then a team name inside the side, then a unique partial match."""
    s = norm(side)
    exact = [tid for tid, n in teams.items() if norm(n) == s]
    if exact:
        return exact[0]
    inside = [(len(norm(n)), tid) for tid, n in teams.items() if norm(n) and norm(n) in s]
    if inside:
        return max(inside)[1]
    contains = [tid for tid, n in teams.items() if s and s in norm(n)]
    if len(contains) == 1:
        return contains[0]
    return None


def per_match_all(table, field):
    out = {}
    for tid, r in table.items():
        mp = r.get("MatchesPlayed") or 0
        v = r.get(field)
        if mp and v is not None:
            out[tid] = v / mp
    return out


def rank_of(values, tid):
    """1 = lowest value (tightest defence)."""
    if tid not in values:
        return None
    return 1 + sum(1 for v in values.values() if v < values[tid])


def team_profile(team_id, tables):
    tid = str(team_id)
    xgc_t = tables.get("expected_goals_conceded_team", {})
    row = xgc_t.get(tid)

    if not row:
        # league without xG stats: goals conceded from the league table only
        lt = tables.get("_league_table", {})
        lrow = lt.get(tid)
        if not lrow:
            return None
        gc_all = per_match_all(lt, "StatValue")
        return {
            "name": lrow.get("ParticipantName"),
            "matches": lrow.get("MatchesPlayed") or 0,
            "n_teams": len(gc_all),
            "xgc_pm": None, "xgc_rank": None,
            "gc_pm": gc_all.get(tid), "gc_rank": rank_of(gc_all, tid),
            "sp_total": None, "sp_pm": None, "sp_rank": None,
            "pen_total": None, "cs_rate": None,
            "table_only": True,
        }

    mp = row.get("MatchesPlayed") or 0
    xgc_all = per_match_all(xgc_t, "StatValue")
    gc_all = per_match_all(xgc_t, "SubStatValue")
    sp_t = tables.get("_set_piece_goals_conceded_team", {})
    sp_all = per_match_all(sp_t, "StatValue")
    pen_row = tables.get("penalty_conceded_team", {}).get(tid)
    cs_row = tables.get("clean_sheet_team", {}).get(tid)
    sp_row = sp_t.get(tid)

    return {
        "name": row.get("ParticipantName"),
        "matches": mp,
        "n_teams": len(xgc_all),
        "xgc_pm": xgc_all.get(tid),
        "xgc_rank": rank_of(xgc_all, tid),
        "gc_pm": gc_all.get(tid),
        "gc_rank": rank_of(gc_all, tid),
        "sp_total": sp_row.get("StatValue") if sp_row else None,
        "sp_pm": sp_all.get(tid),
        "sp_rank": rank_of(sp_all, tid),
        "pen_total": pen_row.get("StatValue") if pen_row else None,
        "cs_rate": (cs_row.get("StatValue") / mp) if (cs_row and mp and cs_row.get("StatValue") is not None) else None,
        "table_only": False,
    }


def rk(rank, n):
    return "n/a" if rank is None else f"{rank} of {n}"


def opponent_lines(p, note=""):
    if not p:
        return [f"opponent      : unavailable ({note or 'no data'})"]
    if p.get("table_only"):
        return [
            f"opponent      : {p['name']} ({p['matches']} games) | "
            f"goals conceded {fmt(p['gc_pm'])}/match (rank {rk(p['gc_rank'], p['n_teams'])}) | "
            f"this league has no xG or set-piece defence stats  (rank 1 = tightest defence)"
        ]
    return [
        f"opponent      : {p['name']} ({p['matches']} games) | "
        f"xG conceded {fmt(p['xgc_pm'])}/match (rank {rk(p['xgc_rank'], p['n_teams'])}) | "
        f"goals conceded {fmt(p['gc_pm'])}/match (rank {rk(p['gc_rank'], p['n_teams'])}) | "
        f"set-piece goals conceded {fmt(p['sp_total'], 0)} (rank {rk(p['sp_rank'], p['n_teams'])}) | "
        f"penalties conceded {fmt(p['pen_total'], 0)} | clean sheets {pct(p['cs_rate'])}  "
        f"(rank 1 = tightest defence)"
    ]


def opponent_profile(fixture, team, league_id):
    """Find the opponent in the player's league. Returns (profile, note)."""
    try:
        tables, teams = league_context(league_id)
    except Exception as e:
        return None, f"league {league_id} team stats could not be loaded ({e})"
    if not tables:
        return None, f"no team stats or table for league {league_id}"
    sides = [s.strip() for s in fixture.split(" vs ")]
    if len(sides) != 2:
        return None, "fixture is not in 'A vs B' format"
    own = find_team(team, teams)
    if own is None:
        return None, f"own team '{team}' not found in league {league_id}"
    others = [t for t in (find_team(s, teams) for s in sides) if t is not None and t != own]
    if len(others) != 1:
        return None, f"opponent not found in league {league_id} (cup or foreign opponent?)"
    return team_profile(others[0], tables), ""


if __name__ == "__main__":
    league_id = sys.argv[1] if len(sys.argv) > 1 else "112"
    fixture = sys.argv[2] if len(sys.argv) > 2 else "Argentinos Juniors vs CA Tigre"

    tables, teams = league_context(league_id)
    if not tables:
        sys.exit("no team stats loaded")
    print(f"league {league_id}: {len(teams)} teams, stats loaded: {list(tables)}")

    for side in [s.strip() for s in fixture.split(" vs ")]:
        tid = find_team(side, teams)
        print(f"\n=== '{side}' -> {teams.get(tid)} (id {tid}) ===")
        if tid is None:
            print("  NOT MATCHED to any team in this league")
            continue
        for line in opponent_lines(team_profile(tid, tables)):
            print("  " + line)