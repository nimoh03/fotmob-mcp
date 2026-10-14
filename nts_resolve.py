import json
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

from fotmob_mcp.client import FotMobClient
from nts_extract import extract, show

ENTRIES = [
    "Renato Espinosa | CS Corvinul Hunedoara",
    "Francisco Gonzalez | Tijuana",
    "Franco Romero | Toluca",
    "Felipe Sanchez | Santos Laguna",
    "Jorge Diaz Price | Toluca",
    "Dan N'Lundulu | Petrolul",
]

client = FotMobClient()

# Optional file: {"Name | Club": fotmob_player_id}. Checked before any searching.
OVERRIDES_PATH = Path("player_overrides.json")
_OVERRIDES = None

EXTRA = str.maketrans({"ø": "o", "đ": "d", "ł": "l", "ß": "ss", "æ": "ae", "œ": "oe", "ı": "i"})

# words that don't help tell clubs apart ("FC", "CS", "ASC" ...)
GENERIC = {
    "fc", "cf", "sc", "ac", "as", "cs", "cd", "ca", "sd", "ud", "afc", "asc", "fk", "sk",
    "nk", "club", "clube", "acs", "sv", "ssc", "rc", "fsv", "tsv", "bk",
}


def norm(s):
    """Lowercase, no accents, no apostrophes, hyphens and dots become spaces."""
    s = (s or "").lower().translate(EXTRA)
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = s.replace("'", "").replace("\u2019", "").replace("`", "").replace("-", " ").replace(".", " ")
    return " ".join(s.split())


def sig_tokens(s):
    """Main words of a club name: no accents, no numbers, no FC/CS/ASC style words."""
    toks = [t for t in norm(s).split() if not t.isdigit()]
    core = [t for t in toks if t not in GENERIC]
    return set(core or toks)


def team_match(a, b):
    """True if one club name's main words are all inside the other's."""
    ta, tb = sig_tokens(a), sig_tokens(b)
    if not ta or not tb:
        return False
    return ta <= tb or tb <= ta


def tok_match(a, b):
    """Same word, or (for longer words) a close spelling like espinosa / espinoza."""
    if a == b:
        return True
    if len(a) >= 5 and len(b) >= 5:
        return SequenceMatcher(None, a, b).ratio() >= 0.85
    return False


def load_overrides():
    global _OVERRIDES
    if _OVERRIDES is None:
        _OVERRIDES = {}
        try:
            raw = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
            for k, v in raw.items():
                key = " | ".join(norm(p) for p in str(k).split("|"))
                _OVERRIDES[key] = str(v)
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"  (could not read player_overrides.json: {e})")
    return _OVERRIDES


def collect_typed(o, kind, found):
    if isinstance(o, dict):
        if o.get("type") == kind and o.get("id") is not None and not o.get("isCoach"):
            found.append(o)
        for v in o.values():
            collect_typed(v, kind, found)
    elif isinstance(o, list):
        for v in o:
            collect_typed(v, kind, found)
    return found


def _search(term, kind):
    data = client.get_json(
        "/api/data/search/suggest", {"term": term, "hits": "10", "lang": "en"}
    )
    seen, out = set(), []
    for c in collect_typed(data, kind, []):
        cid = str(c["id"])
        if cid not in seen:
            seen.add(cid)
            out.append(c)
    return out


def search_players(term):
    return _search(term, "player")


def search_teams(term):
    return _search(term, "team")


def real_team(pid):
    """Team name from the player's actual profile (search labels can be stale or empty)."""
    try:
        d = client.get_json(
            "/api/data/playerData", {"id": str(pid), "includeMarketValues": "true"}
        )
    except Exception:
        return ""
    return (d.get("primaryTeam") or {}).get("teamName") or ""


# ---------- the club's squad page ----------

def find_club(club):
    toks = [t for t in norm(club).split() if t not in GENERIC]
    terms = [club]
    stripped = " ".join(toks)
    if stripped and stripped != norm(club):
        terms.append(stripped)
    for term in terms:
        try:
            cands = search_teams(term)
        except Exception:
            continue
        hits = [c for c in cands if team_match(club, c.get("name", ""))]
        if hits:
            exact = [c for c in hits if norm(c["name"]) == norm(club)]
            return (exact or hits)[0]
    return None


def squad_members(team_id):
    try:
        data = client.get_json("/api/data/teams", {"id": str(team_id)})
    except Exception:
        data = client.get_json("/api/data/teams", {"id": str(team_id), "ccode3": "NGA"})
    squad = (data.get("overview") or {}).get("squad") or data.get("squad") or []
    out = []
    for g in squad:
        if not isinstance(g, dict) or str(g.get("title", "")).lower() == "coach":
            continue
        for m in g.get("members") or []:
            if m.get("id") is not None and m.get("name"):
                out.append(m)
    return out


