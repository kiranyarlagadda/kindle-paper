#!/bin/sh
# Wake the reader at WAKE_AT (05:45 Eastern by default) every day to refresh the dashboard
# sleep screen and pull the new paper, then let it sleep again. Also syncs whenever someone
# wakes it by hand. Started by boot.sh (restart with restart-listener.sh). Logs to $K/power.log.
# The design and the dead ends are in docs/power.md.
#
# powerd sends readyToSuspend every few seconds while it waits to sleep. Each one either
# holds it off (deferSuspend) while a refresh runs, or sets the next alarm.
K=${PAPER_HOME:-/mnt/us/paper}
. "$K/config.sh"
LOG=$K/power.log
WAKE_AT=${WAKE_AT:-05:45}
WAKE_TZ=${WAKE_TZ:-EST5EDT,M3.2.0,M11.1.0}
BUSY=/tmp/paper-refreshing
[ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 200000 ] && mv "$LOG" "$LOG.old"
exec >> "$LOG" 2>&1
say() { echo "$(date '+%F %T %Z') $*"; }

next_wake() {
  # Test mode: while $K/test-wake exists, every alarm is 2 minutes out.
  [ -f "$K/test-wake" ] && { echo 120; return; }
  now=$(date +%s)
  t=$(TZ=$WAKE_TZ date -d "$WAKE_AT" +%s)
  [ "$t" -le $((now + 120)) ] && t=$((t + 86400))
  echo $((t - now))
}

online() { curl -s -m 5 -o /dev/null ${PROXY:+-x "$PROXY"} "$BASE/health"; }

refresh() {
  touch "$BUSY"
  ( sleep 300; rm -f "$BUSY" ) &     # never hold the reader awake longer than 5 minutes
  sleep 3
  st=$(lipc-get-prop com.lab126.powerd state 2>/dev/null)
  due=$(cat /tmp/paper-alarm-at 2>/dev/null || echo 0); gap=$(( $(date +%s) - due ))
  [ "$gap" -lt 0 ] && gap=$(( -gap ))
  if [ "$st" != "screenSaver" ] || [ "$gap" -gt 90 ]; then
    # Someone pressed the button: just sync, never put the reader back to sleep on them.
    rm -f "$BUSY"; say "refresh: manual wake (state $st), sync only"
    ( online || sleep 15; /bin/sh "$K/sync.sh" ) &
    return
  fi
  # Alarm wake. WiFi won't run in the half-awake screen saver state, so wake fully,
  # sync, then press the power button so KOReader redraws its sleep screen with the new image.
  say "refresh: alarm wake, waking fully"
  lipc-set-prop com.lab126.powerd wakeUp 1 2>/dev/null
  lipc-set-prop com.lab126.cmd wirelessEnable 1 2>/dev/null
  lipc-set-prop com.lab126.wifid enable 1 2>/dev/null
  ok=""
  for i in $(seq 1 40); do
    online && { ok=1; break; }
    [ $((i % 10)) -eq 0 ] && say "refresh: still waiting, state $(lipc-get-prop com.lab126.powerd state 2>/dev/null), wifi $(lipc-get-prop com.lab126.wifid cmState 2>/dev/null)"
    sleep 3
  done
  if [ -n "$ok" ]; then
    say "refresh: online after ~$((i * 3))s"
    /bin/sh "$K/sync.sh"
    report
  else
    say "refresh: never got online (wifi $(lipc-get-prop com.lab126.wifid cmState 2>/dev/null))"
  fi
  rm -f "$BUSY"
  lipc-set-prop com.lab126.powerd powerButton 1 2>/dev/null
  say "refresh: done, back to sleep"
}

# Send the last part of this log to the server so each morning can be checked remotely.
report() {
  tail -40 "$LOG" | curl -s -m 20 -o /dev/null ${PROXY:+-x "$PROXY"} -X POST --data-binary @- "$BASE/kindle/log?key=$KEY"
}

say "== power events started"
lipc-wait-event -m com.lab126.powerd readyToSuspend,wakeupFromSuspend,outOfScreenSaver | while read -r ev rest; do
  case "$ev" in
    readyToSuspend)
      if [ -f "$BUSY" ]; then
        lipc-set-prop com.lab126.powerd deferSuspend 20 2>/dev/null
      else
        s=$(next_wake)
        lipc-set-prop com.lab126.powerd rtcWakeup "$s" 2>/dev/null && echo $(( $(date +%s) + s )) > /tmp/paper-alarm-at
        [ "$s" != "$last" ] && say "alarm set for ${s}s from now"
        last=$s
      fi
      ;;
    wakeupFromSuspend)
      say "woke from suspend"
      rm -f "$K/test-wake"
      refresh &
      ;;
    outOfScreenSaver)
      [ -f "$BUSY" ] || ( online || sleep 15; /bin/sh "$K/sync.sh" ) &
      ;;
  esac
done
