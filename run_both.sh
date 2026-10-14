#!/usr/bin/env bash
# Runs both systems.
#   bash run_both.sh            5-minute system (five_slate.csv), then players (slate.csv)
#   bash run_both.sh --fresh    same, but the player run ignores an unfinished earlier run
cd "$(dirname "$0")" || exit 1

# fresh team pages and league tables (30 minutes). Saved goal minutes of finished matches are kept for good.
export FOTMOB_CACHE_TTL_SECONDS="${FOTMOB_CACHE_TTL_SECONDS:-1800}"
mkdir -p reports/five

if [ -f five_slate.csv ]; then
  echo "=================== 5-MINUTE SYSTEM ==================="
  python five_min.py five_slate.csv
else
  echo "five_slate.csv not found, skipping the 5-minute system."
fi

echo
if [ -f slate.csv ]; then
  echo "=================== PLAYER NOT-TO-SCORE SYSTEM ==================="
  python nts_slate.py slate.csv "$@"
else
  echo "slate.csv not found, skipping the player system."
fi

echo
echo "RESULTS:"
echo "  5-minute quick list : reports/five/five_shortlist_latest.txt"
echo "  5-minute full report: reports/five/five_latest.txt"
echo "  players quick list  : reports/shortlist_latest.txt"
echo "  players full report : reports/latest.txt"bash run_both.shj