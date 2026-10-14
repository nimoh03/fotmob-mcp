"""
probe_day.py - can FotMob's daily fixture list resolve our slate names?

Run:  python probe_day.py options.csv
      python probe_day.py options.csv --all      (print every row, not just weak ones)
"""
import csv
import re
import sys
from datetime import timedelta, timezone
from difflib import SequenceMatcher

from five_min import client, parse_dt, find_matches, norm_match
from nts_resolve import norm, team_match

INTL_TOURN = re.compile(
    r"world cup|nations league|friendl|olympic|european championship|"
    r"africa cup|asian cup|copa america|gold cup", re.I)


def aware(d):
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def is_international(r):
    if (r.get("category") or "").strip().lower() == "international":
        return True
    return bool(INTL_TOURN.search(r.get("tournament") or ""))


def read_rows(path):
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            ko = parse_dt(r.get("startTime", ""))
            if ko is None:
                continue
            # scanner stores Nigeria time with a Z suffix; real UTC is 1h earlier
            r["kickoff"] = aware(ko) - timedelta(hours=1)
            rows.append(r)
    return rows


_day = {}


def day_fixtures(day):
    """All FotMob fixtures for one date (YYYYMMDD)."""
    if day in _day:
        return _day[day]
    data, err = None, None
    for params in ({"date": day}, {"date": day, "timezone": "UTC", "ccode3": "NGA"}):
        try:
            data = client.get_json("/api/data/matches", params, use_cache=False)
            break
        except Exception as e:
            err = e
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
        print(f"  {day}: top-level keys {list(data.keys())[:8]} | fixtures found: {len(out)}")
    else:
        print(f"  {day}: FAILED ({err})")
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


def main():
    path = next((a for a in sys.argv[1:] if not a.startswith("--")), "options.csv")
    show_all = "--all" in sys.argv
    rows = read_rows(path)
    intl = [r for r in rows if is_international(r)]
    club = [r for r in rows if not is_international(r)]
    print(f"slate rows: {len(rows)} | international skipped: {len(intl)} | club matches: {len(club)}")
    print("\nSKIPPED as international (check nothing wrong is in here):")
    seen = sorted({(r.get("category", ""), r.get("tournament", "")) for r in intl})
    for cat, t in seen[:30]:
        print(f"  [{cat}] {t}")

    days = sorted({(r["kickoff"] + timedelta(days=d)).strftime("%Y%m%d") for r in club for d in (-1, 0, 1)})
    print("\nfetching FotMob daily fixture lists:")
    fixtures = {}
    for d in days:
        for fx in day_fixtures(d):
            fixtures[fx["id"]] = fx
    fixtures = list(fixtures.values())
    print(f"total FotMob fixtures loaded: {len(fixtures)}")

    counts = {"OK": 0, "ONE-SIDE": 0, "WEAK": 0, "NONE": 0}
    lines = []
    for r in club:
        b = best_fixture(r, fixtures)
        v = verdict(b)
        counts[v] += 1
        if b:
            fx = b["fx"]
            line = (f"[{v:8}] {r['homeTeam']} vs {r['awayTeam']} ({r['tournament']})  ->  "
                    f"{fx['home']} vs {fx['away']} ({fx['league']}) "
                    f"names {b['sh']:.2f}/{b['sa']:.2f} time {b['dt']:.0f}min")
        else:
            line = f"[NONE    ] {r['homeTeam']} vs {r['awayTeam']} ({r['tournament']})  ->  nothing near that kickoff"
        lines.append((v, line))

    print("\n" + "=" * 60)
    print("RESULT:", counts)
    print("=" * 60)
    shown = 0
    for v, line in lines:
        if show_all or v != "OK":
            print(line)
            shown += 1
            if shown >= 70 and not show_all:
                print("... (use --all to see everything)")
                break


if __name__ == "__main__":
    main()