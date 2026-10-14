"""Post-mortem + results log for the three slips (NTS, cards, 5-minute). v2

  python postmortem.py slip_nts.txt slip_cards.txt slip_five.txt \
        --five=five_report.txt --nts=nts_report.txt --cards=ranked_cards_all.csv

Each slip file = the SportyBet slip text pasted as-is (one file per slip).

  --five=FILE      5-minute report text (adds our estimate, edge, market flags)
  --nts=FILE       NTS report text (adds role flags: rotation, set piece, etc.)
  --cards=FILE     ranked_cards_all.csv (adds hit rate, ref, smoothing check)
  --log=FILE       results log (default results_log.csv, appended, no duplicates)
  --year=2026      year for the slip dates
  --only-lost      print only lost legs (mismatches always print)
  --no-fotmob      skip FotMob, use SportyBet's own Won/Lost/Void only
  --dump=ID        print what FotMob returns for one match id, then stop

Put this file in the SAME folder as five_min.py (it imports from it).
SportyBet's Won/Lost/Void is treated as the truth. We also re-grade every leg
from FotMob events and flag MISMATCH if the two disagree.
GR column: W won, L lost, V void, pending. '*' = graded by FotMob because
SportyBet had not settled it. '!!' = SportyBet and FotMob disagree.
"""
import csv
import json
import os
import re
import sys
import unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

ARGV = sys.argv[1:]
args = [a for a in ARGV if not a.startswith("--")]
opts = {a.split("=", 1)[0]: (a.split("=", 1)[1] if "=" in a else "1")
        for a in ARGV if a.startswith("--")}

CARDS_CSV = opts.get("--cards", "ranked_cards_all.csv")
FIVE_TXT = opts.get("--five", "")
NTS_TXT = opts.get("--nts", "")
LOG_FILE = opts.get("--log", "results_log.csv")
YEAR = int(opts.get("--year", datetime.now().year))
ONLY_LOST = "--only-lost" in opts
NO_FOTMOB = "--no-fotmob" in opts

OPEN_GAME_DRAW = 1.13      # 5-min draw price at/above this = market sees an open game
MATCH_WINDOW = 36 * 3600   # a FotMob match must be within 36h of the slip date
CACHE_FILE = "postmortem_cache.json"
ALIAS_FILE = "aliases.csv"
WORKERS = 3
STATUS_WORDS = ("Won", "Lost", "Void", "Ongoing", "Not Started", "Live")
NOT_FIRST_TEAM = re.compile(r"\(W\)|\bU\d{2}\b|\bwomen\b|\bII\b|\bB$", re.I)
SUFFIX = re.compile(r"\b(FC|CF|SC|AFC|CD|CA|SV|KV|KAA|AD|CE|US|SS|RSC|RC|CR|SK|FK|IF|BK)\b", re.I)

client = None
if not NO_FOTMOB:
    try:
        from fotmob_mcp.client import FotMobClient
        from five_min import candidates, team_page, page_matches, resolve_fixture
        client = FotMobClient()
    except Exception as e:
        print(f"WARNING: could not load FotMob helpers ({e}). Running with --no-fotmob.\n")
        NO_FOTMOB = True


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower()).strip()


def to_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


ALIASES = {}
if os.path.exists(ALIAS_FILE):
    with open(ALIAS_FILE, encoding="utf-8-sig", newline="") as f:
        for row in csv.reader(f):
            if len(row) >= 2 and row[0].strip() and row[0].strip().lower() != "sportybet_name":
                ALIASES[row[0].strip()] = row[1].strip()


def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


CACHE = load_cache()


