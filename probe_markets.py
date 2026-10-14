"""
probe_markets.py - what does FotMob give us this season for each team,
and can we research the 13 markets from it?

Run:  python probe_markets.py "Arsenal" "Lille"
      python probe_markets.py "Remo Stars" "Enyimba"
Optional 3rd arg = season start date (default 2026-07-01).
"""
import sys
from datetime import datetime, timezone

from five_min import client, candidates, team_page, page_matches, parse_goals

SEASON_START = sys.argv[3] if len(sys.argv) > 3 else "2026-07-01"
START = datetime.fromisoformat(SEASON_START).replace(tzinfo=timezone.utc)


def aware(d):
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def find_keys(o, pred, path="", out=None, depth=0):
    if out is None:
        out = []
    if depth > 14 or len(out) >= 12:
        return out
    if isinstance(o, dict):
        for k, v in o.items():
            p = f"{path}.{k}"
            if pred(str(k)) and not isinstance(v, (dict, list)):
                out.append((p, v))
            find_keys(v, pred, p, out, depth + 1)
    elif isinstance(o, list):
        for i, v in enumerate(o[:60]):
            find_keys(v, pred, f"{path}[{i}]", out, depth + 1)
    return out


def count_cards(o, acc):
    if isinstance(o, dict):
        if str(o.get("type") or "").lower() == "card" and "isHome" in o:
            side = "home" if o.get("isHome") else "away"
            acc.setdefault(side, []).append(str(o.get("card") or o.get("cardType") or "?"))
        for v in o.values():
            count_cards(v, acc)
    elif isinstance(o, list):
        for v in o:
            count_cards(v, acc)
    return acc


def team_report(name):
    print("=" * 70)
    print("TEAM:", name)
    cands = candidates(name)
    if not cands:
        print("  NOT FOUND on FotMob search")
        return
    c = cands[0]
    tid = str(c["id"])
    print(f"  resolved: {c.get('name')} (id {tid}) | other candidates: {len(cands) - 1}")
    page = team_page(tid)
    if not page:
        print("  team page unavailable")
        return
    ms = [m for m in page_matches(page)
          if m["finished"] and not m["cancelled"] and m["score"] is not None
          and aware(m["date"]) >= START and tid in (m["home_id"], m["away_id"])]
    ms.sort(key=lambda m: m["date"])
    home = [m for m in ms if m["home_id"] == tid]
    away = [m for m in ms if m["away_id"] == tid]
    print(f"  finished matches since {SEASON_START}: {len(ms)} (home {len(home)}, away {len(away)})")
    print("  (team page can't tell friendlies/cups apart - check the list below)")
    for m in ms[-8:]:
        print(f"    {m['date'].date()}  {m['home']} {m['score'][0]}-{m['score'][1]} {m['away']}")
    if not ms:
        print("  NO DATA this season")
        return

    m = ms[-1]
    print(f"\n  sampling matchDetails of latest match: {m['home']} v {m['away']} (id {m['id']})")
    try:
        data = client.get_json("/api/data/matchDetails", {"matchId": m["id"]}, use_cache=False)
    except Exception as e:
        print("  matchDetails failed:", e)
        return

    goals = parse_goals(data)
    total = m["score"][0] + m["score"][1]
    h1 = [g for g in goals if g[0] <= 45]
    print(f"  goal timeline: {len(goals)} goals parsed vs score total {total} "
          f"-> {'OK' if len(goals) == total else 'MISMATCH'} | 1H {len(h1)} / 2H {len(goals) - len(h1)}")

    cards = count_cards(data, {})
    print(f"  cards found: home {cards.get('home', [])} | away {cards.get('away', [])}")

    xg = find_keys(data, lambda k: "xg" in k.lower() or "expected_goals" in k.lower())
    print(f"  xG keys: {len(xg)}")
    for p, v in xg[:5]:
        print(f"    {p} = {v}")

    fm = find_keys(data, lambda k: k.lower() == "formation")
    print(f"  formation: {[v for _, v in fm[:2]]}")

    stats = find_keys(data, lambda k: k.lower() in ("total_shots", "shots", "shotsontarget", "ShotsOnTarget".lower()))
    print(f"  shot keys: {len(stats)}")

    ok_res = True
    ok_goals = len(goals) == total
    ok_cards = bool(cards)
    ok_xg = bool(xg)
    print("\n  MARKET FEASIBILITY (from this sample match):")
    print(f"    Home/Draw, Draw/Away, 1UP versions (results)      : {'OK' if ok_res else 'NO DATA'}")
    print(f"    Win to Nil, Full match O0.5 (scores)              : {'OK' if ok_res else 'NO DATA'}")
    print(f"    2nd half O0.5 (goal minutes)                      : {'OK' if ok_goals else 'CHECK'}")
    print(f"    No team 3+ in row (goal order)                    : {'OK' if ok_goals else 'CHECK'}")
    print(f"    Team cards O0.5 (card events)                     : {'OK' if ok_cards else 'CHECK (zero cards or no data)'}")
    print(f"    Style: xG                                         : {'OK' if ok_xg else 'NO DATA'}")
    print(f"    Style: formation                                  : {'OK' if fm else 'NO DATA'}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print('usage: python probe_markets.py "Home Team" "Away Team" [season_start YYYY-MM-DD]')
        sys.exit(1)
    team_report(sys.argv[1])
    team_report(sys.argv[2])