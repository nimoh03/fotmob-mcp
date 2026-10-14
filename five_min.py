"""5-minute market research.

Finds the matches most likely to have NO goal in the first 5 minutes
(the Draw in SportyBet's '5-minute 1X2' market).

  python five_min.py five_slate.csv
  options:  --per-league=100   league matches to read per league (smaller = faster test)
            --matches=25       most recent matches used per team
            --fresh            forget saved goal minutes and start over
            --min-edge=1       revised shortlist: smallest edge in points (default 1.0)
            --min-matches=8    revised shortlist: matches needed for BOTH teams (default 8)
            --genk-pct=0.80    revised shortlist: drop games the market ranks in its riskiest
                               fifth (default 0.80; use 1 to switch this rule off)

v2 (after the Oct 10 post-mortem): besides the full report, the script now prints and saves a
REVISED SHORTLIST (reports/five/five_revised_latest.txt). A game must have
  - an edge of at least +1.0 pts,
  - at least 8 matches of data for both teams,
  - a fixture confirmed on FotMob,
  - and must not be "Genk-like" (we like it but the market doesn't, or the market ranks it in
    its riskiest fifth).
The shortlist is sorted calmest first, NOT by edge: the biggest edges come from games where the
market charges more for the draw, so ranking by edge would pick exactly the games the market
is wary of. The main report file keeps its old format, so postmortem.py still reads it.
"""
import csv
import json
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fotmob_mcp.client import FotMobClient, FotMobUnavailable
from nts_context import style_tables, team_formation
from nts_opponent import league_context, load_league, team_profile
from nts_resolve import GENERIC, norm, search_teams, team_match

client = FotMobClient()

OUT_DIR = Path("reports") / "five"
CACHE_FILE = OUT_DIR / "goals_cache.json"
DEBUG_FILE = OUT_DIR / "debug_matchdetails.json"

EARLY = 5            # "a goal in the first 5 minutes" = a goal in minute 5 or earlier
SHRINK = 60          # pseudo-matches pulling a team's rate toward its league's rate (early goals are rare, so strong)
LEAGUE_SHRINK = 100  # pseudo team-matches pulling a league's rate toward the all-leagues rate
MARKET_WEIGHT = 0.5  # how much of the draw price's own view of the match (mismatch, lineups) is mixed in
WORKERS = 3
LAGOS = timezone(timedelta(hours=1))
SMALL_SAMPLE = 6     # fewer matches than this for either team = small sample
LAST_N = 25          # set from --matches
MIN_EDGE_PTS = 1.0   # revised shortlist: smallest edge (points) worth backing; set from --min-edge
MIN_MATCHES = 8      # revised shortlist: both teams need at least this many matches; set from --min-matches
GENK_PCT = 0.80      # revised shortlist: market rank beyond this share of the slate = Genk-like; set from --genk-pct
_debug_saved = False


# ---------- small helpers ----------

def parse_dt(s):
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def parse_score(txt):
    m = re.search(r"(\d+)\s*[-:]\s*(\d+)", str(txt or ""))
    return (int(m.group(1)), int(m.group(2))) if m else None


def read_slate(path):
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            try:
                h, x, a = float(r["homeOdds"]), float(r["drawOdds"]), float(r["awayOdds"])
            except (KeyError, TypeError, ValueError):
                continue
            ko = parse_dt(r.get("startTime"))
            if ko is None or not r.get("homeTeam") or not r.get("awayTeam"):
                continue
            rows.append({
                "category": r.get("category", ""), "tournament": r.get("tournament", ""),
                "home": r["homeTeam"].strip(), "away": r["awayTeam"].strip(),
                "kickoff": ko, "h": h, "x": x, "a": a,
            })
    return rows


# ---------- FotMob fixtures (found by shape, so small layout changes don't break us) ----------

def find_matches(obj, out):
    """Collect every dict that looks like a fixture: id + home{} + away{} + status{}."""
    if isinstance(obj, dict):
        if (obj.get("id") is not None and isinstance(obj.get("home"), dict)
                and isinstance(obj.get("away"), dict) and isinstance(obj.get("status"), dict)):
            out.append(obj)
        for v in obj.values():
            find_matches(v, out)
    elif isinstance(obj, list):
        for v in obj:
            find_matches(v, out)
    return out