# ---------------------------------------------------------------------------
# 1. parse the pasted SportyBet slip text
# ---------------------------------------------------------------------------
def parse_slip(path):
    raw = open(path, encoding="utf-8-sig").read().replace("\r", "")
    label = os.path.splitext(os.path.basename(path))[0]
    hits = list(re.finditer(r"Game ID:\s*(\d+)", raw))
    legs = []
    for i, m in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(raw)
        chunk = raw[m.end():end]
        before = raw[max(0, m.start() - 40):m.start()]
        dm = re.findall(r"\d{1,2}/\d{1,2} \d{1,2}:\d{2}", before)
        lines = [l.strip() for l in chunk.split("\n") if l.strip()]
        if "Pick" not in lines:
            continue
        p = lines.index("Pick")
        head = [l for l in lines[:p]
                if not l.startswith("Check Live") and not l.startswith("Go to Live")]
        k = len(head)
        while k > 0 and (re.fullmatch(r"\d+", head[k - 1]) or head[k - 1] == "--"):
            k -= 1
        if k < 2:
            continue
        away, home = head[k - 1], head[k - 2]
        status = next((l for l in reversed(head[:k - 2]) if l in STATUS_WORDS), "")
        pick_line = lines[p + 1] if p + 1 < len(lines) else ""
        om = re.search(r"@\s*(\d+\.\d+)", pick_line)
        odds = float(om.group(1)) if om else None
        pick_text = re.split(r"\s*@", pick_line)[0].strip()
        market = ""
        if "Market" in lines[p:]:
            mi = lines.index("Market", p)
            market = lines[mi + 1] if mi + 1 < len(lines) else ""
        leg = {"slip": label, "game_id": m.group(1), "date": dm[-1] if dm else "",
               "home": home, "away": away, "sporty": status, "pick": pick_text,
               "odds": odds, "market": market, "kind": "other", "side": "", "player": ""}
        if "Player Not to Score" in market:
            leg["kind"] = "nts"
            pm = re.match(r"(.+?)\s*\((.+)\)\s*$", pick_text)
            leg["player"] = pm.group(1) if pm else pick_text
        elif "Total Bookings" in market:
            leg["kind"] = "cards"
            leg["side"] = "Home" if market.startswith("Home") else "Away"
        elif "1X2 from 1 to 5" in market:
            leg["kind"] = "five_draw"
        elif "Total Goals Over/Under from 1 to 5" in market:
            leg["kind"] = "five_under"
        if odds:
            legs.append(leg)
    return legs


# ---------------------------------------------------------------------------
# 2. model info from the reports
# ---------------------------------------------------------------------------
def load_cards(path):
    rows = {}
    if not path or not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            rows[(r.get("match", ""), r.get("side", ""))] = r
    return rows


def load_five(path):
    out = {}
    if not path or not os.path.exists(path):
        return out
    txt = open(path, encoding="utf-8-sig").read()
    blocks = re.split(r"\n(?=\s*\d+\. \w{3} \d+ \w{3} \d\d:\d\d UTC)", txt)
    for b in blocks:
        m = re.search(r"\n\s+(.+?) vs (.+?)\n\s+price: draw ([\d.]+)", b)
        if not m:
            continue
        g = re.search(r"our estimate: goal in first 5 min ([\d.]+)%", b)
        e = re.search(r"edge ([+-]?[\d.]+) pts", b)
        rk = re.search(r"market rank (\d+) of (\d+)", b)
        fl = re.search(r"FLAGS: (.+)", b)
        out[f"{m.group(1).strip()} vs {m.group(2).strip()}"] = {
            "draw": float(m.group(3)),
            "goal_p": float(g.group(1)) / 100 if g else None,
            "edge": float(e.group(1)) if e else None,
            "rank": int(rk.group(1)) if rk else None,
            "flags": fl.group(1).strip() if fl else "",
        }
    return out


NTS_TAGS = [("rotation_risk", "ROTATION RISK"), ("set_piece", "SET PIECE THREAT"),
            ("penalties", "TAKES PENALTIES"), ("aerial", "AERIAL THREAT"),
            ("high_shots", "HIGH SHOT VOLUME"), ("thin_sample", "THIN SAMPLE"),
            ("injury", "INJURY LISTED"), ("not_playing", "NOT PLAYING"),
            ("sp_mismatch", "SET PIECE MISMATCH")]


def load_nts(path):
    out = {}
    if not path or not os.path.exists(path):
        return out
    lines = open(path, encoding="utf-8-sig").read().split("\n")
    for i, l in enumerate(lines):
        m = re.match(r"^  (\d\.\d\d)  (.+?) \((.+?)\)\s*$", l)
        if not m:
            continue
        blk = []
        j = i + 1
        while j < len(lines) and lines[j].startswith("        "):
            blk.append(lines[j].strip())
            j += 1
        text = " ".join(blk)
        tags = [t for t, key in NTS_TAGS if key in text]
        if "attack-minded for a" in text:
            tags.append("attack_minded")
        xg = re.search(r"xG ([\d.]+) per 90", text)
        out.setdefault(norm(m.group(2)), []).append(
            {"team": m.group(3), "tags": tags, "xg90": xg.group(1) if xg else ""})
    return out


