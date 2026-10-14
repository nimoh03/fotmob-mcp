"""
probe_markets2.py - digs into the 4 open questions from probe_markets.py

Run:  python probe_markets2.py "Arsenal" "Enyimba" "Remo Stars"
"""
import re
import sys
from collections import Counter

from five_min import client, candidates, team_page, find_matches, norm_match

STAT_WORDS = re.compile(r"xg|expected|shot|corner|possession|foul|card", re.I)


def walk(o, fn, path="", depth=0):
    if depth > 18:
        return
    fn(o, path)
    if isinstance(o, dict):
        for k, v in o.items():
            walk(v, fn, f"{path}.{k}", depth + 1)
    elif isinstance(o, list):
        for i, v in enumerate(o[:300]):
            walk(v, fn, f"{path}[{i}]", depth + 1)


def short(x, n=140):
    s = str(x)
    return s if len(s) <= n else s[:n] + "..."


def part_a(name):
    print("=" * 72)
    print("TEAM:", name)
    cands = candidates(name)
    if not cands:
        print("  not found in search")
        return None
    c = cands[0]
    tid = str(c["id"])
    print(f"  resolved: {c.get('name')} id={tid}")
    page = team_page(tid)
    if not page:
        print("  no team page")
        return None
    print("  page top-level keys:", list(page.keys())[:15])
    det = page.get("details") or {}
    print("  details:", short({k: det.get(k) for k in
          ("name", "country", "primaryLeagueId", "primaryLeagueName", "latestSeason")}, 300))
    raw = find_matches(page, [])
    print(f"  raw match dicts on page: {len(raw)}")
    if raw:
        print("  keys of one raw match dict:", list(raw[0].keys()))
        for k in ("tournament", "leagueId", "leagueName", "tournamentName", "league"):
            if k in raw[0]:
                print(f"    {k} = {short(raw[0][k])}")
    norm = [n for n in (norm_match(m) for m in raw) if n]
    norm.sort(key=lambda m: m["date"])
    if norm:
        print(f"  date range on page: {norm[0]['date'].date()} -> {norm[-1]['date'].date()}")
        for m in norm[-6:]:
            sc = f"{m['score'][0]}-{m['score'][1]}" if m["score"] else "?"
            print(f"    {m['date'].date()} fin={m['finished']} {m['home']} {sc} {m['away']}")
    fin = [m for m in norm if m["finished"] and m["score"] is not None
           and tid in (m["home_id"], m["away_id"])]
    return (fin[-1], raw) if fin else None


def part_b(m):
    print(f"\n  matchDetails: {m['home']} v {m['away']} (id {m['id']})")
    data = client.get_json("/api/data/matchDetails", {"matchId": m["id"]}, use_cache=False)
    content = data.get("content") or {}
    print("  content keys:", list(content.keys()))
    gen = data.get("general") or {}
    print("  general:", short({k: gen.get(k) for k in
          ("leagueName", "leagueId", "parentLeagueName", "matchRound", "finished")}, 300))

    types = Counter()
    cards = []

    def ev(o, p):
        if isinstance(o, dict) and isinstance(o.get("type"), str) and "time" in o:
            types[o["type"]] += 1
            if o["type"].lower() == "card":
                cards.append(o)
    walk(data, ev)
    print("  event types seen:", dict(types))

    seen, uniq = set(), []
    for c in cards:
        key = (c.get("time"), c.get("isHome"), str(c.get("playerId") or c.get("nameStr")), str(c.get("card")))
        if key not in seen:
            seen.add(key)
            uniq.append(c)
    print(f"  card events raw={len(cards)} deduped={len(uniq)}")
    for c in uniq[:8]:
        print("    ", short({k: c.get(k) for k in
              ("time", "isHome", "card", "cardDescription", "nameStr", "playerId")}, 200))

    found = {}

    def st(o, p):
        if isinstance(o, dict) and isinstance(o.get("title"), str) \
                and STAT_WORDS.search(o["title"]) and "stats" in o:
            found.setdefault(o["title"], (p, o["stats"]))
    walk(data, st)
    print(f"  stat rows matching xg/shots/corners/fouls/cards: {len(found)}")
    for t, (p, s) in list(found.items())[:14]:
        print(f"    {t}: {short(s, 80)}")
    if found:
        print("    first path:", next(iter(found.values()))[0])


if __name__ == "__main__":
    for nm in sys.argv[1:]:
        r = part_a(nm)
        if r:
            try:
                part_b(r[0])
            except Exception as e:
                print("  matchDetails failed:", e)