def norm_match(m):
    st, h, a = m.get("status") or {}, m["home"], m["away"]
    date = parse_dt(st.get("utcTime"))
    if date is None:
        return None
    score = parse_score(st.get("scoreStr"))
    if score is None:
        hs, as_ = h.get("score"), a.get("score")
        if isinstance(hs, int) and isinstance(as_, int):
            score = (hs, as_)
    return {
        "id": str(m["id"]),
        "home_id": str(h.get("id")), "away_id": str(a.get("id")),
        "home": h.get("name") or h.get("longName") or h.get("shortName") or "",
        "away": a.get("name") or a.get("longName") or a.get("shortName") or "",
        "date": date, "finished": bool(st.get("finished")), "cancelled": bool(st.get("cancelled")),
        "score": score,
    }


def page_matches(page):
    seen, out = set(), []
    for m in find_matches(page, []):
        nm = norm_match(m)
        if nm and nm["id"] not in seen:
            seen.add(nm["id"])
            out.append(nm)
    return out


def team_page(tid):
    for params in ({"id": str(tid)}, {"id": str(tid), "ccode3": "NGA"}):
        try:
            return client.get_json("/api/data/teams", params)
        except FotMobUnavailable:
            continue
    return None


def league_of(page):
    return ((page or {}).get("details") or {}).get("primaryLeagueId")


def league_matches(league_id):
    try:
        payload = load_league(league_id)
    except Exception:
        return []
    src = payload.get("fixtures") if isinstance(payload, dict) and payload.get("fixtures") else payload
    return page_matches(src)


def search_terms(name):
    """Several ways to search for one club name: full, without FC/CS style words, first two words, longest word."""
    toks = [t for t in norm(name).split() if t not in GENERIC]
    terms = [name]
    if toks:
        terms.append(" ".join(toks))
        if len(toks) > 2:
            terms.append(" ".join(toks[:2]))
        terms.append(max(toks, key=len))
    out = []
    for t in terms:
        if t and t not in out:
            out.append(t)
    return out


def candidates(name):
    out, seen = [], set()
    for term in search_terms(name):
        try:
            found = search_teams(term)
        except Exception:
            continue
        for c in found:
            cid = str(c["id"])
            if cid not in seen and team_match(name, c.get("name", "")):
                seen.add(cid)
                out.append(c)
    return out


def find_fixture(page, cid, other_name, ko):
    """The other team's id, if this team's page shows the fixture (same two teams, within 36h of kickoff)."""
    cid = str(cid)
    for m in page_matches(page):
        if abs((m["date"] - ko).total_seconds()) > 36 * 3600:
            continue
        if m["home_id"] == cid and team_match(other_name, m["away"]):
            return m["away_id"]
        if m["away_id"] == cid and team_match(other_name, m["home"]):
            return m["home_id"]
    return None


def resolve_fixture(row):
    """Find both teams on FotMob. Verified = the fixture itself shows up on a team's page.
    If one club's name is not found by search, the other club's page can still lead us to it."""
    ko = row["kickoff"]
    cands_h = candidates(row["home"])
    cands_a = candidates(row["away"])
    for cand in cands_h[:8]:
        page = team_page(cand["id"])
        other = find_fixture(page, cand["id"], row["away"], ko) if page else None
        if other:
            return {"home_id": str(cand["id"]), "away_id": other,
                    "league_id": league_of(page), "verified": True}, ""
    for cand in cands_a[:8]:
        page = team_page(cand["id"])
        other = find_fixture(page, cand["id"], row["home"], ko) if page else None
        if other:
            return {"home_id": other, "away_id": str(cand["id"]),
                    "league_id": league_of(page), "verified": True}, ""
    if len(cands_h) == 1 and len(cands_a) == 1:
        page = team_page(cands_h[0]["id"])
        return {"home_id": str(cands_h[0]["id"]), "away_id": str(cands_a[0]["id"]),
                "league_id": league_of(page), "verified": False}, ""
    return None, (f"could not confirm the fixture (possible teams found: {len(cands_h)} for "
                  f"'{row['home']}', {len(cands_a)} for '{row['away']}')")


# ---------- goal minutes (saved forever once read: finished matches never change) ----------

