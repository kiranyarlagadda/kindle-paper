# Copy to config.sh next to the other scripts on the reader and fill it in.
# Sourced by sync.sh and power-events.sh.

BASE=http://your-server:8484       # brain-api, as the reader reaches it (tailnet name or LAN address)
KEY=change-me                      # HABIT_TOKEN from brain-api.env
PROXY=http://localhost:1056        # tailscaled's outbound HTTP proxy (userspace networking); empty for none
WAKE_AT=05:45                      # daily refresh time
WAKE_TZ="EST5EDT,M3.2.0,M11.1.0"   # POSIX TZ the wake time is in, so it stays put when the clock's zone changes
