import sys
from datetime import timedelta

from engine import build_index, shared_comp
from market_data import read_options, load, CACHE, RESOLVE


def main():
    args = sys.argv[1:]
    n = int(args[args.index("--n") + 1]) if "--n" in args else 10
    league_only = "--league" in args
    skip = {args.index("--n") + 1} if "--n" in args else set()
    words = [a.lower() for i, a in enumerate(args) if not a.startswith("--") and i not in skip]

    cache, rc = load(CACHE), load(RESOLVE)
    by_team = build_index(cache)
    rows = read_options("options.csv")

    names = {}
    for r in rows:
        res = rc.get(str(r["eventId"]))
        if res and res.get("verified") and " vs " in r["match"]:
            h, a = r["match"].split(" vs ", 1)
            names[res["home_id"]], names[res["away_id"]] = h, a

    def name(tid):
        return names.get(tid, f"team {tid}")

    for r in rows:
        if not any(w in r["match"].lower() for w in words):
            continue
        res = rc.get(str(r["eventId"]))
        if not res or not res.get("verified"):
            print(f"\n{r['match']}: not found on FotMob")
            continue
        before = r["kickoff"] - timedelta(hours=2)
        print("\n" + "=" * 78)
        print(f"{r['match']} ({r['tournament']})")
        print("=" * 78)
        for tid, label in ((res["home_id"], "HOME"), (res["away_id"], "AWAY")):
            games = [(d, rec, s) for d, rec, s in by_team.get(tid, []) if d < before]
            if league_only:
                comp = shared_comp(by_team, res["home_id"], res["away_id"])
                games = [g for g in games if g[1].get("comp") == comp]
            games = games[-n:]
            print(f"\n{label}: {name(tid)}  (last {len(games)} games, oldest first)")
            w = dr = l = gf = ga = 0
            for d, rec, s in games:
                f, a = rec["score"][s], rec["score"][1 - s]
                opp = rec["a"] if s == 0 else rec["h"]
                res_txt = "W" if f > a else ("D" if f == a else "L")
                w += res_txt == "W"
                dr += res_txt == "D"
                l += res_txt == "L"
                gf += f
                ga += a
                goals = ""
                if rec.get("sides_ok"):
                    goals = "  goals: " + " ".join(
                        f"{m}'{'F' if (h == 1) == (s == 0) else 'A'}" for m, h in rec["goals"])
                print(f"  {str(d)[:10]}  {'H' if s == 0 else 'A'}  {res_txt}  {f}-{a}  vs {name(opp):<28}"
                      f" [{rec.get('comp') or '?'}]{goals}")
            if games:
                k = len(games)
                print(f"  -> W{w} D{dr} L{l} | points {3 * w + dr} of {3 * k} | goals {gf}-{ga} ({gf / k:.1f}-{ga / k:.1f} per game)")
        print("\n(F = goal for that team, A = goal against it)")


if __name__ == "__main__":
    main()