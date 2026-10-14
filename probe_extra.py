"""Reads probe_raw/*.json (no network). Usage: python probe_extra.py"""
import json
from pathlib import Path


def show(label, o, n=900):
    s = json.dumps(o, ensure_ascii=False)
    print(f"{label} [{type(o).__name__}]: {s[:n]}{' ...' if len(s) > n else ''}")


for mid in ("5795465", "5836841", "5836806"):
    p = Path("probe_raw") / f"{mid}.json"
    if not p.exists():
        continue
    d = json.loads(p.read_text(encoding="utf-8"))
    c = d.get("content") or {}
    mf = c.get("matchFacts") or {}
    print("\n" + "=" * 70)
    print(mid)
    show("general", {k: v for k, v in (d.get("general") or {}).items()
                     if k in ("leagueName", "leagueId", "parentLeagueId", "matchRound",
                              "matchTimeUTCDate", "finished", "countryCode")}, 600)
    poll = mf.get("poll") or {}
    show("poll keys", list(poll.keys()) if isinstance(poll, dict) else poll, 300)
    show("oddspoll", poll.get("oddspoll") if isinstance(poll, dict) else None, 1500)
    show("teamForm", mf.get("teamForm"), 800)
    show("insights", mf.get("insights"), 1200)
    show("infoBox", mf.get("infoBox"), 800)
    tbl = c.get("table")
    show("table keys", list(tbl.keys()) if isinstance(tbl, dict) else tbl, 300)
    show("table", tbl, 1200)
    h = c.get("h2h")
    show("h2h keys", list(h.keys()) if isinstance(h, dict) else h, 300)
    show("h2h summary", (h or {}).get("summary") if isinstance(h, dict) else None, 300)
    ps = c.get("playerStats")
    show("playerStats", {"n_players": len(ps)} if isinstance(ps, dict) else ps, 200)
    per = (c.get("stats") or {}).get("Periods") or {}
    allp = per.get("All") or {}
    show("stats All keys", list(allp.keys()) if isinstance(allp, dict) else allp, 300)

print("\nDONE")