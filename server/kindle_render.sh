#!/bin/bash
# Render the brain-api /kindle page to an 8-bit grayscale PNG for the reader.
# Run daily by launchd (see launchd/). The reader fetches the result from /kindle.png.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "${BRAIN_API_ENV:-$HOME/.config/brain-api/brain-api.env}"
OUT="$HERE/out"
mkdir -p "$OUT"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"

# Fresh Apple Reminders for the page (kindle.py reads out/reminders.json). The Work list is
# not created here (remindctl's --create made a new list each run). If Reminders access is missing the page falls back to the notes todo list.
R="${REMINDCTL:-/opt/homebrew/bin/remindctl}"
# remindctl waits forever on an unanswered privacy prompt; give it 60 seconds.
limit() { perl -e 'alarm shift; exec @ARGV' "$@"; }
if [ -x "$R" ]; then
  if limit 60 "$R" show all --json --no-input > "$TMP/reminders.json" 2>/dev/null && [ -s "$TMP/reminders.json" ]; then
    mv "$TMP/reminders.json" "$OUT/reminders.json"
  else
    echo "$(date '+%F %T') reminders unavailable: $("$R" status 2>&1 | head -1)"
  fi
fi

"$CHROME" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=1 \
  --user-data-dir="$TMP/profile" --window-size=1236,1648 --virtual-time-budget=3000 \
  --screenshot="$TMP/page.png" "http://127.0.0.1:${PORT:-8484}/kindle?key=${HABIT_TOKEN}" >/dev/null 2>&1

sips -m "/System/Library/ColorSync/Profiles/Generic Gray Gamma 2.2 Profile.icc" \
  "$TMP/page.png" --out "$TMP/gray.png" >/dev/null
mv "$TMP/gray.png" "$OUT/kindle.png"
echo "$(date '+%F %T') rendered $OUT/kindle.png"