CARDS = load_cards(CARDS_CSV)
FIVE = load_five(FIVE_TXT)
NTS = load_nts(NTS_TXT)


def smooth(hits, played, w):
    return (hits + 0.85 * w) / (played + w)


def attach_model(leg):
    """Adds p_model (our win probability), p_alt (heavier prior), text and tags."""
    leg["p_model"] = None
    leg["p_alt"] = None
    leg["model"] = ""
    leg["tags"] = [leg["kind"]]
    match = f"{leg['home']} vs {leg['away']}"
    f = FIVE.get(match)
    if leg["kind"] in ("five_draw", "five_under"):
        if f:
            leg["p_model"] = None if f["goal_p"] is None else 1 - f["goal_p"]
            leg["model"] = (f"our no-goal {leg['p_model']:.1%} | edge {f['edge']:+} | "
                            f"market rank {f['rank']} | draw {f['draw']}")
            fl = f["flags"]
            if "WE LIKE IT" in fl:
                leg["tags"].append("we_like_market_doesnt")
            if "MARKET LIKES IT" in fl:
                leg["tags"].append("market_likes_we_dont")
            if "OPEN GAME" in fl:
                leg["tags"].append("open_game")
    elif leg["kind"] == "cards":
        r = CARDS.get((match, leg["side"]))
        if r:
            h, n = to_float(r.get("hits")), to_float(r.get("played"))
            if h is not None and n:
                s3, s15 = smooth(h, n, 3), smooth(h, n, 15)
                leg["p_model"], leg["p_alt"] = s3, s15
                leg["model"] = (f"{int(h)}/{int(n)} cards/g {r.get('avg_cards')} | "
                                f"ref {r.get('referee') or '?'} ({r.get('ref_games')}g) | "
                                f"smoothed w3 {s3:.1%} w15 {s15:.1%} | price {1 / leg['odds']:.1%}")
                if n < 6:
                    leg["tags"].append("small_sample")
                if h == n:
                    leg["tags"].append("perfect_streak")
                if (to_float(r.get("ref_games")) or 0) < 3:
                    leg["tags"].append("ref_thin")
                if s15 < 1 / leg["odds"]:
                    leg["tags"].append("no_edge_w15")
    elif leg["kind"] == "nts":
        cands = NTS.get(norm(leg["player"]), [])
        c = None
        both = norm(leg["home"] + " " + leg["away"])
        for x in cands:
            if norm(x["team"]) in both or any(w in both for w in norm(x["team"]).split()):
                c = x
                break
        if c is None and cands:
            c = cands[0]
        if c:
            leg["tags"] += c["tags"]
            leg["model"] = f"xG/90 {c['xg90'] or '?'} | flags: {', '.join(c['tags']) or 'none'}"
        if f and f["draw"] >= OPEN_GAME_DRAW:
            leg["tags"].append("open_game")
            leg["model"] += f" | 5-min draw price {f['draw']} (rank {f['rank']})"
    return leg


# ---------------------------------------------------------------------------
# 3. FotMob: find the match and read goals / cards
# ---------------------------------------------------------------------------
def variants(name):
    out = []
    for v in (ALIASES.get(name, name), name, SUFFIX.sub("", name).strip(),
              " ".join(name.split()[:2])):
        v = " ".join(v.split())
        if v and v not in out:
            out.append(v)
    return out


def team_ids(name):
    ids = []
    for v in variants(name):
        try:
            cands = candidates(v)
        except Exception:
            cands = []
        for c in cands[:3]:
            if NOT_FIRST_TEAM.search(str(c.get("name", ""))):
                continue
            cid = str(c["id"])
            if cid not in ids:
                ids.append(cid)
        if len(ids) >= 4:
            break
    return ids


def to_naive_utc(d):
    if not isinstance(d, datetime):
        return None
    if d.tzinfo:
        d = d.astimezone(timezone.utc).replace(tzinfo=None)
    return d


def slip_when(leg):
    m = re.match(r"(\d{1,2})/(\d{1,2}) (\d{1,2}):(\d{2})", leg["date"] or "")
    if m:
        try:
            return datetime(YEAR, int(m.group(2)), int(m.group(1)), int(m.group(3)), int(m.group(4)))
        except ValueError:
            pass
    return datetime.utcnow()


