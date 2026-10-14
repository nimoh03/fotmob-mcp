"""
market_data.py - STEP 1: season data for every club match in options.csv   (v3)

Run:   python market_data.py options.csv --limit 20     (test on first 20 club matches)
       python market_data.py options.csv                (full slate)

Resolving teams: (1) FotMob daily fixture list (kickoff time + names), (2) name search, but only
when FotMob confirms the fixture on the team page (so reserve/youth sides never match a senior team).
International / national-team games are skipped.

Writes:
  market_cache.json                        one record per finished match (reused forever)
  resolve_cache.json                       slate match -> FotMob team ids (confirmed only)
  teams_index.json                         team id -> its season match ids (complete histories)
  reports/markets/coverage_<stamp>.csv     per slate match: READY/SKIP + which data exists
"""
import csv
import json
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path

from fotmob_mcp.client import FotMobUnavailable
from five_min import (client, parse_dt, find_matches, norm_match, team_page,
                      resolve_fixture, parse_goals)
from nts_resolve import norm, team_match

MIN_GAMES = 5
CACHE = Path("market_cache.json")
RESOLVE = Path("resolve_cache.json")
TEAMS = Path("teams_index.json")
OUT_DIR = Path("reports/markets")
FRIENDLY = re.compile(r"friendl", re.I)
INTL_TOURN = re.compile(
    r"world cup|nations league|friendl|olympic|european championship|"
    r"africa cup|asian cup|copa america|gold cup", re.I)


def load(p):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save(p, d):
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def aware(d):
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def to_num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- slate
def is_international(r):
    if (r.get("category") or "").strip().lower() == "international":
        return True
    return bool(INTL_TOURN.search(r.get("tournament") or ""))


def read_options(path):
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            ko = parse_dt(r.get("startTime", ""))
            if ko is None:
                continue
            # scanner stores Nigeria time (UTC+1) with a Z suffix, so real UTC is 1h earlier
            r["kickoff"] = aware(ko) - timedelta(hours=1)
            r["home"], r["away"] = r["homeTeam"], r["awayTeam"]
            rows.append(r)
    return rows


# ---------------------------------------------------------------- resolving teams (daily fixture list)
_day = {}


def day_fixtures(day):
    if day in _day:
        return _day[day]
    data = None
    for params in ({"date": day}, {"date": day, "timezone": "UTC", "ccode3": "NGA"}):
        try:
            data = client.get_json("/api/data/matches", params, use_cache=False)
            break
        except Exception:
            continue
    out = []
    if isinstance(data, dict):
        for lg in data.get("leagues") or []:
            for m in lg.get("matches") or []:
                try:
                    nm = norm_match(m)
                except Exception:
                    nm = None
                if nm:
                    nm["league"] = lg.get("name") or ""
                    out.append(nm)
        if not out:
            for m in find_matches(data, []):
                try:
                    nm = norm_match(m)
                except Exception:
                    nm = None
                if nm:
                    nm["league"] = "?"
                    out.append(nm)
    _day[day] = out
    return out


def sim(a, b):
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    if team_match(a, b):
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def best_fixture(r, fixtures):
    best = None
    for fx in fixtures:
        dt = abs((aware(fx["date"]) - r["kickoff"]).total_seconds()) / 60
        if dt > 180:
            continue
        sh, sa = sim(r["homeTeam"], fx["home"]), sim(r["awayTeam"], fx["away"])
        score = sh + sa - dt / 600
        if best is None or score > best["score"]:
            best = {"score": score, "fx": fx, "sh": sh, "sa": sa, "dt": dt}
    return best


def verdict(b):
    if not b:
        return "NONE"
    if b["sh"] >= 0.6 and b["sa"] >= 0.6 and b["dt"] <= 30:
        return "OK"
    if max(b["sh"], b["sa"]) >= 0.85 and min(b["sh"], b["sa"]) >= 0.3 and b["dt"] <= 20:
        return "ONE-SIDE"
    return "WEAK"


