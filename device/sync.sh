#!/bin/sh
# Pull new editions of the paper and today's morning page from brain-api.
# Keeps whatever editions the server lists (the last 7 days) and removes older ones.
K=${PAPER_HOME:-/mnt/us/paper}
. "$K/config.sh"
LIB=${LIBRARY:-/mnt/us/library}
[ -n "$PAPER_NAME" ] || PAPER_NAME="Kiran's Paper"
NEWS="$LIB/Newspaper"
SLEEP=$K/sleep
mkdir -p "$NEWS" "$SLEEP"
# One sync at a time (wake events and the hourly loop can overlap).
LOCK=/tmp/paper-sync.lock
mkdir "$LOCK" 2>/dev/null || { echo "sync $(date): already running"; exit 0; }
trap 'rmdir "$LOCK"' EXIT
get() { curl -s -f -m 180 ${PROXY:+-x "$PROXY"} "$@"; }

J=$(get "$BASE/paper/editions.json?key=$KEY") || { echo "sync $(date): server unreachable"; exit 0; }

# The clock follows the owner's location (the server works out the zone). The reader has no
# zoneinfo of its own: /etc/localtime points at /var/local/system/tz, which this writes.
TZNAME=$(echo "$J" | grep -o '"tz": "[^"]*"' | cut -d'"' -f4)
if [ -n "$TZNAME" ] && { [ "$TZNAME" != "$(cat "$K/tz.name" 2>/dev/null)" ] || [ ! -s /var/local/system/tz ]; }; then
  if get -o /var/local/system/tz.part "$BASE/paper/tzfile?key=$KEY"; then
    mv /var/local/system/tz.part /var/local/system/tz && echo "$TZNAME" > "$K/tz.name" && echo "sync: time zone now $TZNAME"
  fi
fi

DATES=$(echo "$J" | grep -o '"date": "[0-9-]*"' | grep -o '[0-9]\{4\}-[0-9][0-9]-[0-9][0-9]')

for d in $DATES; do
  f="$NEWS/$PAPER_NAME $d.epub"
  if [ ! -s "$f" ]; then
    get -o "$f.part" "$BASE/paper/$d.epub?key=$KEY" && mv "$f.part" "$f" && echo "sync: got $d"
    rm -f "$f.part"
  fi
done

for f in "$NEWS"/*.epub; do
  [ -e "$f" ] || continue
  d=$(echo "$f" | grep -o '[0-9]\{4\}-[0-9][0-9]-[0-9][0-9]')
  echo "$DATES" | grep -q "$d" || { rm -f "$f"; rm -rf "${f%.epub}.sdr"; echo "sync: pruned $d"; }
done

# Write to a temp name and rename, so KOReader never reads a half-downloaded sleep screen.
TMP="$LIB/.Homepage.$$.png"
if get -o "$TMP" "$BASE/kindle.png?key=$KEY"; then
  mv "$TMP" "$LIB/Homepage.png"
  cp "$LIB/Homepage.png" "$SLEEP/homepage.png"
fi
echo "sync $(date): ok"