def find_match(leg):
    """Find the FotMob match. Same lookup as five_min.py first, then a fallback.
    The match must be within 36h of the slip date, otherwise it counts as not found."""
    when = slip_when(leg)
    res = None
    try:
        res, _why = resolve_fixture({"home": ALIASES.get(leg["home"], leg["home"]),
                                     "away": ALIASES.get(leg["away"], leg["away"]),
                                     "kickoff": when.replace(tzinfo=timezone.utc)})
    except Exception:
        res = None
    if res:
        hids, aids = [str(res["home_id"])], {str(res["away_id"])}
    else:
        hids, aids = team_ids(leg["home"]), set(team_ids(leg["away"]))
    best = None
    for h in hids[:3]:
        try:
            page = team_page(h)
        except Exception:
            continue
        for m in (page_matches(page) if page else []):
            if str(m["home_id"]) == h and str(m["away_id"]) in aids and not m.get("cancelled"):
                d = to_naive_utc(m["date"])
                gap = abs((d - when).total_seconds()) if d else 10 ** 9
                if gap <= MATCH_WINDOW and (best is None or gap < best[0]):
                    best = (gap, m)
        if best:
            break
    return best[1] if best else None


def own_flag(e):
    return bool(e.get("ownGoal")) or bool(e.get("isOwnGoal")) or \
        "own" in str(e.get("goalDescription") or "").lower()


def extract_events(data):
    evs, seen = [], set()

    def add(t, time, home, name, card, own):
        key = (t, time, home, card)
        if key in seen:
            return
        seen.add(key)
        evs.append({"type": t, "time": time, "home": home, "name": name, "card": card, "own": own})

    def walk(o):
        if isinstance(o, dict):
            for k in ("homeTeamGoals", "awayTeamGoals"):
                v = o.get(k)
                if isinstance(v, dict):
                    for pname, lst in v.items():
                        for e in (lst or []):
                            if isinstance(e, dict) and isinstance(e.get("time"), (int, float)):
                                add("Goal", int(e["time"]), k.startswith("home"), pname, "", own_flag(e))
            t = o.get("type")
            if t in ("Goal", "Card") and isinstance(o.get("time"), (int, float)) and "isHome" in o:
                pl = o.get("player") or {}
                name = (o.get("nameStr") or o.get("fullName") or o.get("playerName")
                        or pl.get("name") or pl.get("fullName") or "")
                add(t, int(o["time"]), bool(o["isHome"]), name, str(o.get("card") or ""), own_flag(o))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    return evs


def fetch_info(mid):
    key = str(mid)
    if key in CACHE:
        return CACHE[key]
    data = client.get_json("/api/data/matchDetails", {"matchId": mid}, use_cache=False)
    st = ((data.get("header") or {}).get("status")) or {}
    lt = st.get("liveTime") or {}
    info = {"events": extract_events(data), "finished": bool(st.get("finished")),
            "started": bool(st.get("started")), "score": st.get("scoreStr") or "",
            "live": str(lt.get("short") or lt.get("long") or (st.get("reason") or {}).get("short") or "")}
    if info["finished"]:
        CACHE[key] = info
    return info


def lookup(leg):
    if NO_FOTMOB:
        return None, None, "no FotMob"
    try:
        m = find_match(leg)
        if not m:
            return None, None, "match not found on FotMob"
        return m, fetch_info(m["id"]), ""
    except Exception as e:
        return None, None, f"FotMob error: {e}"


# ---------------------------------------------------------------------------
# 4. grade each leg ourselves (used as a check, and when SportyBet has not settled it)
# ---------------------------------------------------------------------------
def name_match(pick, scorer):
    a, b = norm(pick).split(), norm(scorer).split()
    if not a or not b or a[-1] not in b:
        return False
    if len(a) == 1:
        return True
    return len(set(a) & set(b)) >= 2 or a[0][0] == b[0][0]


def minutes_played(info):
    if info["finished"]:
        return 999
    if not info["started"]:
        return 0
    live = info["live"]
    if "HT" in live or "FT" in live:
        return 45
    m = re.search(r"\d+", live)
    return int(m.group(0)) if m else 1


