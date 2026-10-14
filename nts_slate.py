import csv
import hashlib
import json
import re
import sys
import time
from collections import Counter, OrderedDict, defaultdict
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path

from fotmob_mcp.client import FotMobUnavailable
from nts_context import context_lines
from nts_extract import extract, fmt, pct, threat_lines
from nts_opponent import opponent_lines, opponent_profile
from nts_resolve import describe, load_overrides, norm, resolve, team_match

SEL = re.compile(r"^(.*?)\s*\(([^()]*)\)\s*$")  # "Franco Paredes (Argentinos Juniors)"

MATCH_LEAGUE = {}  # match_id -> league id, learned from the first player who has one

# below this many league minutes, goals-vs-position wording is left out (first guess, tune later)
GOAL_MIN_MINUTES = 900

REPORTS_DIR = Path("reports")
MAX_CONSECUTIVE_ERRORS = 8   # stop cleanly if this many players in a row fail (FotMob refusing?)
RESUME_MAX_HOURS = 6         # an unfinished run older than this is not resumed (stats would be stale)

# flags that keep a player off the shortlist
BAD_FLAGS = (
    "ROTATION", "NOT PLAYING", "MISSING FROM SQUAD", "THIN SAMPLE", "AERIAL", "SET PIECE",
    "FREE KICK", "HIGH SHOT", "TAKES PEN", "INJURY", "STALE", "UNVERIFIED", "FUZZY",
)


def parse_rows(path):
    text = Path(path).read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    if not lines:
        return []
    delim = "\t" if "\t" in lines[0] else ","
    rows = []
    for raw in csv.reader(lines, delimiter=delim):
        # anchor on the "sr:match:..." cell so a different first column doesn't break us
        idx = next((i for i, c in enumerate(raw) if c.strip().startswith("sr:match:")), None)
        if idx is None or len(raw) < idx + 11:
            continue
        market = raw[idx + 7].strip()
        if "not to score" not in market.lower():
            continue
        sel = raw[idx + 9].strip()
        m = SEL.match(sel)
        name, team = (m.group(1).strip(), m.group(2).strip()) if m else (sel, "")
        try:
            odds = float(raw[idx + 10])
        except ValueError:
            odds = None
        category = raw[idx + 1].strip()
        rows.append({
            "match_id": raw[idx].strip(),
            "category": category,
            "intl": category.lower() == "international",
            "league": raw[idx + 2].strip(),
            "fixture": raw[idx + 3].strip(),
            "kickoff": raw[idx + 6].strip(),
            "name": name,
            "team": team,
            "odds": odds,
        })
    return rows


def row_key(row):
    return f"{row['match_id']}|{row['name']}|{row['team']}"


def with_retry(fn, *args):
    """Retry only when FotMob itself failed to answer (network hiccup, rate limit)."""
    delay = 1
    for attempt in range(3):
        try:
            return fn(*args)
        except FotMobUnavailable:
            if attempt == 2:
                raise
            time.sleep(delay)
            delay *= 3


def resolve_row(row):
    """Return (status, pool, note)."""
    status, pool = resolve(f"{row['name']} | {row['team']}")
    if status == "notfound":
        s2, p2 = resolve(row["name"])
        if s2 in ("ok", "fuzzy"):
            return s2, p2, "club name not matched, searched by name only"
        return "notfound", [], f"nobody called {row['name']} found at {row['team']}"
    if status == "squad":
        return status, pool, "found via the club's squad page (normal search missed him)"
    if status == "squadpick":
        return status, pool, "two profiles with this name at the club, picked the one on the current squad list"
    return status, pool, ""


def is_forced(row):
    """True if this player was picked by hand in player_overrides.json."""
    key = " | ".join(norm(p) for p in f"{row['name']} | {row['team']}".split("|"))
    return key in load_overrides()


