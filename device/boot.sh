#!/bin/sh
# Startup for the reader: Tailscale, SSH for debugging, KOReader as the home screen,
# an hourly sync of the paper and the morning page, and the daily wake listener.
# Run once at startup, after the UI framework is up.
K=${PAPER_HOME:-/mnt/us/paper}
TS=${TS_DIR:-/mnt/us/tailscale}
SOCK=/tmp/tailscaled.sock
LOG=$K/boot.log
[ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 500000 ] && mv "$LOG" "$LOG.old"
exec >> "$LOG" 2>&1
echo "== boot $(date)"

# Let loopback, SSH, Tailscale's UDP port and replies through the reader's firewall.
hole() { iptables -C INPUT "$@" -j ACCEPT 2>/dev/null || iptables -I INPUT 1 "$@" -j ACCEPT; }
hole -i lo
hole -p tcp --dport 2222
hole -p udp --dport 41641
hole -m state --state RELATED,ESTABLISHED

# Tailscale in userspace mode: no TUN device, outbound traffic goes through its local proxies.
if ! "$TS/tailscale" --socket=$SOCK status >/dev/null 2>&1; then
  mkdir -p "$TS/state"
  cd "$TS" && ./tailscaled --tun=userspace-networking --statedir="$TS/state" --socket=$SOCK \
    --port=41641 --socks5-server=localhost:1055 --outbound-http-proxy-listen=localhost:1056 \
    > /tmp/tailscaled.log 2>&1 &
  sleep 10
  "$TS/tailscale" --socket=$SOCK up --hostname="${TS_HOSTNAME:-reader}" --ssh --accept-routes --timeout=60s
fi

# KOReader ships a dropbear SSH server; key auth only.
if [ ! -f /tmp/dropbear_koreader.pid ] || ! kill -0 "$(cat /tmp/dropbear_koreader.pid)" 2>/dev/null; then
  cd /mnt/us/koreader && ./dropbear -E -R -p 2222 -P /tmp/dropbear_koreader.pid -s
fi

# Open KOReader so the stock home screen never shows. Once per boot (flag in /tmp),
# so a crash or a deliberate exit can't turn into a launch loop.
if [ ! -f "$K/no-autostart" ] && [ ! -f /tmp/paper-koreader-started ]; then
  touch /tmp/paper-koreader-started
  sleep 20
  if ! ps | grep -v grep | grep -q reader.lua; then
    cd /mnt/us/koreader && /bin/sh ./koreader.sh > /tmp/koreader-boot.log 2>&1 &
  fi
fi

if [ ! -f /tmp/paper-sync.pid ] || ! kill -0 "$(cat /tmp/paper-sync.pid)" 2>/dev/null; then
  ( while :; do /bin/sh "$K/sync.sh"; sleep 3600; done ) &
  echo $! > /tmp/paper-sync.pid
fi

# Daily wake to refresh the sleep screen (see power-events.sh and docs/power.md).
if [ ! -f /tmp/paper-power.pid ] || ! kill -0 "$(cat /tmp/paper-power.pid)" 2>/dev/null; then
  /bin/sh "$K/power-events.sh" &
  echo $! > /tmp/paper-power.pid
fi
echo "boot done $(date)"
