#!/bin/bash
# Start the HTTP server with the environment file loaded. Run by launchd with KeepAlive.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
set -a
source "${BRAIN_API_ENV:-$HOME/.config/brain-api/brain-api.env}"
set +a
cd "$HERE"
exec "${PYTHON:-/usr/bin/python3}" brain_api.py