def names_agree(csv_name, fm_name):
    """Do the slate's name and FotMob's name look like the same person?
    Needs two name parts to match (or one, if a name only has one part). Close spellings count."""
    a, b = norm(csv_name).split(), norm(fm_name).split()
    if not a or not b:
        return False

    def close(t, o):
        if t == o:
            return True
        if len(t) >= 2 and len(o) >= 2 and (o.startswith(t) or t.startswith(o)):
            return True  # "mo" / "mohammad"
        return len(t) >= 4 and len(o) >= 4 and SequenceMatcher(None, t, o).ratio() >= 0.8

    hits = sum(1 for t in a if any(close(t, o) for o in b))
    return hits >= min(2, len(a), len(b))


def in_squad(c):
    """Games (of his last 5 club rows) where he started, came on, or sat on the bench."""
    return c["started"] + c["cameo"] + c["unused"]


# ---------- the plain-language briefing ----------

def playing_line(r):
    pos = r["position"] or "Player"
    c, g = r["club5"], r["club5_games"]
    if r["tier"].startswith("NO CLUB DATA"):
        return f"{pos}. FotMob has no club games for him, so we can't tell if he starts."
    if g == 0:
        return f"{pos}. No club games listed."
    if g >= 3 and c["minutes"] == 0:
        return f"{pos}. Not playing: unused sub in all of his last {g} club games."
    s = f"{pos}. Started {c['started']} of his last {g} club games ({c['minutes']} min)"
    if r["avg_min"]:
        s += f", about {r['avg_min']:.0f} min a game this season"
    s += "."
    if g >= 3 and c["started"] <= 1:
        s += " Mostly a sub, so a rotation risk."
    if 0 < g < 4:
        s += f" Only {g} club games on record (new signing or injury?), so treat the numbers with care."
    elif r["minutes"] is not None and r["minutes"] < 600:
        s += f" Small sample: only {r['minutes']:.0f} minutes this season."
    return s


def shots_vs_peers(t, pos):
    ps = t.get("peer_shots")
    if ps is None:
        return None
    if ps >= 0.9:
        return f"shoots more than almost every {pos} (top {max(1, round((1 - ps) * 100))}%)"
    if ps >= 0.7:
        return f"shoots more than most {pos}s"
    if ps <= 0.3:
        return f"rarely shoots for a {pos}"
    return None


def goals_vs_peers(t, pos, minutes):
    """Only said once there are enough minutes for it to mean something."""
    if minutes < GOAL_MIN_MINUTES:
        return None
    pg = t.get("peer_goals")
    if pg is None:
        return None
    if pg >= 0.75:
        return f"scores more than most {pos}s"
    if pg <= 0.25:
        return f"rarely scores for a {pos}"
    return None


def goals_text(goals):
    if goals is None:
        return ""
    if goals == 0:
        return " No goals yet this season."
    return f" Goals this season: {goals:.0f}."


def volume_line(r):
    """Leagues with no shot-by-shot data (no xG): say what the per-90 numbers show."""
    t = r.get("threat") or {}
    pos = (r["position"] or "player").lower()
    mins = r["minutes"] or 0
    gtxt = goals_text(r.get("goals"))
    if mins <= 0:
        return "No league minutes this season, so there is nothing to go on."
    if mins < 90:
        return f"Only {mins:.0f} minutes this season, too little for shot numbers to mean anything.{gtxt}"
    if r["shots90"] is None:
        return "FotMob has no shot numbers for him." + gtxt

    first = f"shoots {r['shots90']:.1f} times per 90"
    if r["sot90"] is not None:
        first += f" ({r['sot90']:.1f} on target)"
    bits = [first]
    for b in (shots_vs_peers(t, pos), goals_vs_peers(t, pos, mins)):
        if b:
            bits.append(b)
    if t.get("aerials_p90") is not None:
        bits.append(f"wins {t['aerials_p90']:.1f} aerial duels per 90")
    if t.get("box_touches_p90") is not None:
        bits.append(f"{t['box_touches_p90']:.1f} touches in the opposition box per 90")
    s = "; ".join(bits)
    s = s[0].upper() + s[1:] + "."
    return s + " No shot-by-shot data in this league, so no xG, headers or set-piece detail." + gtxt