def parse_goals(data):
    """[[minute, 1 if home else 0], ...] sorted by minute."""
    try:
        events = data["content"]["matchFacts"]["events"]["events"]
    except (KeyError, TypeError):
        events = None
    goals, seen = [], set()

    def take(o):
        if str(o.get("type") or "").lower() not in ("goal", "owngoal") or "isHome" not in o:
            return
        t = o.get("time")
        if not isinstance(t, (int, float)):
            mt = re.match(r"\d+", str(o.get("timeStr") or ""))
            t = int(mt.group(0)) if mt else None
        if t is None:
            return
        who = o.get("playerId") or (o.get("player") or {}).get("id") or o.get("nameStr") or ""
        key = (int(t), bool(o.get("isHome")), str(who), str(o.get("overloadTime")))
        if key in seen:
            return
        seen.add(key)
        goals.append([int(t), 1 if o.get("isHome") else 0])

    def walk(o):
        if isinstance(o, dict):
            take(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(events if events is not None else data)
    goals.sort()
    return goals


def fetch_record(m):
    global _debug_saved
    data = None
    for attempt in range(3):
        try:
            data = client.get_json("/api/data/matchDetails", {"matchId": m["id"]}, use_cache=False)
            break
        except FotMobUnavailable:
            time.sleep(1 + attempt * 2)
    if data is None:
        return None
    goals = parse_goals(data)
    sc = m["score"]
    ok = sc is None or len(goals) == sc[0] + sc[1]
    if not ok and not _debug_saved:
        _debug_saved = True
        try:
            DEBUG_FILE.write_text(json.dumps(data, ensure_ascii=False)[:400000], encoding="utf-8")
        except Exception:
            pass
    return {"h": m["home_id"], "a": m["away_id"], "score": list(sc) if sc else None,
            "goals": goals, "ok": ok, "d": m["date"].isoformat()}


def load_cache():
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def save_cache(cache):
    tmp = CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CACHE_FILE)


# ---------- the model ----------

def build_index(cache):
    """team id -> [(date, scored early, conceded early, match id)] from saved matches."""
    index = defaultdict(list)
    for mid, rec in cache.items():
        if not rec.get("ok") or not rec.get("d"):
            continue
        d = parse_dt(rec["d"])
        eh = any(g[0] <= EARLY and g[1] == 1 for g in rec["goals"])
        ea = any(g[0] <= EARLY and g[1] == 0 for g in rec["goals"])
        nh = any(EARLY < g[0] <= 10 and g[1] == 1 for g in rec["goals"])
        na = any(EARLY < g[0] <= 10 and g[1] == 0 for g in rec["goals"])
        index[rec["h"]].append((d, eh, ea, mid, nh, na))
        index[rec["a"]].append((d, ea, eh, mid, na, nh))
    return index


def team_stats(tid, ko, index):
    rows = sorted((r for r in index.get(tid, []) if r[0] < ko), key=lambda r: r[0], reverse=True)[:LAST_N]
    return {"n": len(rows), "for5": sum(r[1] for r in rows), "ag5": sum(r[2] for r in rows),
            "for10": sum(r[4] for r in rows), "ag10": sum(r[5] for r in rows)}


def league_rate(ids, before, cache):
    """matches, early goals scored per side summed, matches with any early goal."""
    n = s = a = 0
    for mid in ids:
        rec = cache.get(mid)
        if not rec or not rec.get("ok") or not rec.get("d") or parse_dt(rec["d"]) >= before:
            continue
        eh = any(g[0] <= EARLY and g[1] == 1 for g in rec["goals"])
        ea = any(g[0] <= EARLY and g[1] == 0 for g in rec["goals"])
        n += 1
        s += int(eh) + int(ea)
        a += int(eh or ea)
    return n, s, a


def shrunk(k, n, base):
    return (k + SHRINK * base) / (n + SHRINK)


def rank_in(vals, tid):
    if not vals or tid not in vals:
        return None
    v = vals[tid]
    return v, 1 + sum(1 for x in vals.values() if x > v), len(vals)


