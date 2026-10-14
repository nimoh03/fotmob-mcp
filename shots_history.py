"""Team shots profile, this season only.
Usage:
  python shots_history.py "Arsenal"
  python shots_history.py "Cardiff City" --n=8 --lines=12.5,14.5 --sotlines=4.5,5.5
  python shots_history.py "Arsenal" --league-only     (drop cups/Europe)
Shows: shots for/against, SoT, xG with mean/median/sd/min/max, split All/Home/Away,
line hit rates, match list. n is on every line."""
import json
import sys
from collections import Counter
from pathlib import Path
from statistics import mean, median, pstdev

from fotmob_mcp.client import FotMobClient
from five_min import candidates, team_page, page_matches

client = FotMobClient()
argv = sys.argv[1:]
args = [a for a in argv if not a.startswith("--")]


def opt(name, default=None):
    return next((a.split("=", 1)[1] for a in argv if a.startswith(f"--{name}=")), default)


N = int(opt("n", 0)) or None
LINES = [float(x) for x in opt("lines", "").split(",") if x]
SOT_LINES = [float(x) for x in opt("sotlines", "").split(",") if x]
LEAGUE_ONLY = "--league-only" in argv

RAW = Path("probe_raw")
RAW.mkdir(exist_ok=True)
CACHE = Path("shots_cache.json")
cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}


def collect_stats(o, out):
    if isinstance(o, dict):
        v = o.get("stats")
        if o.get("key") and isinstance(v, list) and len(v) == 2 \
                and not any(isinstance(x, (dict, list)) for x in v):
            out[o["key"]] = v
        for x in o.values():
            collect_stats(x, out)
    elif isinstance(o, list):
        for x in o:
            collect_stats(x, out)


def period_stats(per, name):
    out = {}
    collect_stats((per or {}).get(name), out)
    return out


def num(x):
    try:
        return float(x)
    except Exception:
        return None


def load_match(mid):
    p = RAW / f"{mid}.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    d = client.get_json("/api/data/matchDetails", {"matchId": mid}, use_cache=False)
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return d


def compact(d):
    gen = d.get("general") or {}
    c = d.get("content") or {}
    per = (c.get("stats") or {}).get("Periods") or {}
    a, h1, h2 = period_stats(per, "All"), period_stats(per, "FirstHalf"), period_stats(per, "SecondHalf")
    shots = (c.get("shotmap") or {}).get("shots") or []
    g = lambda k, s: (s.get(k) or [None, None])
    return {
        "date": gen.get("matchTimeUTCDate"),
        "league": gen.get("leagueName"),
        "parent": gen.get("parentLeagueId"),
        "home_id": str((gen.get("homeTeam") or {}).get("id")),
        "away_id": str((gen.get("awayTeam") or {}).get("id")),
        "home": (gen.get("homeTeam") or {}).get("name"),
        "away": (gen.get("awayTeam") or {}).get("name"),
        "shots": g("total_shots", a), "sot": g("ShotsOnTarget", a),
        "blocked": g("blocked_shots", a), "xg": g("expected_goals", a),
        "red": g("red_cards", a),
        "shots_1h": g("total_shots", h1), "shots_2h": g("total_shots", h2),
        "shotmap_n": len(shots),
        "has_stats": bool(a),
    }


# ------------------------------------------------------------ team + matches
name = args[0]
cands = candidates(name)
tid = str(cands[0]["id"])
print("team:", cands[0].get("name"), tid)
page = team_page(tid)
done = sorted((m for m in page_matches(page) if m["finished"] and not m["cancelled"]),
              key=lambda m: m["date"], reverse=True)
print("finished on team page:", len(done))

fetched = 0
for m in done:
    mid = str(m["id"])
    if mid in cache:
        continue
    try:
        cache[mid] = compact(load_match(mid))
        fetched += 1
    except Exception as e:
        print("fetch failed", mid, repr(e)[:100])
CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
print(f"fetched {fetched} new, {len(done) - fetched} from cache")

