#!/usr/bin/env bash
# Stage the sibling rank-report modules into the build context.
# The Docker build context is this directory, so a sibling directory is not
# reachable from the Dockerfile. Run this before `docker compose build`.
set -euo pipefail
cd "$(dirname "$0")"
SRC="../rank-report"
[ -d "$SRC" ] || { echo "rank-report not found at $SRC"; exit 1; }
rm -rf rank-report && mkdir -p rank-report
# Every .py in the module set — an explicit list silently drops new modules,
# which is the same failure the Dockerfile COPY list warns about. search.py and
# gsc.py were both missed by a hand-maintained list once already.
cp "$SRC"/*.py rank-report/
[ -f rank-report/rank_report.py ] || { echo "rank_report.py missing after copy"; exit 1; }

# The image runs Python 3.11 and this machine runs 3.14. PEP 701 relaxed
# f-strings in 3.12, so a backslash inside an f-string expression parses here and
# is a SyntaxError there. That shipped once and only surfaced when a client
# report died at run time in the container, so it is gated here now.
python3 "$(dirname "$0")/pycheck311.py" rank-report \
  || { echo "staging aborted — will not parse on the container's Python"; exit 1; }

echo "staged $(ls -1 rank-report | wc -l | tr -d ' ') modules into ./rank-report"
