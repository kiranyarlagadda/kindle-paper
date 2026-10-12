#!/bin/bash
# Build today's edition (paper.py). Run daily by launchd at 05:15,
# before the 05:30 morning page render.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
set -a
source "${BRAIN_API_ENV:-$HOME/.config/brain-api/brain-api.env}"
set +a
cd "$HERE"
echo "$(date '+%F %T') paper build start"
"${PYTHON:-/usr/bin/python3}" paper.py build
echo "$(date '+%F %T') paper build done"
