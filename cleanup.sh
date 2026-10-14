#!/usr/bin/env bash
# Tidy the project folder without deleting anything important.
#   bash cleanup.sh            dry run: only shows what WOULD move, changes nothing
#   bash cleanup.sh --do-it    really moves it into _archive_DATE/
cd "$(dirname "$0")" || exit 1

DO=0
[ "$1" = "--do-it" ] && DO=1

# 0 = keep in place, 1 = archive
keep() {
  case "$1" in
    # the original fotmob-mcp project
    fotmob_mcp|fotmob_mcp.egg-info|tests|docs|examples|assets) return 0 ;;
    pyproject.toml|setup.py|setup.cfg|uv.lock|poetry.lock|Makefile|MANIFEST.in|Dockerfile) return 0 ;;
    README*|LICENSE*|CHANGELOG*|requirements*.txt) return 0 ;;
    # our system and its data
    nts_extract.py|nts_resolve.py|nts_opponent.py|nts_slate.py|nts_context.py) return 0 ;;
    run_slate.sh|HOW_TO_RUN.md|cleanup.sh|slate.csv|player_overrides.json|reports) return 0 ;;
    # earlier archives
    _archive_*) return 0 ;;
    # hidden junk
    .nts_cache|*.swp) return 1 ;;
    # every other hidden file or folder (.git, .env, .vscode ...)
    .*) return 0 ;;
  esac
  return 1
}

STAMP=$(date +%Y%m%d_%H%M)
DEST="_archive_$STAMP"
COUNT=0

shopt -s dotglob nullglob
for f in *; do
  [ "$f" = "__pycache__" ] && continue
  keep "$f" && continue
  COUNT=$((COUNT + 1))
  if [ "$DO" = "1" ]; then
    mkdir -p "$DEST"
    mv -- "$f" "$DEST/"
    echo "moved        $f"
  else
    echo "would move   $f"
  fi
done

echo
if [ "$DO" = "1" ]; then
  find . -name "__pycache__" -type d -not -path "./.git/*" -prune -exec rm -rf {} + 2>/dev/null
  echo "Done: $COUNT items moved into $DEST/ (nothing deleted except __pycache__)."
  echo "Check it still works:   python nts_slate.py slate.csv 10"
  echo "Put things back:        cd $DEST && mv -n * .. && mv -n .[!.]* .. ; cd .."
  echo "Happy with the result?  rm -rf $DEST"
else
  echo "$COUNT items would move. Nothing was changed."
  echo "If the list looks right, run:  bash cleanup.sh --do-it"
fi