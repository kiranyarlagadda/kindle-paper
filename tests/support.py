"""Shared setup for the tests: put server/ on the import path and switch off anything
that could reach the network (no model, no Home Assistant, no fixed location).

Import this before any server module, since those read their settings at import time."""

import os
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent / "server"
EXAMPLES = SERVER.parent / "examples"

for key in ("LLM_URL", "LLM_KEY", "HA_URL", "HA_TOKEN", "KINDLE_LAT", "KINDLE_LON", "KINDLE_SKIP_LISTS"):
    os.environ[key] = ""
os.environ.update(KINDLE_WORK_LIST="Work", KINDLE_LEFT_LISTS="Reminders", KINDLE_OVERDUE_DAYS="14")

if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))