def threat_line(r):
    t = r.get("threat")
    pos = (r["position"] or "player").lower()
    if not t:
        return "No season shot data (FotMob has no club league stats for him)."
    if not t["shots"]:
        if not r["has_xg"]:
            return volume_line(r)
        return "No shots recorded this season."

    n = t["shots"]
    head = f"{n} shot{'' if n == 1 else 's'}"
    if r["shots90"] is not None:
        head += f" ({r['shots90']:.1f} per 90)"
    if n < 5:
        return f"Very few shots on record ({head}), too little to judge."

    bits = []
    if r["pens_taken"]:
        bits.append(f"takes penalties ({r['pens_taken']} so far)")
    if t["headers"] >= 5 and (t["headers_p90"] or 0) >= 0.5:
        bits.append(f"aerial threat: {t['headers']} of his {n} shots are headers")
    if t["sp_xg"] >= 0.5 and (t["sp_share"] or 0) >= 0.5:
        bits.append(f"{pct(t['sp_share'])} of his xG comes from corners and set pieces")
    if t["fk_xg"] >= 0.3:
        bits.append(f"takes free kicks (free-kick xG {t['fk_xg']:.2f})")

    b = shots_vs_peers(t, pos)
    if b:
        bits.append(b)

    ins = t["inside_pct"]
    if ins is not None:
        if ins >= 0.6:
            bits.append(f"{pct(ins)} of his shots are inside the box")
        elif ins <= 0.3:
            bits.append(f"only {pct(ins)} of his shots are inside the box (mostly long range)")

    b = goals_vs_peers(t, pos, r["minutes"] or 0)
    if b:
        bits.append(b)

    if not bits:
        bits.append("nothing stands out")
    s = f"{head}: " + "; ".join(bits) + "."
    if r["has_xg"]:
        s += f" xG {r['xg90']:.2f} per 90."
    return s


def opponent_brief(opp, note):
    if not opp:
        return f"Opponent: unavailable ({note or 'no data'})."
    n = opp["n_teams"]
    lo, hi = n / 3, n * 2 / 3
    use_xg = opp["xgc_rank"] is not None
    xr = opp["xgc_rank"] if use_xg else opp["gc_rank"]
    what = "xG conceded" if use_xg else "goals conceded"
    sr = opp["sp_rank"]

    if xr is None:
        dl = "defence unknown"
    elif xr <= lo:
        dl = "a tight defence"
    elif xr > hi:
        dl = "a leaky defence"
    else:
        dl = "an average defence"

    sp = ""
    if sr is not None:
        conceded = fmt(opp["sp_total"], 0)
        if sr <= lo:
            sp = f", good at set pieces (conceded {conceded}, rank {sr} of {n})"
        elif sr > hi:
            sp = f", weak at set pieces (conceded {conceded}, rank {sr} of {n})"
        else:
            sp = f", average at set pieces (rank {sr} of {n})"
    xr_txt = "n/a" if xr is None else f"{xr} of {n}"
    tail = " No xG or set-piece defence stats for this league." if opp.get("table_only") else ""
    return f"Faces {opp['name']}: {dl} ({what} rank {xr_txt}){sp}.{tail}"


def opp_short(opp):
    """Very short opponent label for the shortlist."""
    if not opp:
        return None
    n = opp["n_teams"]
    rank = opp["xgc_rank"] if opp["xgc_rank"] is not None else opp["gc_rank"]
    if rank is None:
        return opp["name"]
    label = "tight" if rank <= n / 3 else ("leaky" if rank > n * 2 / 3 else "average")
    return f"{opp['name']} ({label} defence, {rank}/{n})"


# ---------- flags (kept under each brief) ----------

