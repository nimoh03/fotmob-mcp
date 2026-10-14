import re
import sys
from collections import Counter
from statistics import mean, pstdev

from fotmob_mcp.client import FotMobClient
from five_min import candidates, team_page, page_matches, league_of, league_matches

client = FotMobClient()

# ---------------------------------------------------------------------------
# CLI
#   python probe_team.py Arsenal --n=10
#   python probe_team.py Arsenal --n=10 --keys
#   python probe_team.py Arsenal --lines=total_shots:12,corners:4,goal_kicks_proxy:6
# ---------------------------------------------------------------------------
args = [a for a in sys.argv[1:] if not a.startswith("--")]
N = int(next((a.split("=")[1] for a in sys.argv[1:] if a.startswith("--n=")), 10))
SHOW_KEYS = "--keys" in sys.argv[1:]
DEBUG = "--debug" in sys.argv[1:]

DEFAULT_LINES = {
    "total_shots": 15,
    "ShotsOnTarget": 5,
    "corners": 5,
    "player_throws": 20,
    "yellow_cards": 2,
    "goal_kicks_proxy": 6,
}
LINES = dict(DEFAULT_LINES)
custom = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--lines=")), None)
if custom:
    LINES = {}
    for part in custom.split(","):
        stat, value = part.split(":")
        LINES[stat.strip()] = float(value)

WANT = [
    "total_shots", "ShotsOnTarget", "blocked_shots", "corners",
    "yellow_cards", "red_cards", "fouls", "player_throws",
    "Offsides", "keeper_saves", "expected_goals", "BallPossesion",
    "goal_kicks_proxy",  # derived, NOT a real FotMob stat
]


def num(value):
    if isinstance(value, (int, float)):
        return float(value)
    match = re.match(r"-?\d+(\.\d+)?", str(value or "").strip())
    return float(match.group()) if match else None


def collect(obj, out):
    if isinstance(obj, dict):
        values = obj.get("stats")
        if (
            obj.get("key")
            and isinstance(values, list)
            and len(values) == 2
            and not any(isinstance(x, (dict, list)) for x in values)
        ):
            out.setdefault(obj["key"], values)

        for value in obj.values():
            collect(value, out)

    elif isinstance(obj, list):
        for value in obj:
            collect(value, out)

    return out


def add_goal_kick_proxy(stats):
    """
    FotMob has no goal kick stat (check with --keys to confirm).
    Approximation: a team's goal kicks ~ opponent's shots that went off target.
        off_target = total_shots - ShotsOnTarget - blocked_shots
    Stored as [home_value, away_value] so values[side] works like every other stat.
    Ignores crosses/passes going out over the byline, so treat it as a rough proxy.
    """
    off_target = []
    direct = stats.get("ShotsOffTarget")

    if direct and len(direct) == 2 and None not in (num(direct[0]), num(direct[1])):
        # FotMob provides off-target shots directly, so use it
        off_target = [num(direct[0]), num(direct[1])]
    else:
        total = stats.get("total_shots")
        on_target = stats.get("ShotsOnTarget")
        blocked = stats.get("blocked_shots")

        if not (total and on_target and blocked):
            return stats

        for i in (0, 1):
            t, s, b = num(total[i]), num(on_target[i]), num(blocked[i])
            if None in (t, s, b):
                return stats
            off_target.append(max(t - s - b, 0.0))

    # home's goal kicks = away's off-target shots, and vice versa
    stats["goal_kicks_proxy"] = [off_target[1], off_target[0]]
    return stats