def resolve_daily(r, fixtures):
    b = best_fixture(r, fixtures)
    if verdict(b) in ("OK", "ONE-SIDE"):
        fx = b["fx"]
        return {"home_id": str(fx["home_id"]), "away_id": str(fx["away_id"]),
                "league_id": None, "verified": True, "via": "daily"}
    return None


# ---------------------------------------------------------------- season matches
def season_start(page):
    ls = str(((page or {}).get("details") or {}).get("latestSeason") or "")
    now = datetime.now(timezone.utc)
    if ls.isdigit():  # calendar-year league, e.g. "2026"
        return datetime(now.year, 1, 1, tzinfo=timezone.utc)
    y = now.year if now.month >= 7 else now.year - 1  # split-year, e.g. "2026/2027"
    return datetime(y, 7, 1, tzinfo=timezone.utc)


_pages = {}


def season_matches(tid):
    page = _pages.get(tid)
    if page is None:
        page = team_page(tid) or {}
        _pages[tid] = page
    start = season_start(page)
    seen, out = set(), []
    for m in find_matches(page, []):
        nm = norm_match(m)
        if not nm or nm["id"] in seen:
            continue
        seen.add(nm["id"])
        nm["comp"] = (m.get("tournament") or {}).get("name") or ""
        if not nm["finished"] or nm["cancelled"] or nm["score"] is None:
            continue
        if tid not in (nm["home_id"], nm["away_id"]):
            continue
        if FRIENDLY.search(nm["comp"]):
            continue
        if aware(nm["date"]) < start:
            continue
        out.append(nm)
    out.sort(key=lambda x: x["date"])
    return out


# ---------------------------------------------------------------- match record
def stat_rows(data):
    """{'Expected goals (xG)': [h, a], 'Total shots': [h, a], ...} from the 'All' period."""
    content = data.get("content") or {}
    root = ((content.get("stats") or {}).get("Periods") or {}).get("All") or content.get("stats") or {}
    rows = {}

    def walk(o, d=0):
        if d > 12:
            return
        if isinstance(o, dict):
            t, s = o.get("title"), o.get("stats")
            if isinstance(t, str) and isinstance(s, list) and len(s) == 2 and t not in rows:
                a, b = to_num(s[0]), to_num(s[1])
                if a is not None and b is not None:
                    rows[t] = [a, b]
            for v in o.values():
                walk(v, d + 1)
        elif isinstance(o, list):
            for v in o:
                walk(v, d + 1)

    walk(root)
    return rows


def card_info(data, rows):
    """[home_cards, away_cards] (players only, coach cards excluded) or None if FotMob has no card data."""
    y, r = rows.get("Yellow cards"), rows.get("Red cards")
    if y:
        return [int(y[0] + (r[0] if r else 0)), int(y[1] + (r[1] if r else 0))]
    counts, seen, found = [0, 0], set(), [False]

    def walk(o, d=0):
        if d > 14:
            return
        if isinstance(o, dict):
            if str(o.get("type") or "").lower() == "card" and "isHome" in o:
                desc = o.get("cardDescription")
                coach = isinstance(desc, dict) and desc.get("localizedKey") == "coach"
                key = (o.get("time"), o.get("isHome"), str(o.get("playerId") or o.get("nameStr")), str(o.get("card")))
                if not coach and key not in seen:
                    seen.add(key)
                    found[0] = True
                    counts[0 if o.get("isHome") else 1] += 1
            for v in o.values():
                walk(v, d + 1)
        elif isinstance(o, list):
            for v in o:
                walk(v, d + 1)

    walk((data.get("content") or {}).get("matchFacts") or data)
    return counts if found[0] else None


def formations(data):
    lu = (data.get("content") or {}).get("lineup") or {}
    out = []
    for side in ("homeTeam", "awayTeam"):
        f = (lu.get(side) or {}).get("formation") if isinstance(lu, dict) else None
        out.append(f if isinstance(f, str) and f else None)
    return out