def flags(row, r, status, note, opp):
    f = []
    if status == "fuzzy":
        f.append(f"FUZZY NAME -> {r['name']}")
    if note:
        f.append(note)
    if r["tier"].startswith("NO CLUB DATA"):
        f.append("UNVERIFIED CLUB STARTER")

    g = r["club5_games"]
    absent = g - in_squad(r["club5"])
    if g >= 3 and r["club5"]["minutes"] == 0:
        f.append("NOT PLAYING (bench only, 0 min in last 5)")
    elif g >= 3 and absent >= 2:
        f.append(f"MISSING FROM SQUAD (absent from {absent} of last {g} club games)")
    elif g >= 3 and r["club5"]["started"] <= 1:
        f.append("ROTATION RISK (1 or fewer starts in last 5)")
    if 0 < g < 4:
        f.append(f"THIN SAMPLE (only {g} club games listed)")

    air_or_set_piece = False
    t = r.get("threat")
    if t and t["shots"]:
        if t["headers"] >= 5 and (t["headers_p90"] or 0) >= 0.5:
            f.append(f"AERIAL THREAT ({t['headers']} headed shots, {fmt(t['headers_p90'])}/90)")
            air_or_set_piece = True
        if t["sp_xg"] >= 0.5 and (t["sp_share"] or 0) >= 0.5:
            f.append(f"SET PIECE THREAT ({pct(t['sp_share'])} of his xG from corners and set pieces)")
            air_or_set_piece = True
        if t["fk_xg"] >= 0.3:
            f.append(f"FREE KICK TAKER (free-kick xG {fmt(t['fk_xg'])})")
        if t["shots"] >= 8 and (t["peer_shots"] or 0) >= 0.9:
            top = max(1, round((1 - t["peer_shots"]) * 100))
            f.append(f"HIGH SHOT VOLUME FOR POSITION (top {top}%, {pct(t['inside_pct'])} inside box)")

    # rank 1 = tightest defence, so a high rank means weak at set pieces
    if opp and air_or_set_piece and opp["sp_rank"] and opp["sp_rank"] > opp["n_teams"] * 2 / 3:
        f.append(f"SET PIECE MISMATCH (he is an aerial/set-piece threat, "
                 f"{opp['name']} are {opp['sp_rank']} of {opp['n_teams']} for set-piece goals conceded)")

    if r["pens_taken"]:
        f.append("TAKES PENALTIES")
    if r["injury"]:
        f.append("INJURY LISTED")
    if r["last_club_date"]:
        days = (date.today() - date.fromisoformat(r["last_club_date"])).days
        if days > 30:
            f.append(f"STALE CLUB DATA ({days}d)")
    return f


# ---------- one player ----------

