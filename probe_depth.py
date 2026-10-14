"""Probe: history depth, table file, fetch helpers. Usage: python probe_depth.py"""
import gzip
import inspect
import json

import five_min
from fotmob_mcp.client import FotMobClient

client = FotMobClient()


def src(obj, n=45):
    try:
        lines = inspect.getsource(obj).splitlines()
        print("\n".join(lines[:n]))
    except Exception as e:
        print("  (no source:", e, ")")


print("=== five_min.load_league ===")
src(getattr(five_min, "load_league", None) or five_min.league_matches)
print("\n=== FotMobClient.get_json ===")
src(FotMobClient.get_json, 40)
print("\n=== five_min.fetch_record ===")
src(five_min.fetch_record, 30)

# ---- how deep does league_matches go?
for lid in (47, 48, 938218):
    print(f"\n=== league_matches({lid}) ===")
    try:
        ms = five_min.league_matches(lid)
        fin = [m for m in ms if m["finished"] and not m["cancelled"]]
        print("total:", len(ms), "finished:", len(fin))
        if fin:
            ds = sorted(m["date"] for m in fin)
            print("date range:", ds[0].date(), "->", ds[-1].date())
            print("sample:", fin[0]["id"], fin[0]["home"], fin[0]["score"], fin[0]["away"])
    except Exception as e:
        print("failed:", repr(e)[:200])

# ---- previous season attempts (guesses, wrapped so failures are just info)
print("\n=== previous season attempts ===")
for path, params in (
    ("/api/data/leagues", {"id": 47, "season": "2025/2026"}),
    ("/api/data/leagues", {"id": 47, "season": "2025-2026"}),
    ("/api/leagues", {"id": 47, "season": "2025/2026"}),
):
    try:
        d = client.get_json(path, params, use_cache=False)
        fx = (d.get("fixtures") or {}) if isinstance(d, dict) else {}
        print("OK", path, params, "| keys:", list(d.keys())[:12] if isinstance(d, dict) else type(d))
        print("   fixtures keys:", list(fx.keys())[:8] if isinstance(fx, dict) else type(fx))
        print("   details:", json.dumps((d.get("details") or {}), ensure_ascii=False)[:300])
    except Exception as e:
        print("FAIL", path, params, repr(e)[:160])

# ---- league table file
print("\n=== table file ===")
url = "https://data.fotmob.com/tables.ext.47.fot.gz"
try:
    import requests
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    print("status:", r.status_code, "bytes:", len(r.content))
    raw = r.content
    try:
        raw = gzip.decompress(raw)
    except Exception:
        pass
    t = json.loads(raw.decode("utf-8"))
    print("type:", type(t).__name__)
    s = json.dumps(t, ensure_ascii=False)
    print(s[:1500])
except Exception as e:
    print("table fetch failed:", repr(e)[:200])

print("\nDONE")