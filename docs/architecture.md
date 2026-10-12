# Architecture

Two outputs, one server, one reader. The server builds a daily EPUB and a daily PNG; the reader wakes once a morning and fetches both.

![Architecture diagram](../media/architecture.svg)

## Server files

| File | What it does |
|---|---|
| `server/paper.py` | Builds the edition: gathers every section, renders XHTML pages, zips an EPUB3, prunes editions older than 7 days. Also serves the browser preview, the edition list and the time zone file. `--demo` builds from a fixtures file instead. |
| `server/paper_sources.py` | World, local, markets, science, Reddit, Plex and calendar. |
| `server/sports.py` | ESPN scoreboards (NBA, NFL, MLB, soccer, tennis), Jolpica for the next F1 race, the watchability score, spoiler-free lines. |
| `server/extract.py` | Feed parsing, Google News search and link resolving, a readability-style article extractor on `html.parser`, grayscale image downscaling with `sips`. |
| `server/llm.py` | OpenAI-compatible chat client: JSON replies, `<think>` stripping, a shared time budget, a circuit breaker after three failures, and a scrubber that rewrites long dashes. |
| `server/location.py` | Phone location from a Home Assistant person entity, reverse geocoding with OpenStreetMap Nominatim (cached, rate limited), fallback coordinates, and the IANA zone plus POSIX rule for that spot. |
| `server/kindle.py` | The morning page: date, weather with an hourly strip, tasks, habits or upcoming items, a Stoic quote in a public domain translation. `--demo` renders it from a fixtures file. |
| `server/kindle_render.sh` | Headless Chrome screenshot of `/kindle` at 1236x1648, converted to 8-bit gray with `sips`. |
| `server/paper_build.sh`, `server/brain_api.sh` | Load the environment file and run the build or the server. |
| `server/brain_api.py` | The HTTP server. It started as a small read-only JSON API over a notes folder for a desktop dashboard, and grew the Kindle endpoints. |

## Device files

| File | What it does |
|---|---|
| `device/boot.sh` | Run once at startup. Starts Tailscale in userspace mode, KOReader's SSH server for debugging, KOReader itself (once per boot), the hourly sync loop and the power listener. |
| `device/power-events.sh` | Listens to powerd. Sets the daily alarm before each suspend and runs the morning refresh. See [power.md](power.md). |
| `device/sync.sh` | Fetches the edition list, downloads new editions, prunes old ones, installs the time zone file when the zone changes, and replaces the sleep screen image. |
| `device/restart-listener.sh` | Stops every running power listener by exact command line and starts one. |
| `device/config.example.sh` | Server address, token, proxy, wake time. |

## Daily timeline

```
05:15  launchd: paper_build.sh -> paper.py build
         location -> weather, calendar, sports, world, local, markets, science, reddit, media
         model calls share a 900 s budget; every call has a fallback
         -> out/paper/YYYY-MM-DD.epub and latest.epub; older than 7 days pruned
05:30  launchd: kindle_render.sh -> out/kindle.png
05:45  reader wakes on its RTC alarm (see power.md)
         GET  /paper/editions.json   edition list plus the current time zone
         GET  /paper/tzfile          only when the zone changed
         GET  /paper/<date>.epub     anything new, into the Newspaper folder
         GET  /kindle.png            becomes the sleep screen
         POST /kindle/log            last 40 lines of the power log
         then back to sleep, with the alarm set for tomorrow
```

The sync loop also runs at boot and every hour the device is awake, and whenever someone wakes it by hand.

## Endpoints

The Kindle and paper routes need `?key=` (the `HABIT_TOKEN`) and a request from an address in `HABIT_NETS` (RFC 1918, the tailnet's CGNAT range and loopback by default).

| Path | Returns |
|---|---|
| `/kindle`, `/kindle.png` | The morning page as HTML, and the rendered PNG |
| `/paper` | Browser preview of the latest front page, links rewritten to keep working |
| `/paper/view/<page>` | One page, stylesheet or image from the latest edition |
| `/paper/latest.epub`, `/paper/<date>.epub` | The editions |
| `/paper/editions.json` | Edition list, plus `tz` and `tz_posix` for the current location |
| `/paper/tzfile` | The compiled zoneinfo file for that zone |
| `POST /kindle/log` | The reader's power log; the server keeps the last 64 KB |

The JSON routes for the desktop dashboard (`/todos`, `/projects`, `/today`) use a separate bearer token and a strict IP allowlist. The habit widget (`/habits`) is the only route that writes to the notes folder, and only to checkbox lines in today's note.

## The EPUB

- `mimetype` first, stored uncompressed, then `META-INF/container.xml`, then everything under `OEBPS/`.
- An EPUB3 nav document for the table of contents and a landmarks entry pointing at the front page, plus an NCX for older readers.
- Score pages for flagged games are in the manifest and the spine, marked `linear="no"`, and left out of the table of contents.
- Images are downscaled to at most 1072 px wide, converted to grayscale JPEG, and capped at about 4 MB per edition. A normal edition is around half a megabyte.
- Every XHTML page is parsed as XML before zipping. The package files are too, since a failure there is a bug in the code rather than in the day's news.

## Calendar permission

The calendar block shells out to `icalBuddy`, which reads Calendar through EventKit, so macOS asks for permission per program. From a `launchd` job the request just waits, and the build times out after 20 seconds and carries on without a calendar. To grant it: open Calendar.app once, start the build job by hand with `launchctl kickstart`, and watch the screen for the "would like to access your calendars" dialog.

## Hardware

- Kindle Paperwhite 5 (2021): 6.8 inches, 300 ppi, 1236x1648, 16 shades of gray
- A Mac mini for the builds, the server and the render
- A GPU box running Qwen3.8-27B through llama.cpp, behind a LiteLLM gateway
- Home Assistant, used here only for the phone's location