def process(row, detail=False):
    """Return a record: lines (the brief), outcome, info, has_xg, short.
    outcome: ok | unsure | notfound | ambiguous | error"""
    odds = f"{row['odds']:.2f}" if row["odds"] else " ?  "
    lines = [f"  {odds}  {row['name']} ({row['team']})"]

    def add(s):
        lines.append("        " + s)

    def result(outcome, info="", r=None, short=None):
        return {"lines": lines, "outcome": outcome, "info": info,
                "has_xg": bool(r and r["has_xg"]), "short": short}

    try:
        status, pool, note = with_retry(resolve_row, row)
    except Exception as e:
        add(f"!! search failed: {e} | {getattr(e, '__cause__', None)}")
        return result("error", f"search failed: {e}")
    if status == "notfound":
        add(f"NOT FOUND ({note})" if note else "NOT FOUND")
        return result("notfound", note or "no match found")
    if status == "ambiguous":
        add(f"AMBIGUOUS ({len(pool)} players), skipped. To pick one, add "
            f"\"{row['name']} | {row['team']}\": <id> to player_overrides.json")
        for c in pool[:4]:
            add("  " + describe(c))
        return result("ambiguous", f"{len(pool)} possible players with this name")
    try:
        r = with_retry(extract, int(pool[0]["id"]))
    except Exception as e:
        add(f"!! extract failed: {e} | {getattr(e, '__cause__', None)}")
        return result("error", f"extract failed: {e}")

    # is this really the person on the slate? (skipped if the name or club doesn't agree)
    if not is_forced(row):
        problems = []
        if not names_agree(row["name"], r["name"]):
            problems.append(f"name differs (FotMob has '{r['name']}')")
        if row["team"] and r["team"] and not team_match(row["team"], r["team"]):
            problems.append(f"club differs (FotMob has '{r['team']}')")
        if problems:
            why = "; ".join(problems)
            add(f"UNSURE MATCH, not researched: {why}. It may be a different person.")
            add(f"If it is the right one, add \"{row['name']} | {row['team']}\": {pool[0]['id']} "
                f"to player_overrides.json and run again.")
            return result("unsure", why)

    league_id = r["league_id"]
    if league_id:
        MATCH_LEAGUE.setdefault(row["match_id"], league_id)
    else:
        league_id = MATCH_LEAGUE.get(row["match_id"])

    opp, opp_note = None, "no league known for this match"
    if league_id:
        try:
            opp, opp_note = with_retry(opponent_profile, row["fixture"], row["team"], league_id)
        except Exception as e:
            opp, opp_note = None, f"opponent lookup failed ({e})"

    add(playing_line(r))
    add(threat_line(r))
    add(opponent_brief(opp, opp_note))
    for line in context_lines(int(pool[0]["id"]), r):
        add(line)

    fl = flags(row, r, status, note, opp)
    if fl:
        add("FLAGS: " + " | ".join(fl))

    if detail:
        c = r["club5"]
        club = (f"{r['team']} {c['started']}S {c['cameo']}C {c['unused']}U {c['minutes']}min "
                f"(in squad {in_squad(c)} of {r['club5_games']})"
                if r["club5_games"] else f"{r['team']}: no club games")
        shoot = (f"xG/90 {fmt(r['xg90'])} | shots/90 {fmt(r['shots90'])} | avg {fmt(r['avg_min'],0)} min/app"
                 if r["has_xg"] else f"no xG | shots/90 {fmt(r['shots90'])} | SoT/90 {fmt(r['sot90'])}")
        add(f"-- detail: tier {r['tier'].split(' (')[0]}")
        add(f"CLUB last5    : {club}")
        add(f"shooting      : {shoot}")
        for line in threat_lines(r["threat"]):
            add(line)
        for line in opponent_lines(opp, opp_note):
            add(line)

    short = {
        "position": r["position"],
        "started": r["club5"]["started"],
        "games": r["club5_games"],
        "avg_min": r["avg_min"],
        "shots90": r["shots90"],
        "xg90": r["xg90"],
        "flags": fl,
        "opp": opp_short(opp),
    }
    return result("ok", "", r, short)


# ---------- saving progress so a crash costs nothing ----------

def work_path(path, limit):
    h = hashlib.sha1(Path(path).read_bytes()).hexdigest()[:10]
    return REPORTS_DIR / "work" / f"{h}_{limit or 'all'}.jsonl"


def load_done(wp, fresh):
    """Players already finished in an earlier, unfinished run of the same slate."""
    done = {}
    if not wp.exists():
        return done
    age_hours = (time.time() - wp.stat().st_mtime) / 3600
    if fresh or age_hours > RESUME_MAX_HOURS:
        wp.unlink()
        return done
    for line in wp.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue  # a half-written last line after a crash
        if rec.get("outcome") == "error":
            continue  # failed players get another try
        done[rec["key"]] = rec
    return done


# ---------- the summary and the shortlist ----------

def build_summary(results, pending, path, skipped_intl, elapsed, stop_reason, resumed):
    n = len(results)
    cnt = Counter(x["outcome"] for x in results)
    out = ["RUN SUMMARY (paste this part when you share the report)"]
    if pending:
        out.append(f"PARTIAL RUN: {n} of {n + pending} players done ({stop_reason}). "
                   f"Run the same command again to finish the rest.")
    took = f"this session took {elapsed:.0f}s"
    if resumed:
        took += f", {resumed} players came from the earlier run"
    out.append(f"slate: {path} | run at {datetime.now():%Y-%m-%d %H:%M} | {took} | "
               f"{skipped_intl} international rows skipped")
    pct_ok = (cnt["ok"] / n * 100) if n else 0
    out.append(f"players: {n} | researched: {cnt['ok']} ({pct_ok:.0f}%) | "
               f"unsure match, skipped: {cnt['unsure']} | not found: {cnt['notfound']} | "
               f"ambiguous: {cnt['ambiguous']} | errors: {cnt['error']}")
    if cnt["error"]:
        out.append("errors are usually FotMob hiccups: run the same command again to retry them")

    per = defaultdict(lambda: [0, 0, 0])  # total, researched, with xG
    for x in results:
        key = f"{x['row']['category']} / {x['row']['league']}"
        per[key][0] += 1
        if x["outcome"] == "ok":
            per[key][1] += 1
            if x["has_xg"]:
                per[key][2] += 1
    for key, (tot, ok, xg) in per.items():
        extra = "" if xg else "  (no xG here, so the briefs are thinner)"
        out.append(f"  {key}: {tot} players, {ok} researched, {xg} with xG data{extra}")

    missed = [x for x in results if x["outcome"] != "ok"]
    if missed:
        out.append("NOT RESEARCHED:")
        for x in missed:
            w = x["row"]
            o = f"{w['odds']:.2f}" if w["odds"] else "?"
            out.append(f"  - {o} {w['name']} ({w['team']}) [{w['fixture']}]: {x['outcome']}: {x['info']}")
    out.append("Stats are as of the run time. Run it before kickoff.")
    return out