def build_record(m):
    data = None
    for attempt in range(3):
        try:
            data = client.get_json("/api/data/matchDetails", {"matchId": m["id"]}, use_cache=False)
            break
        except FotMobUnavailable:
            time.sleep(1 + attempt * 2)
        except Exception:
            return None
    if not isinstance(data, dict):
        return None
    hs, as_ = m["score"]
    goals = parse_goals(data)
    g_home = sum(g[1] for g in goals)
    timeline_ok = len(goals) == hs + as_
    sides_ok = timeline_ok and g_home == hs and (len(goals) - g_home) == as_
    rows = stat_rows(data)
    return {
        "id": m["id"], "d": aware(m["date"]).isoformat(),
        "h": m["home_id"], "a": m["away_id"], "hn": m["home"], "an": m["away"],
        "score": [hs, as_], "comp": m["comp"],
        "goals": goals,                 # [[minute, 1 if home else 0], ...]
        "timeline_ok": timeline_ok,     # goal count matches the final score
        "sides_ok": sides_ok,           # home/away split of goals also matches
        "cards": card_info(data, rows),  # [home, away] or None
        "xg": rows.get("Expected goals (xG)"),
        "shots": rows.get("Total shots"),
        "sot": rows.get("Shots on target"),
        "corners": rows.get("Corners"),
        "fouls": rows.get("Fouls committed"),
        "poss": rows.get("Ball possession"),
        "form": formations(data),
    }


# ---------------------------------------------------------------- coverage
def team_summary(tid, sm, cache):
    recs = [cache[m["id"]] for m in sm if m["id"] in cache]

    def share(f):
        return sum(1 for r in recs if f(r)) / len(recs) if recs else 0.0

    return {
        "n": len(sm), "have": len(recs),
        "n_home": sum(1 for m in sm if m["home_id"] == tid),
        "n_away": sum(1 for m in sm if m["away_id"] == tid),
        "goals": share(lambda r: r["timeline_ok"]),
        "cards": share(lambda r: r["cards"] is not None),
        "xg": share(lambda r: r["xg"] is not None),
        "form": share(lambda r: any(r["form"])),
    }


def yn(a, b, key, thr=0.8):
    return "Y" if a[key] >= thr and b[key] >= thr else "N"


