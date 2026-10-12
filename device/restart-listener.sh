#!/bin/sh
# Stop every power-events.sh listener (and its lipc-wait-event child), then start exactly one.
# Walks /proc instead of pattern-killing: run from an ssh one-liner, a pattern match
# also hits the ssh session's own command line.
K=${PAPER_HOME:-/mnt/us/paper}
ME=$$
for p in $(ls /proc | grep -E '^[0-9]+$'); do
  [ "$p" = "$ME" ] && continue
  c=$(tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null) || continue
  case "$c" in "/bin/sh $K/power-events.sh"*|"sh $K/power-events.sh"*)
    for q in $(ls /proc | grep -E '^[0-9]+$'); do
      [ "$(awk '{print $4}' /proc/$q/stat 2>/dev/null)" = "$p" ] && kill "$q" 2>/dev/null
    done
    kill "$p" 2>/dev/null && echo "stopped $p";;
  esac
done
sleep 1
setsid /bin/sh "$K/power-events.sh" </dev/null >/dev/null 2>&1 &
echo $! > /tmp/paper-power.pid
echo "started $(cat /tmp/paper-power.pid)"