def is_clean(short):
    if not short or short["games"] < 4 or short["started"] < 4:
        return False
    return not any(f.startswith(BAD_FLAGS) for f in short["flags"])


def build_shortlist(matches, done, path):
    out = [
        f"SHORTLIST from {path} | {datetime.now():%Y-%m-%d %H:%M}",
        "Only players who started 4 or more of their last 5 club games and have no risk or threat flag.",
        "Quietest first (lowest shots per 90). A very low number can also mean a small sample, so check the full report.",
        "This is a filter, not a recommendation.",
    ]
    total_clean = 0
    for plist in matches.values():
        f = plist[0]
        recs = []
        for row in sorted(plist, key=lambda x: -(x["odds"] or 0)):
            rec = done.get(row_key(row))
            if rec:
                recs.append((row, rec))
        clean = [(row, rec["short"]) for row, rec in recs if rec["outcome"] == "ok" and is_clean(rec["short"])]
        clean.sort(key=lambda t: (t[1]["shots90"] is None, t[1]["shots90"] or 0))
        total_clean += len(clean)
        out.append(f"\n######## {f['fixture']} | {f['league']} | {f['kickoff']} ########")
        out.append(f"  {len(clean)} of {len(recs)} players on the clean list")
        for row, s in clean:
            o = f"{row['odds']:.2f}" if row["odds"] else "?"
            sh = "n/a" if s["shots90"] is None else f"{s['shots90']:.1f}"
            mins = "n/a" if s["avg_min"] is None else f"~{s['avg_min']:.0f}"
            bits = [f"{o}  {row['name']} ({row['team']})", s["position"] or "?",
                    f"started {s['started']}/{s['games']}, {mins} min", f"shots/90 {sh}"]
            if s.get("xg90") is not None:
                bits.append(f"xG/90 {s['xg90']:.2f}")
            if s.get("opp"):
                bits.append(f"vs {s['opp']}")
            out.append("   " + " | ".join(bits))
    out.append(f"\n{total_clean} players on the clean list in total.")
    return out