def grade(leg, info):
    ev, done = info["events"], info["finished"]
    goals = [e for e in ev if e["type"] == "Goal"]
    if leg["kind"] == "nts":
        if goals and not any(e["name"] for e in goals):
            return "pending", "FotMob goals have no scorer names (cannot grade)"
        sc = [e for e in goals if not e["own"] and name_match(leg["player"], e["name"])]
        if sc:
            return "L", "scored " + ", ".join(f"{e['time']}'" for e in sc)
        return ("W", "no goal") if done else ("pending", f"live {info['live']}")
    if leg["kind"] == "cards":
        want_home = leg["side"] == "Home"
        cards = [e for e in ev if e["type"] == "Card" and e["home"] == want_home]
        if cards:
            return "W", "cards " + ", ".join(f"{e['time']}'{e['card'][:1]}" for e in cards)
        return ("L", "0 cards") if done else ("pending", f"live {info['live']}")
    if leg["kind"] in ("five_draw", "five_under"):
        early = [e for e in goals if e["time"] <= 5]
        edge_goals = [e["time"] for e in goals if e["time"] in (5, 6)]
        note = f" (goal at {edge_goals}' - check 5th-minute rule)" if edge_goals else ""
        past5 = minutes_played(info) > 5
        if leg["kind"] == "five_under":
            if early:
                return "L", "goal at " + ", ".join(f"{e['time']}'" for e in early) + note
            return ("W", "no goal in 1-5" + note) if past5 else ("pending", "not past 5 min")
        h = sum(1 for e in early if e["home"])
        a = len(early) - h
        if past5:
            return ("W" if h == a else "L"), f"{h}-{a} after 5" + note
        return "pending", "not past 5 min"
    return "pending", "unknown market"


# ---------------------------------------------------------------------------
# 5. run
# ---------------------------------------------------------------------------
if "--dump" in opts:
    d = client.get_json("/api/data/matchDetails", {"matchId": opts["--dump"]}, use_cache=False)
    print("top-level keys:", list(d.keys()))
    print("header.status:", json.dumps((d.get("header") or {}).get("status"), indent=1)[:800])
    for e in extract_events(d):
        print(e)
    sys.exit(0)

if not args:
    print(__doc__)
    sys.exit(1)

print(f"reports read: 5-min {len(FIVE)} fixtures | NTS {sum(len(v) for v in NTS.values())} players | "
      f"cards {len(CARDS)} rows")
if FIVE_TXT and not FIVE:
    print(f"WARNING: nothing could be read from {FIVE_TXT} (wrong path, or not the 5-min report text)")
if NTS_TXT and not NTS:
    print(f"WARNING: nothing could be read from {NTS_TXT}")
if CARDS_CSV and not CARDS and os.path.exists(CARDS_CSV) is False:
    print(f"NOTE: {CARDS_CSV} not found, so cards legs get no model info")

legs = []
for p in args:
    if not os.path.exists(p):
        print(f"Can't find {p}")
        sys.exit(1)
    legs += parse_slip(p)
if not legs:
    print("No legs found. Check that the slip text was pasted as-is.")
    sys.exit(1)

for leg in legs:
    attach_model(leg)

print(f"{len(legs)} legs read from {len(args)} slip file(s). Looking up matches...\n")
with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    looked = list(ex.map(lookup, legs))
with open(CACHE_FILE, "w", encoding="utf-8") as f:
    json.dump(CACHE, f)

MAP = {"Won": "W", "Lost": "L", "Void": "V"}
rows = []
for leg, (m, info, err) in zip(legs, looked):
    sp = MAP.get(leg["sporty"], "")
    mine, detail = None, ""
    if info is not None:
        mine, detail = grade(leg, info)
        if info["finished"] and not info["events"]:
            detail += " [NO EVENTS FOUND]"
    else:
        detail = f"({err})"
    final = sp if sp else (mine or "pending")
    leg.update({
        "spk": sp, "mine": mine, "grade": final, "detail": detail, "info": info,
        "fm_id": str(m["id"]) if m else "",
        "fm_date": f"{m['date']:%Y-%m-%d %H:%M}" if m and isinstance(m.get("date"), datetime) else "",
        "mismatch": bool(sp in ("W", "L") and mine in ("W", "L") and sp != mine),
        "found": info is not None,
    })
    rows.append(leg)

print(f"{'SLIP':<12}{'GR':<7}{'ODDS':>5}  {'PICK':<30}{'MATCH':<44}DETAIL")
print("-" * 140)
for r in rows:
    if ONLY_LOST and r["grade"] != "L":
        continue
    gr = r["grade"] + ("*" if not r["spk"] and r["grade"] in ("W", "L") else "") + ("!!" if r["mismatch"] else "")
    print(f"{r['slip'][:11]:<12}{gr:<7}{r['odds']:>5.2f}  {r['pick'][:29]:<30}"
          f"{(r['home'] + ' v ' + r['away'])[:43]:<44}{r['detail']}  [{r['fm_id']}]")
    if r["grade"] == "L":
        print(f"{'':<12}{'':<7}{'':>5}  -> {r['model'] or 'no model info'} | tags: {', '.join(r['tags'])}")