def style_info(league_id, home_id, away_id):
    """Small adjustment for attack, tempo and defence quality (first-guess weights) plus text for the report."""
    factor, notes = 1.0, []
    info = {"home": [], "away": []}
    try:
        tabs = style_tables(league_id) if league_id else {}
    except Exception:
        tabs = {}
    try:
        tables = league_context(league_id)[0] if league_id else {}
    except Exception:
        tables = {}
    atk_vals = tabs.get("xg") or tabs.get("goals")
    atk_unit = "xG" if tabs.get("xg") else "goals"
    for side, tid in (("home", home_id), ("away", away_id)):
        got = rank_in(atk_vals, tid)
        if got:
            v, rk, n = got
            info[side].append(f"attack {v:.2f} {atk_unit}/game (rank {rk} of {n}, 1 = most)")
            if rk <= n / 4:
                factor += 0.05
                notes.append(f"{side} team attacks a lot")
        got = rank_in(tabs.get("long"), tid)
        if got and got[1] <= got[2] / 4:
            v, rk, n = got
            info[side].append(f"plays direct ({v:.0f} long balls/game, rank {rk} of {n})")
            factor += 0.03
            notes.append(f"{side} team plays direct")
        try:
            prof = team_profile(tid, tables) if tables else None
        except Exception:
            prof = None
        if prof:
            use_xg = prof["xgc_rank"] is not None
            rk = prof["xgc_rank"] if use_xg else prof["gc_rank"]
            val = prof["xgc_pm"] if use_xg else prof["gc_pm"]
            n = prof["n_teams"]
            if rk is not None and val is not None:
                what = "xG conceded" if use_xg else "goals conceded"
                info[side].append(f"defence {val:.2f} {what}/game (rank {rk} of {n}, 1 = tightest)")
                if rk > n * 3 / 4:
                    factor += 0.05
                    notes.append(f"{side} defence is leaky")
                elif rk <= n / 4:
                    factor -= 0.05
                    notes.append(f"{side} defence is tight")
        try:
            form = team_formation(tid)
        except Exception:
            form = None
        if form:
            info[side].append(f"last formation {form}")
    return max(0.8, min(1.3, factor)), notes, info


def actual_for(fx, cache):
    ko = fx["kickoff"]
    for rec in cache.values():
        if not rec.get("ok") or not rec.get("d"):
            continue
        if {rec["h"], rec["a"]} != {fx["home_id"], fx["away_id"]}:
            continue
        if abs((parse_dt(rec["d"]) - ko).total_seconds()) > 36 * 3600:
            continue
        goals = rec["goals"]
        return {"first": goals[0][0] if goals else None,
                "early": any(g[0] <= EARLY for g in goals), "score": rec.get("score")}
    return None


# ---------- the revised shortlist (rules from the Oct 10 post-mortem) ----------

def screen(entries):
    """Mark each game as passing or failing the revised rules, and say why it failed.
    Uses the unrounded edge, so a game printed as +1.0 may fall just either side of the line."""
    total = len(entries)
    for e in entries:
        why = []
        edge_pts = e["edge"] * 100
        if edge_pts < MIN_EDGE_PTS:
            why.append(f"edge {edge_pts:+.1f} is under +{MIN_EDGE_PTS:.1f}")
        if any("WE LIKE IT" in f for f in e["flags"]):
            why.append("Genk-like: we like it, the market doesn't")
        elif e["market_rank"] > total * GENK_PCT:
            why.append(f"Genk-like: market ranks it {e['market_rank']} of {total} (riskiest fifth)")
        n = min(e["h"]["n"], e["a"]["n"])
        if n < MIN_MATCHES:
            why.append(f"only {n} matches of data for one team")
        if not e["fx"]["verified"]:
            why.append("fixture not confirmed on FotMob")
        e["screen_why"] = why
        e["screen_ok"] = not why


def revised_lines(entries):
    """The shortlist after the rules above, calmest first. Kept out of the main report so
    postmortem.py reads that file exactly as before."""
    keep = [e for e in entries if e["screen_ok"]]
    total = len(entries)
    out = [f"REVISED SHORTLIST: {len(keep)} of {total} games pass "
           f"(edge >= {MIN_EDGE_PTS:+.1f} pts, both teams >= {MIN_MATCHES} matches, fixture confirmed, "
           f"not Genk-like). Sorted calmest first, NOT by edge."]
    for i, e in enumerate(keep, 1):
        fx = e["fx"]
        out.append(f"{i:>2}. {fx['kickoff']:%a %H:%M}Z ({fx['kickoff'].astimezone(LAGOS):%H:%M} Lagos) "
                   f"{fx['home']} vs {fx['away']} | draw {fx['x']} | ours {e['ours']:.1%} "
                   f"| edge {e['edge'] * 100:+.1f} | model #{e['rank']} | market rank {e['market_rank']}")
    dropped = [e for e in entries[:18] if not e["screen_ok"]]
    if dropped:
        out.append("")
        out.append(f"DROPPED FROM THE MODEL'S TOP 18 ({len(dropped)}):")
        for e in dropped:
            fx = e["fx"]
            out.append(f"  model #{e['rank']} {fx['home']} vs {fx['away']}: " + "; ".join(e["screen_why"]))
    out.append("")
    out.append("Sorted by probability, not edge: edge is biggest where the market charges more for the draw, "
               "so ranking by edge pulls in the games the market is wary of. Edge is used as a minimum only.")
    return out


