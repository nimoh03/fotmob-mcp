"""Reads probe_raw/*.json (no network).
Usage: python probe_check.py
Checks: shotmap vs total_shots, half splits, lineup structure, odds paths."""
import json
from collections import Counter
from pathlib import Path


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


def find_odds(o, path="", out=None, depth=0):
    if out is None:
        out = []
    if depth > 9 or len(out) >= 15:
        return out
    if isinstance(o, dict):
        for k, v in o.items():
            p = f"{path}.{k}" if path else k
            if any(s in str(k).lower() for s in ("odd", "bet", "bookmaker")):
                out.append(p)
            find_odds(v, p, out, depth + 1)
    elif isinstance(o, list):
        for i, v in enumerate(o[:3]):
            find_odds(v, f"{path}[{i}]", out, depth + 1)
    return out


files = sorted(Path("probe_raw").glob("*.json"))
print(f"{len(files)} files")

for f in files:
    d = json.loads(f.read_text(encoding="utf-8"))
    gen = d.get("general") or {}
    c = d.get("content") or {}
    hid = str((gen.get("homeTeam") or {}).get("id"))
    aid = str((gen.get("awayTeam") or {}).get("id"))
    print("\n" + "=" * 70)
    print(f.stem, (gen.get("homeTeam") or {}).get("name"), "v", (gen.get("awayTeam") or {}).get("name"))
    print("top-level keys:", list(d.keys()))

    # --- 1) shotmap vs stats reconciliation
    allst, h1, h2 = {}, {}, {}
    per = (c.get("stats") or {}).get("Periods") or {}
    collect_stats(per.get("All"), allst)
    collect_stats(per.get("FirstHalf"), h1)
    collect_stats(per.get("SecondHalf"), h2)
    shots = (c.get("shotmap") or {}).get("shots") or []
    print("stats total_shots H/A:", allst.get("total_shots"),
          "| 1H:", h1.get("total_shots"), "| 2H:", h2.get("total_shots"))
    for label, tid, idx in (("HOME", hid, 0), ("AWAY", aid, 1)):
        mine = [s for s in shots if str(s.get("teamId")) == tid]
        blocked = sum(1 for s in mine if s.get("isBlocked"))
        ontgt = sum(1 for s in mine if s.get("isOnTarget"))
        og = sum(1 for s in mine if s.get("isOwnGoal"))
        sits = dict(Counter(s.get("situation") for s in mine))
        print(f"  {label}: shotmap={len(mine)} blocked={blocked} onTarget={ontgt} ownGoal={og} "
              f"nonBlocked={len(mine) - blocked} | stat total_shots={allst.get('total_shots', [None, None])[idx]} "
              f"SoT={allst.get('ShotsOnTarget', [None, None])[idx]} blk={allst.get('blocked_shots', [None, None])[idx]}")
        print(f"     situations: {sits}")
        print(f"     periods: {dict(Counter(s.get('period') for s in mine))}")

    # --- 2) lineup
    lu = c.get("lineup")
    print("lineup type:", type(lu).__name__)
    if isinstance(lu, dict):
        print("lineup keys:", list(lu.keys()))
        for k in ("homeTeam", "awayTeam"):
            t = lu.get(k) or {}
            if isinstance(t, dict):
                print(f"  {k}: keys={list(t.keys())}")
                print(f"     formation={t.get('formation')} starters={len(t.get('starters') or [])} subs={len(t.get('subs') or [])}")
                un = t.get("unavailable")
                print(f"     unavailable={un if un is None else (str(un)[:300])}")
                st = (t.get("starters") or [None])[0]
                if st:
                    print("     starter fields:", list(st.keys()))

    # --- 3) odds / extra pre-match info
    print("odds-ish paths:", find_odds(d) or "NONE")
    mf = c.get("matchFacts") or {}
    print("matchFacts keys:", list(mf.keys()))
    print("momentum type:", type(c.get("momentum")).__name__,
          "| table type:", type(c.get("table")).__name__,
          "| h2h type:", type(c.get("h2h")).__name__)

print("\nDONE")