rows = []
for m in done:
    r = cache.get(str(m["id"]))
    if not r or not r.get("has_stats"):
        continue
    if "friendl" in str(r["league"]).lower():
        continue
    i = 0 if r["home_id"] == tid else 1
    j = 1 - i
    rows.append({
        "date": str(r["date"])[:10], "home": i == 0,
        "opp": r["away"] if i == 0 else r["home"],
        "comp": r["league"], "parent": r["parent"],
        "sf": r["shots"][i], "sa": r["shots"][j],
        "tf": r["sot"][i], "ta": r["sot"][j],
        "xf": num(r["xg"][i]), "xa": num(r["xg"][j]),
        "h1": r["shots_1h"][i], "h2": r["shots_2h"][i],
        "red": (num(r["red"][0]) or 0) + (num(r["red"][1]) or 0) > 0,
    })

if LEAGUE_ONLY and rows:
    main_parent = Counter(r["parent"] for r in rows).most_common(1)[0][0]
    rows = [r for r in rows if r["parent"] == main_parent]
    print("league-only: parentLeagueId", main_parent)
if N:
    rows = rows[:N]

# ------------------------------------------------------------ summaries
def summ(label, vals, fmt="{:.1f}"):
    v = [x for x in vals if x is not None]
    if not v:
        print(f"  {label:<20} n=0")
        return
    flag = "  <-- small sample" if len(v) < 5 else ""
    print(f"  {label:<20} n={len(v):<3} mean={fmt.format(mean(v))} med={fmt.format(median(v))} "
          f"sd={fmt.format(pstdev(v))} min={fmt.format(min(v))} max={fmt.format(max(v))}{flag}")


groups = [("ALL", rows), ("HOME", [r for r in rows if r["home"]]), ("AWAY", [r for r in rows if not r["home"]])]
print("\n" + "=" * 90)
print(f"{cands[0].get('name')}  -  {len(rows)} matches this season")
for gname, g in groups:
    print(f"\n[{gname}]")
    summ("shots for", [r["sf"] for r in g])
    summ("shots against", [r["sa"] for r in g])
    summ("SoT for", [r["tf"] for r in g])
    summ("SoT against", [r["ta"] for r in g])
    summ("xG for", [r["xf"] for r in g], "{:.2f}")
    summ("xG against", [r["xa"] for r in g], "{:.2f}")
    summ("shots 1H", [r["h1"] for r in g])
    summ("shots 2H", [r["h2"] for r in g])


def hit_table(title, key, lines):
    print(f"\n{title}")
    for L in lines:
        parts = []
        for gname, g in groups:
            v = [r[key] for r in g if r[key] is not None]
            k = sum(1 for x in v if x > L)
            pct = f"{100 * k / len(v):.0f}%" if v else "-"
            parts.append(f"{gname} {k}/{len(v)} ({pct})")
        print(f"  over {L:<5} " + " | ".join(parts))


if LINES:
    hit_table("TEAM SHOTS over line (raw hit rate, no smoothing)", "sf", LINES)
if SOT_LINES:
    hit_table("TEAM SoT over line", "tf", SOT_LINES)

print("\n" + "=" * 90)
print(f"{'date':<11}{'H/A':<4}{'opponent':<22}{'comp':<18}{'shots':<9}{'SoT':<8}{'xG':<12}{'1H/2H':<8}{'red?':<5}")
for r in rows:
    xg = f"{r['xf']:.2f}-{r['xa']:.2f}" if r["xf"] is not None and r["xa"] is not None else "-"
    print(f"{r['date']:<11}{'H' if r['home'] else 'A':<4}{str(r['opp'])[:21]:<22}{str(r['comp'])[:17]:<18}"
          f"{str(r['sf']) + '-' + str(r['sa']):<9}{str(r['tf']) + '-' + str(r['ta']):<8}{xg:<12}"
          f"{str(r['h1']) + '/' + str(r['h2']):<8}{'RED' if r['red'] else '':<5}")
print("\nNote: cups/Europe and opponent quality are NOT adjusted yet.")
print("DONE")