for name in args:
    print("\n" + "=" * 90)

    found = candidates(name)
    if not found:
        print(f"{name}: no team found")
        continue

    team_id = str(found[0]["id"])
    team_name = found[0].get("name", name)

    page = team_page(team_id)
    league_id = league_of(page)

    pool = {
        match["id"]: match
        for match in page_matches(page)
        if match["finished"] and not match["cancelled"]
    }

    if league_id:
        for match in league_matches(league_id):
            if (
                match["finished"]
                and not match["cancelled"]
                and team_id in (match["home_id"], match["away_id"])
            ):
                pool.setdefault(match["id"], match)

    ordered_matches = sorted(
        pool.values(),
        key=lambda match: match["date"],
        reverse=True,
    )

    if DEBUG:
        def show(label, matches):
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            print(f"\n--- DEBUG: {label} (10 closest to today {now:%Y-%m-%d}, ALL statuses) ---")
            closest = sorted(matches, key=lambda x: abs(x["date"] - now))[:10]
            for m in sorted(closest, key=lambda x: x["date"]):
                print(
                    f"  {m['date']} | {m.get('home', m.get('home_name', '?'))} vs "
                    f"{m.get('away', m.get('away_name', '?'))} | "
                    f"finished={m.get('finished')} cancelled={m.get('cancelled')} | ID {m['id']}"
                )

        show("team page matches", page_matches(page))
        if league_id:
            mine = [
                m for m in league_matches(league_id)
                if team_id in (m["home_id"], m["away_id"])
            ]
            show("league matches involving team", mine)
        print("\n--- DEBUG: pool used for stats (finished, not cancelled) ---")
        for m in ordered_matches[:8]:
            print(f"  {m['date']} | ID {m['id']}")

    recent = []
    match_periods = {}
    friendly_count = 0
    failed = 0

    for match in ordered_matches:
        if len(recent) >= N:
            break

        try:
            data = client.get_json(
                "/api/data/matchDetails",
                {"matchId": match["id"]},
                use_cache=False,
            )

            general = data.get("general") or {}
            competition = (
                general.get("leagueName")
                or general.get("parentLeagueName")
                or "Unknown"
            ).strip()

            if "friendl" in competition.lower():
                friendly_count += 1
                continue

            periods = (
                ((data.get("content") or {}).get("stats") or {})
                .get("Periods") or {}
            )

            recent.append((match, competition))
            match_periods[match["id"]] = {
                period: collect(section, {})
                for period, section in periods.items()
            }

        except Exception as error:
            failed += 1
            print(f"WARNING: Could not inspect match {match['id']}: {error}")

    print(f"TEAM: {team_name} | ID: {team_id} | LEAGUE: {league_id}")
    print(f"Finished matches found: {len(pool)}")
    print(f"Friendlies excluded: {friendly_count}")
    print(f"Competitive matches selected: {len(recent)}/{N}")
    print(f"Failed requests: {failed}")

    if len(recent) < N:
        print(f"WARNING: only {len(recent)} competitive matches available, wanted {N}. Averages use a smaller sample.")

    rows = []
    for match, competition in recent:
        periods = match_periods[match["id"]]
        all_stats = add_goal_kick_proxy(periods.get("All") or {})
        side = 0 if match["home_id"] == team_id else 1

        match["competition"] = competition
        rows.append((match, all_stats, side))

    print(f"Successfully inspected: {len(rows)}/{len(recent)} matches")

    # -----------------------------------------------------------------------
    # KEY DISCOVERY: every stat key FotMob returned (use --keys)
    # -----------------------------------------------------------------------
    if SHOW_KEYS:
        print("\n=== ALL STAT KEYS FOUND (real FotMob keys + derived proxy) ===")
        key_counts = Counter()
        samples = {}
        for _, stats, _ in rows:
            for key, values in stats.items():
                key_counts[key] += 1
                samples.setdefault(key, values)

        for key in sorted(key_counts):
            tag = "  (DERIVED)" if key == "goal_kicks_proxy" else ""
            print(f"  {key:<30}{key_counts[key]}/{len(rows)}  sample={samples[key]}{tag}")

        goal_kick_like = [
            k for k in key_counts
            if k != "goal_kicks_proxy" and "goal" in k.lower() and "kick" in k.lower()
        ]
        if goal_kick_like:
            print(f"\nREAL goal kick keys found: {goal_kick_like}")
        else:
            print("\nNo real goal kick key in FotMob data. goal_kicks_proxy is an estimate only.")

    print("\n=== STAT COVERAGE AND SUMMARY ===")
    print(
        f"{'STAT':<19}{'COVERAGE':>10}{'FOR AVG':>10}"
        f"{'AGAINST':>10}{'MIN':>8}{'MAX':>8}{'SD':>8}"
    )

    for stat in WANT:
        for_values = []
        against_values = []

        for _, stats, side in rows:
            values = stats.get(stat)

            if not values or len(values) != 2:
                continue

            team_value = num(values[side])
            opponent_value = num(values[1 - side])

            if team_value is not None:
                for_values.append(team_value)

            if opponent_value is not None:
                against_values.append(opponent_value)

        coverage = f"{len(for_values)}/{len(recent)}"

        if for_values:
            print(
                f"{stat:<19}{coverage:>10}"
                f"{mean(for_values):>10.2f}"
                f"{mean(against_values):>10.2f}"
                f"{min(for_values):>8.0f}"
                f"{max(for_values):>8.0f}"
                f"{pstdev(for_values):>8.2f}"
            )
        else:
            print(f"{stat:<19}{coverage:>10}{'MISSING':>10}")

    print("\n=== MATCH-BY-MATCH TEAM VALUES ===")
    for match, stats, side in rows:
        home = match.get("home", match.get("home_name", "Home"))
        away = match.get("away", match.get("away_name", "Away"))

        print(f"\n{match['date']} | {home} vs {away} | {match['competition']} | ID {match['id']}")

        for stat in WANT:
            values = stats.get(stat)
            if values and len(values) == 2:
                team_value = num(values[side])
                opponent_value = num(values[1 - side])

                if team_value is not None:
                    label = f"{stat} (proxy)" if stat == "goal_kicks_proxy" else stat
                    print(f"  {label}: team={team_value:g}, opponent={opponent_value:g}")

    print("\n=== CONSISTENCY CHECKS ===")
    print("Team stayed at or below each threshold:")
    for stat, threshold in LINES.items():
        available = []

        for _, stats, side in rows:
            values = stats.get(stat)
            if values and len(values) == 2:
                value = num(values[side])
                if value is not None:
                    available.append(value)

        if available:
            hits = sum(value <= threshold for value in available)
            print(
                f"  {stat} <= {threshold:g}: "
                f"{hits}/{len(available)} ({hits / len(available) * 100:.1f}%)"
            )
        else:
            print(f"  {stat} <= {threshold:g}: NO DATA")

    print("\nDONE")