# ---------- the run ----------

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    detail = "--detail" in sys.argv
    show_all = "--print" in sys.argv
    fresh = "--fresh" in sys.argv
    path = args[0] if args else "slate.csv"
    limit = int(args[1]) if len(args) > 1 else None

    try:
        rows = parse_rows(path)
    except FileNotFoundError:
        print(f"Can't find {path}. Save the bookie export in this folder first (name it slate.csv).")
        return
    if not rows:
        print(f"No 'Player Not to Score' rows parsed from {path}.")
        print("Paste me the first 3 lines of the file (head -3) so I can fix the column positions.")
        return

    skipped_intl = sum(1 for r in rows if r["intl"])
    rows = [r for r in rows if not r["intl"]]
    total_club = len(rows)
    if not rows:
        print(f"Only international rows found ({skipped_intl}), nothing to research.")
        return
    if limit:
        rows = rows[:limit]

    header = [f"PARSED {len(rows)} of {total_club} club rows. First 3, check these look right:"]
    for r in rows[:3]:
        header.append("   " + " | ".join(str(x) for x in (
            r["category"], r["league"], r["fixture"], r["kickoff"], r["name"], r["team"], r["odds"])))
    print("\n".join(header))

    matches = OrderedDict()
    for row in rows:
        matches.setdefault(row["match_id"], []).append(row)
    ordered = [row for plist in matches.values()
               for row in sorted(plist, key=lambda x: -(x["odds"] or 0))]
    n = len(ordered)

    REPORTS_DIR.mkdir(exist_ok=True)
    wp = work_path(path, limit)
    wp.parent.mkdir(parents=True, exist_ok=True)
    done = load_done(wp, fresh)
    resumed = len(done)
    if resumed:
        print(f"Resuming: {resumed} of {n} players were already done in an earlier run of this slate.")

    tty = sys.stdout.isatty()
    t0 = time.time()
    new_count, consecutive_errors, stop_reason = 0, 0, None
    fh = wp.open("a", encoding="utf-8")
    try:
        for i, row in enumerate(ordered, 1):
            key = row_key(row)
            if key in done:
                continue
            if tty:
                eta = ""
                if new_count >= 5:
                    left = (n - i) * ((time.time() - t0) / new_count)
                    eta = f" (about {left / 60:.1f} min left)"
                print(f"\r  researching {i}/{n}{eta}: {row['name'][:28]:<28}", end="", flush=True)
            elif i % 25 == 0 or i == n:
                print(f"  {i}/{n} done", flush=True)

            rec = process(row, detail)
            rec["key"] = key
            done[key] = rec
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            new_count += 1

            consecutive_errors = consecutive_errors + 1 if rec["outcome"] == "error" else 0
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                stop_reason = (f"stopped after {MAX_CONSECUTIVE_ERRORS} failures in a row, "
                               f"FotMob may be refusing requests, wait a few minutes")
                break
    except KeyboardInterrupt:
        stop_reason = "stopped by you (Ctrl+C)"
    finally:
        fh.close()
    if tty:
        print("\r" + " " * 78 + "\r", end="")

    elapsed = time.time() - t0
    results = [{"row": row, "outcome": done[row_key(row)]["outcome"], "info": done[row_key(row)]["info"],
                "has_xg": done[row_key(row)]["has_xg"]}
               for row in ordered if row_key(row) in done]
    pending = n - len(results)

    body = []
    for plist in matches.values():
        f = plist[0]
        body.append(f"\n######## {f['fixture']} | {f['league']} | {f['category']} | {f['kickoff']} ########")
        for row in sorted(plist, key=lambda x: -(x["odds"] or 0)):
            rec = done.get(row_key(row))
            if rec:
                body.append("")
                body.extend(rec["lines"])

    summary = build_summary(results, pending, path, skipped_intl, elapsed, stop_reason, resumed)
    footer = ["", "Briefs use this season's stats. Opponent rank 1 = tightest defence. "
                  "Club style rank 1 = most of it."]
    text = "\n".join(summary + ["", "=" * 70] + header + body + footer) + "\n"
    shortlist = "\n".join(build_shortlist(matches, done, path)) + "\n"

    stamp = f"{datetime.now():%Y-%m-%d_%H%M}"
    report_path = REPORTS_DIR / f"report_{stamp}.txt"
    short_path = REPORTS_DIR / f"shortlist_{stamp}.txt"
    report_path.write_text(text, encoding="utf-8")
    short_path.write_text(shortlist, encoding="utf-8")
    (REPORTS_DIR / "latest.txt").write_text(text, encoding="utf-8")
    (REPORTS_DIR / "shortlist_latest.txt").write_text(shortlist, encoding="utf-8")

    if not pending:
        try:
            wp.unlink()  # finished: the next run of this slate starts fresh
        except FileNotFoundError:
            pass

    if show_all:
        print(text)
    else:
        print("\n".join(summary))
    print(f"\nFull report : {report_path}   (copy: {REPORTS_DIR / 'latest.txt'})")
    print(f"Shortlist   : {short_path}   (copy: {REPORTS_DIR / 'shortlist_latest.txt'})")


if __name__ == "__main__":
    main()