bad = [r for r in rows if r["mismatch"]]
if bad:
    print("\nMISMATCHES: SportyBet and FotMob disagree (SportyBet is used in the log)")
    for r in bad:
        print(f"  {r['slip']} | {r['pick']} | {r['home']} v {r['away']} | SportyBet {r['spk']} vs FotMob {r['mine']} "
              f"| FotMob match {r['fm_id']} on {r['fm_date']}")
        for e in r["info"]["events"]:
            print(f"      {e['type']:<5} {e['time']:>3}' {'home' if e['home'] else 'away'} "
                  f"name='{e['name']}' card='{e['card']}' own={e['own']}")

missing = [r for r in rows if not r["found"]]
if missing:
    print(f"\nNOT FOUND ON FOTMOB ({len(missing)}): graded from SportyBet only")
    for r in missing:
        print(f"  {r['slip']} | {r['pick']} | {r['home']} v {r['away']} | {r['detail']}")

pend = [r for r in rows if r["grade"] == "pending"]
done = [r for r in rows if r["grade"] in ("W", "L")]
print(f"\nSUMMARY (void and pending legs left out; {len(pend)} pending)")
for slip in sorted({r["slip"] for r in done}):
    rs = [r for r in done if r["slip"] == slip]
    lost = sum(1 for r in rs if r["grade"] == "L")
    exp = sum(1 - 1 / r["odds"] for r in rs)
    line = f"  {slip}: {len(rs)} settled, {lost} lost | expected losses at the price {exp:.2f}"
    mod = [r for r in rs if r["p_model"] is not None]
    if mod:
        line += f" | model said {sum(1 - r['p_model'] for r in mod):.2f} (on {len(mod)} legs)"
    alt = [r for r in rs if r["p_alt"] is not None]
    if alt:
        line += f" | heavier prior (w15) said {sum(1 - r['p_alt'] for r in alt):.2f}"
    print(line)

tags = defaultdict(list)
for r in done:
    for t in r["tags"]:
        tags[t].append(r)
print("\nBY TAG (n settled, lost, expected losses at the price)")
for t, rs in sorted(tags.items(), key=lambda kv: -len(kv[1])):
    lost = sum(1 for r in rs if r["grade"] == "L")
    exp = sum(1 - 1 / r["odds"] for r in rs)
    print(f"  {t:<24} n={len(rs):<3} lost={lost:<2} expected={exp:.2f}")

# results log: appended once per (game, market, pick); settled legs only
fields = ["date", "slip", "game_id", "market", "match", "pick", "odds", "grade", "sportybet", "fotmob",
          "detail", "p_implied", "p_model", "p_w15", "tags", "model", "fm_id"]
write_header = True
if os.path.exists(LOG_FILE):
    with open(LOG_FILE, encoding="utf-8-sig", newline="") as f:
        first = f.readline().strip()
    if first == ",".join(fields):
        write_header = False
    else:
        os.replace(LOG_FILE, LOG_FILE + ".old")
have = set()
if not write_header:
    with open(LOG_FILE, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            have.add((r["game_id"], r["market"], r["pick"]))
new = [r for r in rows if r["grade"] in ("W", "L", "V")
       and (r["game_id"], r["market"], r["pick"]) not in have]
with open(LOG_FILE, "a", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    if write_header:
        w.writerow(fields)
    for r in new:
        dm = re.match(r"(\d{1,2})/(\d{1,2})", r["date"] or "")
        date = f"{YEAR}-{int(dm.group(2)):02d}-{int(dm.group(1)):02d}" if dm else ""
        w.writerow([date, r["slip"], r["game_id"], r["market"], f"{r['home']} vs {r['away']}",
                    r["pick"], r["odds"], r["grade"], r["spk"], r["mine"] or "", r["detail"],
                    round(1 / r["odds"], 4),
                    "" if r["p_model"] is None else round(r["p_model"], 4),
                    "" if r["p_alt"] is None else round(r["p_alt"], 4),
                    "|".join(r["tags"]), r["model"], r["fm_id"]])
print(f"\nLogged {len(new)} new settled leg(s) to {LOG_FILE}.")