# ---------- report text ----------

def fmt_ko(ko):
    return f"{ko:%a %d %b %H:%M} UTC ({ko.astimezone(LAGOS):%H:%M} Lagos)"


def team_line(label, name, st, info):
    if st["n"]:
        parts = [f"early goals scored {st['for5']}/{st['n']}, conceded {st['ag5']}/{st['n']} "
                 f"(last {st['n']} matches; minutes 6-10: scored {st['for10']}, conceded {st['ag10']})"]
    else:
        parts = ["no goal-minute data for this team"]
    parts += info
    return f"{label} {name}: " + " | ".join(parts)


def actual_text(act):
    if act is None:
        return None
    sc = f" (final {act['score'][0]}-{act['score'][1]})" if act.get("score") else ""
    if act["early"]:
        return f"RESULT: goal in the first 5 minutes (first goal {act['first']}'){sc} -> the draw LOST"
    first = f"first goal {act['first']}'" if act["first"] is not None else "no goals at all"
    return f"RESULT: no goal in the first 5 minutes ({first}){sc} -> the draw WON"


def block(e):
    fx = e["fx"]
    lines = [
        f"{e['rank']:>2}. {fmt_ko(fx['kickoff'])} | {fx['category']} / {fx['tournament']}",
        f"    {fx['home']} vs {fx['away']}",
        f"    price: draw {fx['x']} (home {fx['h']}, away {fx['a']}) -> needs {e['breakeven']:.1%} no-goal to break even "
        f"| market rank {e['market_rank']} of {e['total']} (1 = calmest in the market's eyes)",
        f"    our estimate: goal in first 5 min {e['p_final']:.1%} -> no goal {e['ours']:.1%} "
        f"| edge {e['edge'] * 100:+.1f} pts | fair draw price {1 / e['ours']:.2f}",
        "    " + team_line("home", fx["home"], e["h"], e["info"]["home"]),
        "    " + team_line("away", fx["away"], e["a"], e["info"]["away"]),
        f"    base rate used: {e['src']}, {e['base']:.1%} per team per match scores in the first 5 min; "
        f"style adjustment x{e['factor']:.2f}" + (f" ({', '.join(e['notes'])})" if e["notes"] else "")
        + f"; market adjustment x{e['mkt']:.2f} (draw price {fx['x']} vs slate average {e['avg_price']:.2f})",
    ]
    if e["flags"]:
        lines.append("    FLAGS: " + " | ".join(e["flags"]))
    t = actual_text(e["actual"])
    if t:
        lines.append("    " + t)
    return lines


def one_liner(e):
    fx = e["fx"]
    flags = f" | {' | '.join(e['flags'])}" if e["flags"] else ""
    res = ""
    if e["actual"] is not None:
        res = " | " + ("LOST" if e["actual"]["early"] else "won")
    return (f"{e['rank']:>2}. {fx['kickoff']:%a %H:%M}Z {fx['home']} vs {fx['away']} | draw {fx['x']} "
            f"| ours {e['ours']:.1%} vs needs {e['breakeven']:.1%} ({e['edge'] * 100:+.1f}){flags}{res}")


def backtest_lines(entries):
    known = [e for e in entries if e["actual"] is not None]
    if not known:
        return []
    n = len(known)
    won = sum(1 for e in known if not e["actual"]["early"])
    out = [f"RESULT CHECK (matches already played, goal minutes read from FotMob): "
           f"the draw won {won} of {n} ({won / n:.0%})."]
    if n >= 9:
        by_ours = sorted(known, key=lambda e: e["p_final"])
        by_market = sorted(known, key=lambda e: e["fx"]["x"])
        k = n // 3

        def rate(group):
            return f"{sum(1 for e in group if not e['actual']['early'])}/{len(group)}"

        out.append(f"  our calmest third: {rate(by_ours[:k])} | our riskiest third: {rate(by_ours[-k:])}")
        out.append(f"  market's calmest third (lowest draw odds): {rate(by_market[:k])} | "
                   f"market's riskiest third: {rate(by_market[-k:])}")
    out.append("  (a small sample, and the style rankings are today's so this check leaks a little: "
               "it shows the data reads correctly, it does not prove the model works)")
    return out