def main():
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    args = [a for a in sys.argv[1:] if not a.startswith("--") and a != str(limit)]
    path = args[0] if args else "options.csv"

    all_rows = read_options(path)
    intl = [r for r in all_rows if is_international(r)]
    rows = [r for r in all_rows if not is_international(r)]
    if limit:
        rows = rows[:limit]
    print(f"slate rows: {len(all_rows)} | international skipped: {len(intl)} | club matches: {len(rows)}")

    # 1) resolve both teams on FotMob: cache -> daily fixture list -> confirmed name search
    rc = {k: v for k, v in load(RESOLVE).items() if v.get("verified")}  # drop old unconfirmed guesses
    days = sorted({(r["kickoff"] + timedelta(days=d)).strftime("%Y%m%d") for r in rows for d in (-1, 0, 1)})
    fixtures = {}
    for d in days:
        for fx in day_fixtures(d):
            fixtures[fx["id"]] = fx
    fixtures = list(fixtures.values())
    print(f"FotMob daily fixtures loaded: {len(fixtures)}")

    resolved = {}
    for r in rows:
        key = str(r["eventId"])
        if key in rc:
            resolved[key] = rc[key]
            continue
        res = resolve_daily(r, fixtures)
        if res:
            resolved[key] = res
            rc[key] = res

    rest = [r for r in rows if str(r["eventId"]) not in resolved]
    rejected = [0]

    def search_row(r):
        try:
            res, _why = resolve_fixture(r)
        except Exception:
            res = None
        if res and not res.get("verified"):
            rejected[0] += 1   # name found but fixture not confirmed on the team page: could be a senior/youth mix-up
            res = None
        if res:
            res["via"] = "search"
        return r, res

    with ThreadPoolExecutor(6) as ex:
        for r, res in ex.map(search_row, rest):
            if res:
                resolved[str(r["eventId"])] = res
                rc[str(r["eventId"])] = res
    save(RESOLVE, rc)
    via = Counter(v.get("via", "search") for v in resolved.values())
    print(f"resolved on FotMob: {len(resolved)}/{len(rows)}  (by method: {dict(via)}) | "
          f"search matches rejected as unconfirmed: {rejected[0]}")

    # 2) season matches per team
    tids = sorted({t for v in resolved.values() for t in (v["home_id"], v["away_id"])})

    def sm_for(tid):
        try:
            return tid, season_matches(tid)
        except Exception:
            return tid, []

    with ThreadPoolExecutor(6) as ex:
        team_sm = dict(ex.map(sm_for, tids))
    print(f"teams: {len(tids)} | season matches listed: {sum(len(v) for v in team_sm.values())}")

    # 3) fetch match records (cached forever)
    cache = load(CACHE)
    need = {}
    for sm in team_sm.values():
        for m in sm:
            if m["id"] not in cache:
                need[m["id"]] = m
    print(f"match records to fetch: {len(need)} (cached already: {len(cache)})")
    done = 0
    with ThreadPoolExecutor(8) as ex:
        for rec in ex.map(build_record, list(need.values())):
            done += 1
            if rec:
                cache[rec["id"]] = rec
            if done % 50 == 0:
                print(f"  fetched {done}/{len(need)}")
                save(CACHE, cache)
    save(CACHE, cache)

    # complete season histories (the backtests only trust these teams)
    tix = load(TEAMS)
    for tid, sm in team_sm.items():
        if sm and all(m["id"] in cache for m in sm):
            tix[tid] = [m["id"] for m in sm]
    save(TEAMS, tix)

    # 4) coverage report
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"coverage_{datetime.now().strftime('%Y-%m-%d_%H%M')}.csv"
    status_count, by_tourn, unresolved = Counter(), Counter(), []
    cols = ["match", "tournament", "kickoff_wat", "status", "reason", "resolved_by", "home_id", "away_id",
            "home_games", "home_at_home", "away_games", "away_away",
            "goals_ok", "cards_ok", "xg_ok", "form_ok"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            res = resolved.get(str(r["eventId"]))
            base = [r["match"], r["tournament"], r["startTime"]]
            if not res:
                w.writerow(base + ["SKIP", "not found on FotMob", ""] + [""] * 10)
                status_count["SKIP"] += 1
                by_tourn[(r["tournament"], "SKIP")] += 1
                unresolved.append(f"{r['match']} ({r['tournament']})")
                continue
            hid, aid = res["home_id"], res["away_id"]
            hs = team_summary(hid, team_sm.get(hid, []), cache)
            as_ = team_summary(aid, team_sm.get(aid, []), cache)
            status, reason = "READY", ""
            for label, s in (("home", hs), ("away", as_)):
                if s["n"] < MIN_GAMES:
                    status, reason = "SKIP", f"{label} team has {s['n']} competitive games this season"
                    break
                if s["have"] < MIN_GAMES:
                    status, reason = "SKIP", f"{label} team: only {s['have']} match records fetched"
                    break
            status_count[status] += 1
            by_tourn[(r["tournament"], status)] += 1
            w.writerow(base + [status, reason, res.get("via", "search"), hid, aid,
                               hs["n"], hs["n_home"], as_["n"], as_["n_away"],
                               yn(hs, as_, "goals"), yn(hs, as_, "cards"),
                               yn(hs, as_, "xg"), yn(hs, as_, "form")])

    print("\n" + "=" * 60)
    print("COVERAGE:", dict(status_count))
    ready_rows = list(csv.DictReader(open(out_path, encoding="utf-8")))
    ready = [x for x in ready_rows if x["status"] == "READY"]
    print(f"READY matches: {len(ready)}")
    for k in ("goals_ok", "cards_ok", "xg_ok", "form_ok"):
        print(f"  {k}: {sum(1 for x in ready if x[k] == 'Y')}/{len(ready)}")
    print("\nSKIPS by tournament (top 12):")
    skips = Counter({t: n for (t, s), n in by_tourn.items() if s == "SKIP"})
    for t, n in skips.most_common(12):
        print(f"  {n:3d}  {t}")
    if unresolved:
        print(f"\nNOT FOUND on FotMob ({len(unresolved)}):")
        for u in unresolved[:40]:
            print("  ", u)
    print("\nreport:", out_path)


if __name__ == "__main__":
    main()