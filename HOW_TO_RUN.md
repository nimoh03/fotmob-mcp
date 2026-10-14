# Daily run: player-not-to-score research

Project folder: /workspaces/fotmob-mcp

## Steps
1. Open the Codespace. The MCP server does NOT need to be running.
2. Export the CSV from the bookie site and save it in the project folder as `slate.csv`
   (drag it into the file list on the left and replace the old one).
3. In the terminal run ONE of these:
     bash run_slate.sh           (stay and watch, about 2 min per 280 players)
     bash run_slate.sh --bg      (big slates: runs in the background, you can close the tab)
4. Read the results in the `reports/` folder:
     reports/shortlist_latest.txt   quick list: regular starters with no risk flags
     reports/latest.txt             full brief for every player, with a RUN SUMMARY on top
   Older runs are kept as report_DATE_TIME.txt and shortlist_DATE_TIME.txt.

## If it stops or crashes
Run the same command again. Finished players are saved in reports/work/, so it carries on where it stopped
(only if the earlier run was in the last 6 hours). To throw that away and start over:
     bash run_slate.sh --fresh
If the summary says "stopped after 8 failures in a row", FotMob is refusing requests: wait a few minutes, run again.

## Big slates (around 1,000 players)
- Expect roughly 7 minutes. Use --bg and watch with:  tail -f reports/run.log
- Don't start a second run while one is going.

## Sharing it with Claude
- Right-click reports/latest.txt (or the shortlist), choose Download, then attach it in the chat.
- Or paste only the RUN SUMMARY at the top plus the matches you care about.

## Good habits
- Run it on matchday, before kickoff. Stats are as of the moment you run it.
- Keep old reports. They are how we can check later whether the flags meant anything.
- Every few weeks delete old files in reports/ to keep the folder tidy.

## Quick test (10 players only)
  python nts_slate.py slate.csv 10

## Forcing a player the script can't tell apart
Create `player_overrides.json` in the project folder:
  {"Name As In CSV | Club As In CSV": 123456}
The number is the FotMob player id. The report prints it for UNSURE players.

## The files that matter
nts_slate.py, nts_extract.py, nts_resolve.py, nts_opponent.py, run_slate.sh
(the probe_* and inspect_* files were for exploring and can be deleted)

## Reading the report
- "NOT RESEARCHED" lines in the summary are players we couldn't match safely. About 2-3% is normal.
- "UNSURE MATCH" means the name or club didn't agree with FotMob, so we skipped him on purpose.
- Romania and similar leagues have no xG, so those briefs are thinner. The report says so.
- The report describes risk, it doesn't decide. No leg is ever "safe".