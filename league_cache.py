"""Fill shots_cache.json for a whole league (this season) and print baselines.
Usage: python league_cache.py 47      (Premier League)
       python league_cache.py 48      (Championship)
Resumable: skips matches already cached, saves every 15."""
import json
import sys
import time
from pathlib import Path
from statistics import mean, median

from fotmob_mcp.client import FotMobClient
from five_min import league_matches

client = FotMobClient()
LID = int(sys.argv[1])
RAW = Path("probe_raw")
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
    return client.get_json("/api/data/matchDetails", {"matchId": mid}, use_cache=False)


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


ms = [m for m in league_matches(LID) if m["finished"] and not m["cancelled"]]
todo = [m for m in ms if str(m["id"]) not in cache]
print(f"league {LID}: {len(ms)} finished, {len(todo)} to fetch, {len(ms) - len(todo)} cached")

failed = []
for i, m in enumerate(todo, 1):
    mid = str(m["id"])
    d = None
    for attempt in range(3):
        try:
            d = load_match(mid)
            break
        except Exception:
            time.sleep(1 + attempt * 2)
    if d is None:
        failed.append(mid)
        continue
    cache[mid] = compact(d)
    if i % 15 == 0:
        CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        print(f"  {i}/{len(todo)}")
    time.sleep(0.2)
CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
print("failed:", failed if failed else "none")

# ---------------------------------------------------------------- baselines
league_ids = {str(m["id"]) for m in ms}
rows = [cache[mid] for mid in league_ids if mid in cache]
nostats = [r for r in rows if not r.get("has_stats")]
rows = [r for r in rows if r.get("has_stats") and num(r["shots"][0]) is not None
        and num(r["shots"][1]) is not None]
print(f"\nmatches with stats: {len(rows)} | without stats: {len(nostats)}")
if not rows:
    sys.exit()

hs = [num(r["shots"][0]) for r in rows]
as_ = [num(r["shots"][1]) for r in rows]
hsot = [num(r["sot"][0]) for r in rows if num(r["sot"][0]) is not None]
asot = [num(r["sot"][1]) for r in rows if num(r["sot"][1]) is not None]
print("\nLEAGUE BASELINE (per team per match)")
print(f"  home shots: mean {mean(hs):.1f} med {median(hs):.1f}")
print(f"  away shots: mean {mean(as_):.1f} med {median(as_):.1f}")
print(f"  all shots : mean {mean(hs + as_):.1f} med {median(hs + as_):.1f}")
print(f"  SoT home {mean(hsot):.1f} | SoT away {mean(asot):.1f}")

T = {}
for r in rows:
    for side in (0, 1):
        tid = r["home_id"] if side == 0 else r["away_id"]
        nm = r["home"] if side == 0 else r["away"]
        T.setdefault(tid, {"name": nm, "rows": []})["rows"].append(
            (side == 0, num(r["shots"][side]), num(r["shots"][1 - side])))


def m_(v):
    return f"{mean(v):.1f}" if v else "-"


print("\nTEAM TABLE (shots for / against, this season)")
print(f"{'team':<26}{'n':<4}{'SF':<7}{'SA':<7}{'homeSF(n)':<12}{'awaySF(n)':<12}{'homeSA':<8}{'awaySA':<8}")
table = []
for tid, t in T.items():
    R = t["rows"]
    table.append((mean([x[1] for x in R]), t["name"], R))
for _, nm, R in sorted(table, reverse=True):
    hf = [x[1] for x in R if x[0]]
    af = [x[1] for x in R if not x[0]]
    ha = [x[2] for x in R if x[0]]
    aa = [x[2] for x in R if not x[0]]
    print(f"{str(nm)[:25]:<26}{len(R):<4}{m_([x[1] for x in R]):<7}{m_([x[2] for x in R]):<7}"
          f"{m_(hf) + '(' + str(len(hf)) + ')':<12}{m_(af) + '(' + str(len(af)) + ')':<12}{m_(ha):<8}{m_(aa):<8}")
print("\nDONE")