"""Rank teams by how likely they are to get at least 1 card (Over 0.5 team bookings).

  python rank_cards.py                      reads foul.csv, shows the top 30 picks
  python rank_cards.py foul.csv --top=15    show only the top 15
  python rank_cards.py --top=0              show every match
  python rank_cards.py --n=10               matches per team (default 10)
  python rank_cards.py --min-edge=1         only show picks with edge of at least +1 point
  python rank_cards.py --sort=score         old ranking (by score instead of edge)
  python rank_cards.py --find="Man Utd"     search FotMob for a team name

Put this file in the SAME folder as five_min.py (it imports from it).

ONE PICK PER MATCH: from each game only the better of the two teams is ranked
(you can't take both). The other side is shown in the last column.

v2 CHANGES (after the Oct 10 post-mortem):
  1. PRIOR_WEIGHT 3 -> 15. The old prior let a 10/10 team reach 96.5%. Now it is 91%.
  2. EDGE now uses the smoothed probability (p_model), not the raw hit rate.
     Before, a 5/5 team showed a fake +15 point edge.
  3. Ranking and the pick inside each match are by EDGE (p_model minus the price's
     implied probability), with the score as tie-breaker. Use --sort=score for the old way.
  4. A referee with fewer than REF_MIN_GAMES games no longer moves the score.
  5. New column p_model (last column of the CSVs) so post-mortems can use the exact
     number the script used.

SCORE (first-guess weights, change them below):
  smoothed hit rate (how often the team got a card, pulled toward 85% for small samples)
  + a boost for average cards per game
  + a boost for average fouls per game   (a team that fouls a lot is good for this bet)
  + a referee adjustment: how far the ref's cards-per-match is above/below the average,
    scaled by how many of his games we have (a ref with a big sample counts for more)

Name matching works like five_min.py (search several spellings, then confirm the
fixture on the team's FotMob page). Teams that can't be found are fixed on the spot:
the script asks for the right FotMob name and saves it to aliases.csv for next time.
"""
import csv
import json
import os
import re
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from statistics import mean

from fotmob_mcp.client import FotMobClient
from five_min import (
    candidates, team_page, page_matches, league_of, league_matches,
    resolve_fixture, parse_dt,
)

ARGV = sys.argv[1:]
args = [a for a in ARGV if not a.startswith("--")]
opts = {a.split("=", 1)[0]: (a.split("=", 1)[1] if "=" in a else "1")
        for a in ARGV if a.startswith("--")}

CSV_PATH = args[0] if args else "foul.csv"
N = int(opts.get("--n", 10))
TOP = int(opts.get("--top", 30))
SORT = opts.get("--sort", "edge").lower()            # "edge" (default) or "score"
MIN_EDGE = float(opts["--min-edge"]) if "--min-edge" in opts else None   # in points

# ---- scoring weights (first guesses) ---------------------------------------
PRIOR_RATE = 0.85     # small samples are pulled toward this hit rate
PRIOR_WEIGHT = 15     # worth this many pseudo-matches (was 3: far too weak)
W_CARDS = 3.0         # points per average card per game
W_FOULS = 0.5         # points per average foul per game
W_REF = 4.0           # points per card/match the referee is above (or below) the average
REF_K = 4             # referee confidence = games / (games + REF_K), so bigger samples count more
REF_MIN_GAMES = 3     # a referee with fewer games than this does not move the score
SMALL_SAMPLE = 6
# ----------------------------------------------------------------------------

WORKERS = 3
CACHE_FILE = "card_cache.json"   # finished matches never change, so cache them
ALIAS_FILE = "aliases.csv"       # sportybet_name,fotmob_name  (edit freely)
OUT_PICKS = "ranked_cards.csv"       # one row per match (the pick)
OUT_ALL = "ranked_cards_all.csv"     # every team
TOP_FILE = "top_picks.txt"           # the final report, as text

# SportyBet name -> name to search on FotMob. Your own fixes go in aliases.csv.
ALIASES = {
    "PSG": "Paris Saint-Germain",
    "Wolves": "Wolverhampton Wanderers",
    "Man Utd": "Manchester United",
    "Tottenham": "Tottenham Hotspur",
    "Augsburg": "FC Augsburg",
    "Bayern Munich": "Bayern München",
    "Parma Calcio": "Parma",
}

