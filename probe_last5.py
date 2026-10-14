"""Last N competitive matches for a team, this season, shots table.
Usage: python probe_last5.py "Arsenal" [--n=5]
Reads probe_raw/ first, fetches only what's missing. Writes shots_cache.json."""
import json
import sys
from pathlib import Path

from fotmob_mcp.client import FotMobClient
from five_min import candidates, team_page, page_matches

client = FotMobClient()
args = [a for a in sys.argv[1:] if not a.startswith("--")]
N = int(next((a.split("=")[1] for a in sys.argv[1:] if a.startswith("--n=")), 5))
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


def compact(mid, d):
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


name = args[0]
cands = candidates(name)
tid = str(cands[0]["id"])
print("team:", cands[0].get("name"), tid)
page = team_page(tid)
done = sorted((m for m in page_matches(page) if m["finished"] and not m["cancelled"]),
              key=lambda m: m["date"], reverse=True)
print("finished on team page:", len(done))

rows = []
for m in done:
    if len(rows) >= N:
        break
    mid = str(m["id"])
    if mid not in cache:
        try:
            cache[mid] = compact(mid, load_match(mid))
        except Exception as e:
            print("fetch failed", mid, repr(e)[:100])
            continue
    r = cache[mid]
    if "friendl" in str(r["league"]).lower():
        continue
    rows.append((m, r))
CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

print("\n" + "=" * 100)
print(f"{'date':<11}{'H/A':<4}{'opponent':<22}{'comp':<20}{'score':<8}{'shots F-A':<11}"
      f"{'SoT F-A':<9}{'xG F-A':<12}{'1H/2H (F)':<11}{'red F-A':<8}")
tf = ta = 0
for m, r in rows:
    i = 0 if r["home_id"] == tid else 1
    j = 1 - i
    opp = r["away"] if i == 0 else r["home"]
    f, a = r["shots"][i], r["shots"][j]
    tf += f or 0
    ta += a or 0
    sc = m["score"]
    sc_txt = f"{sc[i]}-{sc[j]}" if sc else "?"
    xg = f"{r['xg'][i]}-{r['xg'][j]}"
    half = f"{r['shots_1h'][i]}/{r['shots_2h'][i]}"
    print(f"{str(r['date'])[:10]:<11}{'H' if i == 0 else 'A':<4}{str(opp)[:21]:<22}{str(r['league'])[:19]:<20}"
          f"{sc_txt:<8}{str(f) + '-' + str(a):<11}{str(r['sot'][i]) + '-' + str(r['sot'][j]):<9}"
          f"{xg:<12}{half:<11}{str(r['red'][i]) + '-' + str(r['red'][j]):<8}")
n = len(rows)
if n:
    print(f"\navg shots for {tf / n:.1f} | against {ta / n:.1f} | n={n}")
print("shotmap count == total_shots?",
      all(sum(1 for _ in [0]) and r['shotmap_n'] == sum(x or 0 for x in r['shots']) for _, r in rows))
print("DONE")