# ---------- the run ----------

def main():
    global LAST_N, MIN_EDGE_PTS, MIN_MATCHES, GENK_PCT
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    opts = {a.split("=")[0]: (a.split("=", 1)[1] if "=" in a else "1") for a in sys.argv[1:] if a.startswith("--")}
    path = args[0] if args else "five_slate.csv"
    per_league = int(opts.get("--per-league", 100))
    LAST_N = int(opts.get("--matches", 25))
    MIN_EDGE_PTS = float(opts.get("--min-edge", MIN_EDGE_PTS))
    MIN_MATCHES = int(opts.get("--min-matches", MIN_MATCHES))
    GENK_PCT = float(opts.get("--genk-pct", GENK_PCT))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if "--fresh" in opts and CACHE_FILE.exists():
        CACHE_FILE.unlink()
    try:
        rows = read_slate(path)
    except FileNotFoundError:
        print(f"Can't find {path}. Save the 5-minute export in this folder (name it five_slate.csv).")
        return
    if not rows:
        print(f"No usable rows in {path}. I expect the columns: homeTeam, awayTeam, startTime, "
              f"homeOdds, drawOdds, awayOdds.")
        return

    t0 = time.time()
    tty = sys.stdout.isatty()
    print(f"{len(rows)} fixtures. Step 1/3: finding the teams on FotMob")
    fixtures, unresolved = [], []
    for i, row in enumerate(rows, 1):
        if tty:
            print(f"\r  {i}/{len(rows)}  {row['home'][:28]:<28}", end="", flush=True)
        elif i % 10 == 0 or i == len(rows):
            print(f"  teams {i}/{len(rows)}", flush=True)
        try:
            res, why = resolve_fixture(row)
        except Exception as e:
            res, why = None, f"lookup failed: {e}"
        if res:
            fixtures.append({**row, **res})
        else:
            unresolved.append((row, why))
    if tty:
        print("\r" + " " * 60 + "\r", end="")
    if not fixtures:
        print("No fixture could be matched to FotMob teams.")
        for row, why in unresolved[:5]:
            print(f"  {row['home']} vs {row['away']}: {why}")
        return

    # which matches do we need goal minutes for?
    by_league = defaultdict(list)
    for f in fixtures:
        by_league[f["league_id"]].append(f)
    pool, league_ids, notes = {}, {}, []
    for lid, fx in by_league.items():
        if lid is None:
            notes.append("a league id was missing for some fixtures, using team pages only for them")
            continue
        cutoff = max(f["kickoff"] for f in fx) + timedelta(hours=36)
        ms = [m for m in league_matches(lid)
              if m["finished"] and not m["cancelled"] and m["date"] < cutoff]
        if not ms:
            notes.append(f"league {lid}: no finished matches found in the league data")
        ms.sort(key=lambda m: m["date"], reverse=True)
        ms = ms[:per_league]
        league_ids[lid] = [m["id"] for m in ms]
        for m in ms:
            pool[m["id"]] = m
    for f in fixtures:  # each team's own recent matches (covers national teams and cups)
        for tid in (f["home_id"], f["away_id"]):
            page = team_page(tid)
            for m in page_matches(page) if page else []:
                if m["finished"] and not m["cancelled"] and m["date"] < f["kickoff"] + timedelta(hours=36):
                    pool.setdefault(m["id"], m)

    cache = load_cache()
    todo = [m for mid, m in pool.items() if mid not in cache]
    print(f"Step 2/3: goal minutes. {len(pool)} matches needed, {len(pool) - len(todo)} already saved, "
          f"{len(todo)} to read now")
    failed = 0
    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            for i, (m, rec) in enumerate(zip(todo, ex.map(fetch_record, todo)), 1):
                if rec is None:
                    failed += 1
                else:
                    cache[m["id"]] = rec
                if i % 25 == 0 or i == len(todo):
                    msg = f"{i}/{len(todo)} read ({failed} failed)"
                    if tty:
                        print("\r  " + msg, end="", flush=True)
                    else:
                        print("  " + msg, flush=True)
                if i % 100 == 0:
                    save_cache(cache)
                if failed >= 30 and i == failed:
                    print("\nEvery request is failing, FotMob may be refusing us. Wait a few minutes and run again.")
                    break
    except KeyboardInterrupt:
        print("\nStopped by you. What was read is saved, run again to continue.")
    save_cache(cache)
    if tty:
        print()

    ok_ids = [mid for mid in pool if cache.get(mid, {}).get("ok")]
    bad_ids = [mid for mid in pool if mid in cache and not cache[mid].get("ok")]
    if not ok_ids:
        print("Could not read goal minutes from any match.")
        if DEBUG_FILE.exists():
            print(f"I saved one raw match to {DEBUG_FILE}. Paste me its first 60 lines.")
        return

    # base rates
    index = build_index(cache)
    g_n = g_s = g_a = 0
    for ids in league_ids.values():
        n, s, a = league_rate(ids, datetime.max.replace(tzinfo=timezone.utc), cache)
        g_n, g_s, g_a = g_n + n, g_s + s, g_a + a
    glob = (g_n, g_s, g_a)

    g_rate = (g_s / (2 * g_n)) if g_n else 0.035
    avg_q = sum(1 - 1 / f["x"] for f in fixtures) / len(fixtures)
    avg_price = 1 / (1 - avg_q)
    entries = []
    for fx in fixtures:
        n_l, s_l, a_l = league_rate(league_ids.get(fx["league_id"], []), fx["kickoff"], cache)
        if g_n >= 30:
            base = (s_l + LEAGUE_SHRINK * g_rate) / (2 * n_l + LEAGUE_SHRINK)
            src = f"{n_l} matches of this competition blended with all {g_n} in this run"
        else:
            base, src = 0.06, "a default guess (too little data)"
        h, a = team_stats(fx["home_id"], fx["kickoff"], index), team_stats(fx["away_id"], fx["kickoff"], index)
        p_home = (shrunk(h["for5"], h["n"], base) + shrunk(a["ag5"], a["n"], base)) / 2
        p_away = (shrunk(a["for5"], a["n"], base) + shrunk(h["ag5"], h["n"], base)) / 2
        p_goal = 1 - (1 - p_home) * (1 - p_away)
        factor, style_notes, info = style_info(fx["league_id"], fx["home_id"], fx["away_id"])
        mkt = max(0.8, min(1.5, ((1 - 1 / fx["x"]) / avg_q) ** MARKET_WEIGHT)) if avg_q > 0 else 1.0
        p_final = max(0.01, min(0.6, p_goal * factor * mkt))
        breakeven = 1 / fx["x"]
        entries.append({
            "fx": fx, "h": h, "a": a, "base": base, "src": src, "factor": factor, "notes": style_notes,
            "mkt": mkt, "avg_price": avg_price,
            "info": info, "p_final": p_final, "ours": 1 - p_final, "breakeven": breakeven,
            "edge": (1 - p_final) - breakeven, "actual": actual_for(fx, cache), "flags": [],
        })

    # ranks and flags
    total = len(entries)
    for i, e in enumerate(sorted(entries, key=lambda e: e["fx"]["x"]), 1):
        e["market_rank"] = i
    entries.sort(key=lambda e: e["p_final"])
    for i, e in enumerate(entries, 1):
        e["rank"], e["total"] = i, total
        fx, fl = e["fx"], e["flags"]
        if min(e["h"]["n"], e["a"]["n"]) == 0:
            fl.append("NO MATCH DATA for one team")
        elif min(e["h"]["n"], e["a"]["n"]) < SMALL_SAMPLE:
            fl.append(f"SMALL SAMPLE ({min(e['h']['n'], e['a']['n'])} matches)")
        if not fx["verified"]:
            fl.append("FIXTURE NOT CONFIRMED on FotMob (teams matched by name only)")
        if fx["x"] >= 1.15:
            fl.append("MARKET SEES AN OPEN GAME (draw price 1.15+)")
        if i <= total / 3 and e["market_rank"] > total * 2 / 3:
            fl.append("WE LIKE IT, THE MARKET DOESN'T")
        if i > total * 2 / 3 and e["market_rank"] <= total / 3:
            fl.append("MARKET LIKES IT, WE DON'T")

    screen(entries)   # revised shortlist rules (see top of file)

    # write everything
    stamp = f"{datetime.now():%Y-%m-%d_%H%M}"
    summary = [
        "5-MINUTE REPORT SUMMARY (paste this part when you share the report)",
        f"file: {path} | run {datetime.now():%Y-%m-%d %H:%M} | took {time.time() - t0:.0f}s | "
        f"market: back the DRAW (nobody ahead after 5:00)",
        f"fixtures: {len(rows)} | researched: {len(fixtures)} | not matched to FotMob: {len(unresolved)}",
        f"goal minutes: {len(pool)} matches needed | read OK {len(ok_ids)} | goals did not add up to the final score {len(bad_ids)} "
        f"| failed {failed}",
    ]
    if g_n:
        summary.append(f"CALIBRATION: in {g_n} league matches read, a goal came in the first 5 minutes in "
                       f"{g_a / g_n:.1%} (so a draw lands about {1 - g_a / g_n:.1%} of the time with no research)")
    avg_be = sum(e["breakeven"] for e in entries) / total
    summary.append(f"PRICE: the draw prices in this file need {avg_be:.1%} on average just to break even")
    if g_n:
        no_goal = 1 - g_a / g_n
        moe = 1.96 * (no_goal * (1 - no_goal) / g_n) ** 0.5
        summary.append(f"BASELINE: backing every draw would be {(no_goal - avg_be) * 100:+.1f} pts against break-even "
                       f"before any picking (margin of error about +/-{moe * 100:.1f} pts, so treat as unproven)")
        summary.append("EARLY-GOAL RATE BY COMPETITION (small samples, rough):")
        for lid, fxs in by_league.items():
            n, s_, a_ = league_rate(league_ids.get(lid, []), datetime.max.replace(tzinfo=timezone.utc), cache)
            if n:
                summary.append(f"  {fxs[0]['category']} / {fxs[0]['tournament']}: {n} matches read, "
                               f"{a_ / n:.1%} had a goal in the first 5 minutes")
    summary += [f"NOTE: {n}" for n in notes]
    if unresolved:
        summary.append("NOT RESEARCHED:")
        summary += [f"  - {r['home']} vs {r['away']}: {why}" for r, why in unresolved]
    summary += backtest_lines(entries)

    top = [one_liner(e) for e in entries[:15]]
    report = ["\n".join(summary), "", "CALMEST FIRST (top 15)", *top, "", "=" * 70, ""]
    for e in entries:
        report += block(e) + [""]
    report.append("Weights for style adjustments and the shrinkage are first guesses. "
                  "No lineups, injuries, weather, referee or stakes are included.")
    text = "\n".join(report) + "\n"

    revised = revised_lines(entries)

    (OUT_DIR / f"five_report_{stamp}.txt").write_text(text, encoding="utf-8")
    (OUT_DIR / "five_latest.txt").write_text(text, encoding="utf-8")
    short = ["CALMEST FIRST (top 15). A filter, not a recommendation.", *top]
    (OUT_DIR / "five_shortlist_latest.txt").write_text("\n".join(short) + "\n", encoding="utf-8")
    (OUT_DIR / f"five_revised_{stamp}.txt").write_text("\n".join(revised) + "\n", encoding="utf-8")
    (OUT_DIR / "five_revised_latest.txt").write_text("\n".join(revised) + "\n", encoding="utf-8")
    with open(OUT_DIR / f"five_ranking_{stamp}.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["rank", "kickoff_utc", "competition", "home", "away", "draw_odds", "needs", "ours_no_goal",
                    "edge_pts", "market_rank", "flags", "result", "revised_shortlist", "why_not"])
        for e in entries:
            fx = e["fx"]
            res = "" if e["actual"] is None else ("goal in 5" if e["actual"]["early"] else "no goal in 5")
            w.writerow([e["rank"], fx["kickoff"].isoformat(), f"{fx['category']} / {fx['tournament']}", fx["home"],
                        fx["away"], fx["x"], f"{e['breakeven']:.3f}", f"{e['ours']:.3f}", f"{e['edge'] * 100:.1f}",
                        e["market_rank"], " | ".join(e["flags"]), res,
                        "yes" if e["screen_ok"] else "no", "; ".join(e["screen_why"])])

    print("\n".join(summary))
    print("\nCALMEST FIRST (top 15)")
    print("\n".join(top))
    print("\n" + "\n".join(revised))
    print(f"\nFull report : {OUT_DIR / 'five_latest.txt'}")
    print(f"Shortlist   : {OUT_DIR / 'five_shortlist_latest.txt'}")
    print(f"Revised list: {OUT_DIR / 'five_revised_latest.txt'}")
    print(f"Spreadsheet : {OUT_DIR / f'five_ranking_{stamp}.csv'}")


if __name__ == "__main__":
    main()