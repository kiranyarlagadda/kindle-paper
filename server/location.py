"""Where the reader's owner is right now, for weather, local news and the clock.

Home Assistant's person entity (HA_PERSON, fed by the phone's Companion app) gives lat/lon; OpenStreetMap
Nominatim turns that into city, county and state. Nothing here knows about any
particular town: it works anywhere Nominatim covers. Falls back to
KINDLE_LAT/KINDLE_LON when HA is down or has no fix.

Geocodes are cached on disk by coordinates rounded to ~1 km, and Nominatim is
called at most once a second (their usage policy). Logs name the city only,
never coordinates.

Env: HA_URL, HA_TOKEN, HA_PERSON, KINDLE_LAT, KINDLE_LON.
"""

import json
import os
import threading
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

HA_URL = os.environ.get("HA_URL", "").rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
HA_PERSON = os.environ.get("HA_PERSON", "person.me")
FALLBACK = (os.environ.get("KINDLE_LAT", ""), os.environ.get("KINDLE_LON", ""))
CACHE = Path(__file__).resolve().parent / "out" / "geocache.json"
UA = os.environ.get("NOMINATIM_UA", "kindle-paper/1.0 (personal home server)")

_lock = threading.Lock()
_last_call = [0.0]
_memo = {"at": 0, "loc": None}


def ha_position():
    if not (HA_URL and HA_TOKEN):
        return None
    try:
        req = Request(f"{HA_URL}/api/states/{HA_PERSON}",
                      headers={"Authorization": f"Bearer {HA_TOKEN}"})
        with urlopen(req, timeout=8) as r:
            attrs = json.load(r).get("attributes", {})
        lat, lon = attrs.get("latitude"), attrs.get("longitude")
        if lat is None or lon is None:
            return None
        return float(lat), float(lon)
    except Exception as e:
        print(f"location: HA lookup failed: {type(e).__name__}", flush=True)
        return None


def _load_cache():
    try:
        return json.loads(CACHE.read_text())
    except (OSError, ValueError):
        return {}


def reverse(lat, lon):
    """{city, county, state, state_code} for a point, or {} if Nominatim fails."""
    key = f"{lat:.2f},{lon:.2f}"
    with _lock:
        cache = _load_cache()
        if key in cache:
            return cache[key]
        wait = 1.1 - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()
        q = urlencode({"format": "jsonv2", "zoom": 10, "lat": f"{lat:.4f}", "lon": f"{lon:.4f}",
                       "accept-language": "en"})
        try:
            req = Request(f"https://nominatim.openstreetmap.org/reverse?{q}", headers={"User-Agent": UA})
            with urlopen(req, timeout=10) as r:
                addr = json.load(r).get("address", {})
        except Exception as e:
            print(f"location: reverse geocode failed: {type(e).__name__}", flush=True)
            return {}
        iso = addr.get("ISO3166-2-lvl4", "")
        place = {
            "city": next((addr[k] for k in ("city", "town", "village", "hamlet", "municipality",
                                            "suburb") if addr.get(k)), ""),
            "county": addr.get("county", ""),
            "state": addr.get("state", ""),
            "state_code": iso.split("-", 1)[1] if "-" in iso else "",
            "country_code": addr.get("country_code", ""),
        }
        cache[key] = place
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, indent=1))
        os.replace(tmp, CACHE)
        return place


def current(max_age=600):
    """Best current location: {lat, lon, city, county, state, state_code, source}."""
    if _memo["loc"] and time.time() - _memo["at"] < max_age:
        return _memo["loc"]
    pos, source = ha_position(), "home-assistant"
    if not pos:
        try:
            pos, source = (float(FALLBACK[0]), float(FALLBACK[1])), "fallback"
        except ValueError:
            return None
    loc = {"lat": pos[0], "lon": pos[1], "source": source, **reverse(*pos)}
    if not loc.get("city") and not loc.get("county"):
        print(f"location: no place name ({source})", flush=True)
    else:
        print(f"location: {label(loc)} ({source})", flush=True)
    _memo.update(at=time.time(), loc=loc)
    return loc


def label(loc, long=False):
    """'Springfield, IL', or 'Springfield, Illinois' when long."""
    if not loc:
        return ""
    region = loc.get("state") if long else (loc.get("state_code") or loc.get("state"))
    town = loc.get("city") or loc.get("county")
    return ", ".join(x for x in (town, region) if x)


_TZ = {}


def timezone(loc=None):
    """{'name': 'America/Chicago', 'posix': 'CST6CDT,M3.2.0,M11.1.0'} for loc (default: now).

    The zone comes from Open-Meteo (timezone=auto); the POSIX rule is the footer line of the
    system tzfile, which is what a reader without zoneinfo needs in TZ."""
    loc = loc or current()
    if not loc:
        return None
    key = (round(loc["lat"], 1), round(loc["lon"], 1))
    if key in _TZ:
        return _TZ[key]
    try:
        with urlopen(f"https://api.open-meteo.com/v1/forecast?latitude={loc['lat']:.4f}"
                     f"&longitude={loc['lon']:.4f}&timezone=auto&forecast_days=1", timeout=15) as r:
            name = json.load(r)["timezone"]
        footer = open(f"/usr/share/zoneinfo/{name}", "rb").read().rstrip(b"\n").rsplit(b"\n", 1)[-1]
        out = {"name": name, "posix": footer.decode()}
    except Exception as e:
        print(f"location: timezone lookup failed: {type(e).__name__}", flush=True)
        return None
    _TZ[key] = out
    return out