client = FotMobClient()
league_cache = {}

NOT_FIRST_TEAM = re.compile(r"\(W\)|\bU\d{2}\b|\bwomen\b|\bII\b|\bB$", re.I)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def num(value):
    if isinstance(value, (int, float)):
        return float(value)
    m = re.match(r"-?\d+(\.\d+)?", str(value or "").strip())
    return float(m.group()) if m else None


def to_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def collect(obj, out):
    if isinstance(obj, dict):
        values = obj.get("stats")
        if (
            obj.get("key")
            and isinstance(values, list)
            and len(values) == 2
            and not any(isinstance(x, (dict, list)) for x in values)
        ):
            out.setdefault(obj["key"], values)
        for v in obj.values():
            collect(v, out)
    elif isinstance(obj, list):
        for v in obj:
            collect(v, out)
    return out


def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f)


match_cache = load_cache()


def load_aliases():
    if not os.path.exists(ALIAS_FILE):
        return
    with open(ALIAS_FILE, encoding="utf-8-sig", newline="") as f:
        for row in csv.reader(f):
            if len(row) >= 2 and row[0].strip() and row[0].strip().lower() != "sportybet_name":
                ALIASES[row[0].strip()] = row[1].strip()


def save_alias(name, fotmob_name):
    new = not os.path.exists(ALIAS_FILE)
    with open(ALIAS_FILE, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["sportybet_name", "fotmob_name"])
        w.writerow([name, fotmob_name])


load_aliases()


# ---------------------------------------------------------------------------
# --find : look up a name on FotMob
# ---------------------------------------------------------------------------
if "--find" in opts:
    term = opts["--find"]
    print(f"FotMob results for '{term}':")
    try:
        found = candidates(term)
    except Exception as e:
        found = []
        print(f"  search failed: {e}")
    if not found:
        print("  nothing found. Try a shorter or different spelling.")
    for c in found[:10]:
        print(f"  {c.get('name')}   (id {c['id']})")
    print(f"\nTo save a fix, add a line to {ALIAS_FILE}:   SportyBet name,FotMob name")
    sys.exit(0)


