#!/usr/bin/env bash
# Usage:
#   bash run_slate.sh                 run slate.csv in this terminal
#   bash run_slate.sh --bg            run slate.csv in the background (safe if you close the tab)
#   bash run_slate.sh other.csv       run a different file
#   bash run_slate.sh --fresh         ignore an unfinished earlier run and start over
# Options can be combined, e.g.  bash run_slate.sh other.csv --bg
cd "$(dirname "$0")" || exit 1

CSV="slate.csv"
BG=0
EXTRA=()
for arg in "$@"; do
  case "$arg" in
    --bg) BG=1 ;;
    --fresh) EXTRA+=("--fresh") ;;
    *) CSV="$arg" ;;
  esac
done

if [ ! -f "$CSV" ]; then
  echo "Can't find $CSV in $(pwd)."
  echo "Save the bookie export here first and name it slate.csv."
  exit 1
fi

# short cache so matchday numbers are fresh (the default is 6 hours)
export FOTMOB_CACHE_TTL_SECONDS="${FOTMOB_CACHE_TTL_SECONDS:-600}"
mkdir -p reports

if [ "$BG" = "1" ]; then
  nohup python nts_slate.py "$CSV" "${EXTRA[@]}" > reports/run.log 2>&1 &
  echo "Started in the background (process $!)."
  echo "Watch it:  tail -f reports/run.log     (Ctrl+C stops watching, not the run)"
  echo "When the log says 'Full report', read reports/shortlist_latest.txt and reports/latest.txt"
  echo "Don't start a second run until this one is done."
else
  python nts_slate.py "$CSV" "${EXTRA[@]}"
fi