def squad_lookup(name, hint):
    """Find a player on his club's squad page. Close spellings count."""
    club = find_club(hint)
    if not club:
        return []
    try:
        members = squad_members(club["id"])
    except Exception:
        return []
    qt = norm(name).split()
    need = min(2, len(qt))  # first name + surname both need to roughly match
    scored = []
    for m in members:
        mt = norm(m["name"]).split()
        score = sum(1 for q in qt if any(tok_match(q, t) for t in mt))
        if score >= need:
            scored.append((score, m))
    if not scored:
        return []
    best = max(s for s, _ in scored)
    top = [m for s, m in scored if s == best]
    return [
        {"id": str(m["id"]), "name": m["name"], "teamName": club.get("name"), "type": "player"}
        for m in top
    ]


def squad_pick(pool, hint):
    """Two profiles with the same name at one club: keep the one on the current squad list."""
    if not hint or hint.isdigit():
        return None
    club = find_club(hint)
    if not club:
        return None
    try:
        ids = {str(m["id"]) for m in squad_members(club["id"])}
    except Exception:
        return None
    inside = [c for c in pool if str(c["id"]) in ids]
    return inside if len(inside) == 1 else None


def not_found(name, hint):
    """Nothing matched through normal search: try the club's squad page."""
    if hint and not hint.isdigit():
        found = squad_lookup(name, hint)
        if len(found) == 1:
            return "squad", found
        if len(found) > 1:
            return "ambiguous", found
    return "notfound", []


def resolve(entry):
    """Return (status, candidates).
    status: ok | fuzzy | squad | squadpick | ambiguous | notfound"""
    name, _, hint = [p.strip() for p in entry.partition("|")]

    key = " | ".join(norm(p) for p in entry.split("|"))
    forced = load_overrides().get(key)
    if forced:
        return "ok", [{"id": forced, "name": name, "teamName": hint}]

    if hint.isdigit():
        return "ok", [{"id": hint, "name": name, "teamName": ""}]

    cands = search_players(name)
    exact = [c for c in cands if norm(c["name"]) == norm(name)]
    tokens = norm(name).split()
    pool = exact or [c for c in cands if all(t in norm(c["name"]) for t in tokens)]

    # nothing found: try the last name on its own (spelling of the first name may differ)
    if not pool and len(tokens) >= 2:
        extra = search_players(tokens[-1])
        pool = [c for c in extra if tokens[-1] in norm(c["name"])]
        exact = []

    if not pool:
        return not_found(name, hint)

    if hint:
        kept = [c for c in pool if team_match(hint, c.get("teamName", ""))]
        if len(kept) != 1:
            # search labels can be stale or empty: check the real club on each profile
            check = kept or pool[:8]
            verified = [c for c in check if team_match(hint, real_team(c["id"]))]
            kept = verified or kept
        pool = kept
        if not pool:
            return not_found(name, hint)

    if len(pool) > 1:
        picked = squad_pick(pool, hint)
        if picked:
            return "squadpick", picked
        return "ambiguous", pool
    return ("ok" if exact else "fuzzy"), pool


def league_minutes(d):
    for s in (d.get("mainLeague") or {}).get("stats") or []:
        if s.get("localizedTitleId") == "minutes_played":
            return s.get("value")
    return None


def describe(c):
    """One line about a candidate: enough to tell two players with the same name apart."""
    try:
        d = client.get_json(
            "/api/data/playerData", {"id": str(c["id"]), "includeMarketValues": "true"}
        )
    except Exception as e:
        return f"id {c['id']}: {c.get('name')} | {c.get('teamName')} | could not load ({e})"
    pos = ((d.get("positionDescription") or {}).get("primaryPosition") or {}).get("label")
    born = ((d.get("birthDate") or {}).get("utcTime") or "")[:4]
    team = (d.get("primaryTeam") or {}).get("teamName")
    league = (d.get("mainLeague") or {}).get("leagueName")
    return (
        f"id {c['id']}: {d.get('name')} | {team} | {pos} | born {born} "
        f"| {league} | {league_minutes(d)} min this season"
    )


def main():
    for entry in ENTRIES:
        print(f"\n######## {entry} ########")
        try:
            status, pool = resolve(entry)
        except Exception as e:
            print(f"  !! search failed: {e} | {getattr(e, '__cause__', None)}")
            continue

        if status == "notfound":
            print("  NOT FOUND (check spelling, or add it to player_overrides.json)")
        elif status == "ambiguous":
            print(f"  AMBIGUOUS: {len(pool)} different players:")
            for c in pool:
                print("   ", describe(c))
        else:
            if status == "fuzzy":
                print(f"  FUZZY match: searched '{entry}', got '{pool[0]['name']}'. Check it.")
            if status == "squad":
                print(f"  found via the club's squad page: {pool[0]['name']} (id {pool[0]['id']})")
            if status == "squadpick":
                print(f"  two profiles with this name, picked the one on the squad list: "
                      f"{pool[0]['name']} (id {pool[0]['id']})")
            try:
                show(extract(int(pool[0]["id"])))
            except Exception as e:
                print(f"  !! extract failed: {e} | {getattr(e, '__cause__', None)}")


if __name__ == "__main__":
    main()