# ---------------------------------------------------------------------------
# 1. read foul.csv
# ---------------------------------------------------------------------------
def load_fixtures(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        rows = list(reader)

    def get(r, k):
        return (r.get(k) or "").strip()

    fixtures = {}
    if "homeOver05" in headers:
        for r in rows:
            fixtures[get(r, "match")] = {
                "match": get(r, "match"),
                "home_team": get(r, "homeTeam"),
                "away_team": get(r, "awayTeam"),
                "start": get(r, "startTime"),
                "kickoff": get(r, "kickoff"),
                "home_odds": to_float(get(r, "homeOver05")),
                "away_odds": to_float(get(r, "awayOver05")),
            }
    else:
        for r in rows:
            if get(r, "marketId") not in ("900304", "900305"):
                continue
            if get(r, "selection").lower() != "over 0.5":
                continue
            fx = fixtures.setdefault(get(r, "match"), {
                "match": get(r, "match"),
                "home_team": get(r, "homeTeam"),
                "away_team": get(r, "awayTeam"),
                "start": get(r, "startTime"),
                "kickoff": get(r, "kickoff"),
                "home_odds": None, "away_odds": None,
            })
            key = "home_odds" if get(r, "marketId") == "900304" else "away_odds"
            fx[key] = to_float(get(r, "odds"))

    out = []
    for fx in fixtures.values():
        home, away = fx["home_team"], fx["away_team"]
        if (not home or not away) and " vs " in fx["match"]:
            home, away = [s.strip() for s in fx["match"].split(" vs ", 1)]
        if not home or not away:
            continue
        fx["home"], fx["away"] = home, away
        fx["dt"] = parse_dt(fx["start"]) if fx["start"] else None
        out.append(fx)
    return out


# ---------------------------------------------------------------------------
# 2. resolve both teams (same method as five_min.py)
# ---------------------------------------------------------------------------
def first_team_only(cands):
    good = [c for c in cands if not NOT_FIRST_TEAM.search(str(c.get("name", "")))]
    return good or cands


def resolve(fx):
    home = ALIASES.get(fx["home"], fx["home"])
    away = ALIASES.get(fx["away"], fx["away"])

    if fx["dt"]:
        try:
            res, _why = resolve_fixture({"home": home, "away": away, "kickoff": fx["dt"]})
        except Exception:
            res = None
        if res:
            return res

    # fallback: best name match only (not confirmed against the fixture)
    try:
        ch, ca = first_team_only(candidates(home)), first_team_only(candidates(away))
        if ch and ca:
            page = team_page(ch[0]["id"])
            return {"home_id": str(ch[0]["id"]), "away_id": str(ca[0]["id"]),
                    "league_id": league_of(page), "verified": False}
    except Exception:
        pass
    return None


def missing_names(fx):
    out = []
    for name in (fx["home"], fx["away"]):
        try:
            if not candidates(ALIASES.get(name, name)):
                out.append(name)
        except Exception:
            out.append(name)
    return out


def fix_interactively(failed):
    """Ask for the right FotMob name for each team that could not be found."""
    fixed = []
    interactive = sys.stdin.isatty()
    if interactive:
        print("\nLet's fix the teams FotMob could not find.")
        print("Type the name as FotMob spells it (or Enter to skip). Fixes are saved to aliases.csv.\n")
    for fx in failed:
        for name in missing_names(fx):
            if not interactive:
                print(f"  '{name}' not found. Try:  python rank_cards.py --find=\"<fuller name>\"")
                continue
            while True:
                typed = input(f"  FotMob name for '{name}' (Enter to skip): ").strip()
                if not typed:
                    break
                try:
                    found = candidates(typed)
                except Exception:
                    found = []
                if not found:
                    print("    FotMob found nothing for that, try another spelling.")
                    continue
                print("    found: " + ", ".join(f"{c.get('name')} (id {c['id']})" for c in found[:3]))
                ALIASES[name] = typed
                save_alias(name, typed)
                break
        r = resolve(fx)
        if r:
            fixed.append((fx, r))
    return fixed


# ---------------------------------------------------------------------------
# 3. card + foul record per team id (last N competitive matches)
# ---------------------------------------------------------------------------
def pair(stats, key):
    v = stats.get(key)
    if v and len(v) == 2:
        return [num(v[0]), num(v[1])]
    return [None, None]


def find_ref(obj):
    """Referee name anywhere in a matchDetails payload (found by shape, like collect)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() == "referee":
                if isinstance(v, dict):
                    name = v.get("text") or v.get("name") or v.get("fullName")
                    if name:
                        return str(name).strip()
                elif isinstance(v, str) and v.strip():
                    return v.strip()
        for v in obj.values():
            got = find_ref(v)
            if got:
                return got
    elif isinstance(obj, list):
        for v in obj:
            got = find_ref(v)
            if got:
                return got
    return None


def match_stats(match_id):
    """(cards_home, cards_away, fouls_home, fouls_away, competition). Cached on disk."""
    key = str(match_id)
    c = match_cache.get(key)
    if c and "fouls" in c and "ref" in c:
        return c["cards"][0], c["cards"][1], c["fouls"][0], c["fouls"][1], c["comp"]

    data = client.get_json("/api/data/matchDetails", {"matchId": match_id}, use_cache=False)
    general = data.get("general") or {}
    comp = (general.get("leagueName") or general.get("parentLeagueName") or "Unknown").strip()

    periods = (((data.get("content") or {}).get("stats") or {}).get("Periods") or {})
    stats = collect(periods.get("All") or {}, {})

    yellow = pair(stats, "yellow_cards")
    red = pair(stats, "red_cards")
    cards = [None if yellow[i] is None else yellow[i] + (red[i] or 0.0) for i in (0, 1)]
    fouls = pair(stats, "fouls")

    total = None if None in cards else cards[0] + cards[1]
    match_cache[key] = {"cards": cards, "fouls": fouls, "comp": comp,
                        "ref": find_ref(data) or "", "tc": total}
    return cards[0], cards[1], fouls[0], fouls[1], comp


def league_pool(league_id):
    if league_id not in league_cache:
        try:
            league_cache[league_id] = league_matches(league_id)
        except Exception:
            league_cache[league_id] = []
    return league_cache[league_id]


def team_record(tid):
    try:
        page = team_page(tid)
        if not page:
            return None
        league_id = league_of(page)
        name = ((page.get("details") or {}).get("name")) or tid

        pool = {m["id"]: m for m in page_matches(page)
                if m["finished"] and not m["cancelled"]}
        if league_id:
            for m in league_pool(league_id):
                if (m["finished"] and not m["cancelled"]
                        and tid in (m["home_id"], m["away_id"])):
                    pool.setdefault(m["id"], m)

        ordered = sorted(pool.values(), key=lambda m: m["date"], reverse=True)

        cards_list, fouls_list = [], []
        for m in ordered:
            if len(cards_list) >= N:
                break
            try:
                ch, ca, fh, fa, comp = match_stats(m["id"])
            except Exception:
                continue
            if "friendl" in comp.lower():
                continue
            is_home = m["home_id"] == tid
            cards = ch if is_home else ca
            fouls = fh if is_home else fa
            if cards is None:
                continue
            cards_list.append(cards)
            if fouls is not None:
                fouls_list.append(fouls)

        played = len(cards_list)
        return {
            "name": name,
            "played": played,
            "hits": sum(1 for c in cards_list if c >= 1),
            "avg_cards": (sum(cards_list) / played) if played else 0.0,
            "avg_fouls": (sum(fouls_list) / len(fouls_list)) if fouls_list else None,
        }
    except Exception as e:
        print(f"  WARNING: team {tid} failed: {e}")
        return None


def smoothed_p(rec):
    """Probability the team gets at least 1 card: hit rate pulled toward PRIOR_RATE.
    This is the number the edge is measured with."""
    return (rec["hits"] + PRIOR_RATE * PRIOR_WEIGHT) / (rec["played"] + PRIOR_WEIGHT)


def score_of(rec):
    return (smoothed_p(rec) * 100
            + W_CARDS * rec["avg_cards"]
            + W_FOULS * (rec["avg_fouls"] or 0.0))


def rank_key(r):
    """Sort key for ranking and for choosing the better team inside a match."""
    edge = r["edge"] if r["edge"] is not None else -9.0
    if SORT == "score":
        return (r["score"], edge)
    return (edge, r["score"])


def fixture_ref(item):
    """Referee for the upcoming fixture, or None if FotMob has not announced one."""
    fx, r = item
    try:
        page = team_page(r["home_id"])
        upcoming = [m for m in page_matches(page)
                    if not m["finished"] and not m["cancelled"]
                    and m["home_id"] == r["home_id"] and m["away_id"] == r["away_id"]]
        if not upcoming:
            return None
        if fx["dt"]:
            upcoming.sort(key=lambda m: abs((m["date"] - fx["dt"]).total_seconds()))
        data = client.get_json("/api/data/matchDetails",
                               {"matchId": upcoming[0]["id"]}, use_cache=False)
        return find_ref(data)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------
if not os.path.exists(CSV_PATH):
    print(f"Can't find {CSV_PATH}. Save the CSV in this folder as foul.csv.")
    sys.exit(1)

fixtures = load_fixtures(CSV_PATH)
if not fixtures:
    print(f"No usable rows in {CSV_PATH}.")
    sys.exit(1)

if not any(fx["dt"] for fx in fixtures):
    print("NOTE: this CSV has no startTime column, so fixtures can't be verified.")
    print("      Re-run the latest browser script and use that CSV for verified names.\n")

print(f"{len(fixtures)} fixtures loaded. Step 1/3: matching teams on FotMob")
with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    resolved = list(ex.map(resolve, fixtures))

ok = [(fx, r) for fx, r in zip(fixtures, resolved) if r]
failed_fx = [fx for fx, r in zip(fixtures, resolved) if not r]
print(f"  verified: {sum(1 for _, r in ok if r['verified'])} | "
      f"name-match only: {sum(1 for _, r in ok if not r['verified'])} | not found: {len(failed_fx)}")

if failed_fx:
    for fx in failed_fx:
        names = ", ".join(f"'{n}'" for n in missing_names(fx)) or "(see below)"
        print(f"  NOT FOUND: {fx['match']}   -> FotMob has no match for {names}")
    ok += fix_interactively(failed_fx)

if not ok:
    sys.exit(1)

team_ids = sorted({r[k] for _, r in ok for k in ("home_id", "away_id")})
print(f"\nStep 2/3: card and foul records for {len(team_ids)} teams (last {N} competitive matches each)")
with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    records = dict(zip(team_ids, ex.map(team_record, team_ids)))
save_cache(match_cache)

print("Looking up referees")
with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    refs = list(ex.map(fixture_ref, ok))
ref_cards = defaultdict(list)
for c in match_cache.values():
    if c.get("ref") and c.get("tc") is not None and "friendl" not in str(c.get("comp", "")).lower():
        ref_cards[c["ref"]].append(c["tc"])

_all_tc = [c["tc"] for c in match_cache.values()
           if c.get("tc") is not None and "friendl" not in str(c.get("comp", "")).lower()]
league_avg = mean(_all_tc) if _all_tc else 0.0
print(f"  average cards per match across everything read: {league_avg:.2f}")

print(f"Step 3/3: ranking by {SORT} (one pick per match)")
rows = []
for (fx, r), ref in zip(ok, refs):
    ref_list = ref_cards.get(ref) if ref else None
    for side, tid_key, odds_key, team, opp in (
        ("Home", "home_id", "home_odds", fx["home"], fx["away"]),
        ("Away", "away_id", "away_odds", fx["away"], fx["home"]),
    ):
        odds = fx[odds_key]
        if odds is None:
            continue
        rec = records.get(r[tid_key])
        row = {"team": team, "opponent": opp, "side": side, "match": fx["match"],
               "kickoff": fx["kickoff"], "odds": odds, "verified": r["verified"],
               "resolved": rec["name"] if rec else "NOT FOUND",
               "played": 0, "hits": 0, "hit_rate": None, "avg_cards": None,
               "avg_fouls": None, "score": -1.0, "implied": 1 / odds,
               "p_model": None,
               "edge": None, "flags": [], "ref_adj": 0.0, "ref": ref or "",
               "ref_avg": mean(ref_list) if ref_list else None,
               "ref_n": len(ref_list) if ref_list else 0}
        if rec and rec["played"]:
            p = smoothed_p(rec)
            row.update(played=rec["played"], hits=rec["hits"],
                       hit_rate=rec["hits"] / rec["played"],
                       avg_cards=rec["avg_cards"], avg_fouls=rec["avg_fouls"],
                       p_model=p, score=score_of(rec))
            # a referee with only a game or two is noise: show him, but don't let him move the score
            if row["ref_avg"] is not None and row["ref_n"] >= REF_MIN_GAMES:
                conf = row["ref_n"] / (row["ref_n"] + REF_K)
                row["ref_adj"] = W_REF * (row["ref_avg"] - league_avg) * conf
                row["score"] += row["ref_adj"]
            # edge uses the smoothed probability, not the raw hit rate
            row["edge"] = p - row["implied"]
            if rec["played"] < SMALL_SAMPLE:
                row["flags"].append(f"SMALL SAMPLE ({rec['played']})")
        else:
            row["flags"].append("NO DATA")
        if not r["verified"]:
            row["flags"].append("UNVERIFIED NAME")
        rows.append(row)

# keep the better team of each match (by edge, or by score with --sort=score)
by_match = {}
for row in rows:
    by_match.setdefault(row["match"], []).append(row)

picks = []
for match, group in by_match.items():
    group.sort(key=rank_key, reverse=True)
    best = group[0]
    other = group[1] if len(group) > 1 else None
    best["other"] = other
    picks.append(best)

picks.sort(key=rank_key, reverse=True)

candidates_shown = picks
if MIN_EDGE is not None:
    candidates_shown = [p for p in picks
                        if p["edge"] is not None and p["edge"] * 100 >= MIN_EDGE]
shown = candidates_shown[:TOP] if TOP else candidates_shown


def other_text(r):
    o = r.get("other")
    if not o:
        return "(only this side has the market)"
    if o["hit_rate"] is None:
        return f"{o['team'][:16]} (no data)"
    return f"{o['team'][:16]} {o['hits']}/{o['played']}, {o['score']:.0f}"


def ref_text(r):
    if not r["ref"]:
        return "not announced yet"
    if r["ref_avg"] is None:
        return f"{r['ref'][:16]} (no history)"
    return f"{r['ref'][:14]} {r['ref_avg']:.1f} ({r['ref_n']}) {r['ref_adj']:+.1f}"


def f1(x):
    return "-" if x is None else f"{x:.1f}"


hdr = ["rank", "pick", "fotmob_name", "opponent", "side", "match", "kickoff", "odds",
       "hits", "played", "hit_rate", "avg_cards", "avg_fouls", "score", "implied",
       "edge", "referee", "ref_avg_cards_per_match", "ref_games", "ref_adjust", "verified",
       "other_side", "flags", "p_model"]    # p_model is last so older readers keep working


def out_row(rank, r, with_other):
    return [
        rank, r["team"], r["resolved"], r["opponent"], r["side"], r["match"], r["kickoff"],
        r["odds"], r["hits"], r["played"],
        "" if r["hit_rate"] is None else round(r["hit_rate"], 3),
        "" if r["avg_cards"] is None else round(r["avg_cards"], 2),
        "" if r["avg_fouls"] is None else round(r["avg_fouls"], 1),
        round(r["score"], 1), round(r["implied"], 3),
        "" if r["edge"] is None else round(r["edge"], 3),
        r["ref"], "" if r["ref_avg"] is None else round(r["ref_avg"], 2), r["ref_n"],
        round(r["ref_adj"], 1),
        "yes" if r["verified"] else "no",
        other_text(r) if with_other else "",
        " | ".join(r["flags"]),
        "" if r["p_model"] is None else round(r["p_model"], 4),
    ]


with open(OUT_PICKS, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(hdr)
    for idx, r in enumerate(picks, 1):
        w.writerow(out_row(idx, r, True))

with open(OUT_ALL, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(hdr)
    for idx, r in enumerate(sorted(rows, key=rank_key, reverse=True), 1):
        w.writerow(out_row(idx, r, False))

# ---- final report: the top picks, printed last ------------------------------
W = 180
sort_note = "edge (smoothed probability minus price), best first" if SORT != "score" \
    else "score, best first"
if MIN_EDGE is not None:
    sort_note += f" | only edge >= {MIN_EDGE:+.1f} pts"
lines = ["=" * W,
         f"TOP {len(shown)} PICKS (one per match, sorted by {sort_note})",
         "=" * W,
         f"{'#':<4}{'PICK':<21}{'VS':<19}{'SIDE':<6}{'ODDS':>5}{'HIT':>7}{'HIT%':>6}{'P%':>7}"
         f"{'CARDS':>7}{'FOULS':>7}{'SCORE':>7}{'EDGE':>8}  {'REFEREE (avg, games, score adj)':<32}"
         f"{'OTHER SIDE (hit, score)':<32}FLAGS",
         "-" * W]
for idx, r in enumerate(shown, 1):
    flags = " | ".join(r["flags"])
    if r["hit_rate"] is None:
        lines.append(f"{idx:<4}{r['team'][:20]:<21}{r['opponent'][:18]:<19}{r['side']:<6}"
                     f"{r['odds']:>5.2f}{'-':>7}  {flags}")
        continue
    lines.append(
        f"{idx:<4}{r['team'][:20]:<21}{r['opponent'][:18]:<19}{r['side']:<6}"
        f"{r['odds']:>5.2f}{str(r['hits']) + '/' + str(r['played']):>7}"
        f"{r['hit_rate'] * 100:>5.0f}%{r['p_model'] * 100:>6.1f}%"
        f"{r['avg_cards']:>7.2f}{f1(r['avg_fouls']):>7}"
        f"{r['score']:>7.1f}{r['edge'] * 100:>+7.1f}%  {ref_text(r):<32}"
        f"{other_text(r):<32}{flags}")
lines += ["-" * W,
          "HIT = games where the team got at least 1 card | P% = smoothed chance of a card "
          f"(hit rate pulled toward {PRIOR_RATE * 100:.0f}% with weight {PRIOR_WEIGHT}) | "
          "EDGE = P% minus the price's implied probability",
          "REFEREE = his average total cards per match, (games we have), and the points added or removed "
          f"from the score. Referees with fewer than {REF_MIN_GAMES} games are shown but not counted.",
          "Score weights are first guesses (top of the script). The referee adjustment is the same for both "
          "teams in a match, so it changes the ranking between matches, not the pick inside one."]

with open(TOP_FILE, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")

print(f"\nSaved: {OUT_PICKS} (one pick per match, {len(picks)} matches), {OUT_ALL} (every team), {TOP_FILE}")
print("Cards = yellow + red.\n")
print("\n".join(lines))
print("DONE")