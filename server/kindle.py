"""Kindle e-ink morning page for brain-api.

Renders one static HTML page sized for a Kindle Paperwhite 5 (1236x1648,
portrait). No JavaScript: headless Chrome screenshots it once a day and the
Kindle shows the PNG. Every section drops out cleanly when it has no data.

Weather follows the owner via location.py (Home Assistant, then KINDLE_LAT/LON);
Open-Meteo picks the local time zone. The section hides if neither is known.

Tasks come from Apple Reminders when out/reminders.json is fresh (kindle_render.sh
refreshes it with remindctl): the Work list on the right, the other lists on the left.
Otherwise the page falls back to the notes folder's tasks and todo list.

Demo: `python3 kindle.py --demo examples/demo-morning.json > page.html` renders the
page from a fixtures file (made-up tasks, reminders, weather and date) with no network
and no notes folder. tools/render-media.sh screenshots it the same way production does.
"""

import html
import json
import os
import re
import sys
import time
from pathlib import Path
from datetime import date, datetime, timedelta
from urllib.request import urlopen

import location

# Public domain translations, checked against their sources: Marcus Aurelius and Epictetus
# (George Long), Seneca (Richard Gummere), the Gita (Edwin Arnold), the Dhammapada (Max Muller),
# Plato (Benjamin Jowett), Aristotle (W. D. Ross), Heraclitus (John Burnet), Confucius (James
# Legge), Tagore (Stray Birds, 1916). Famous lines with doubtful sources are left out on purpose.
QUOTES = [
    ("In the morning when thou risest unwillingly, let this thought be present: I am rising to the work of a human being.", "Marcus Aurelius", "Meditations V.1"),
    ("Very little is needed to make a happy life; it is all within thyself, in thy way of thinking.", "Marcus Aurelius", "Meditations VII.67"),
    ("Look within. Within is the fountain of good, and it will ever bubble up, if thou wilt ever dig.", "Marcus Aurelius", "Meditations VII.59"),
    ("If it is not right, do not do it: if it is not true, do not say it.", "Marcus Aurelius", "Meditations XII.17"),
    ("Confine thyself to the present.", "Marcus Aurelius", "Meditations VII.29"),
    ("Men are disturbed not by the things which happen, but by the opinions about the things.", "Epictetus", "Enchiridion 5"),
    ("First say to yourself what you would be; and then do what you have to do.", "Epictetus", "Discourses III.23"),
    ("We are more often frightened than hurt; and we suffer more from imagination than from reality.", "Seneca", "Letters to Lucilius XIII"),
    ("Nothing, Lucilius, is ours, except time.", "Seneca", "Letters to Lucilius I"),
    ("The life which is unexamined is not worth living.", "Socrates", "Plato, Apology 38a"),
    ("We become just by doing just acts, temperate by doing temperate acts, brave by doing brave acts.", "Aristotle", "Nicomachean Ethics II.1"),
    ("You cannot step twice into the same rivers; for fresh waters are ever flowing in upon you.", "Heraclitus", "Fragment 41"),
    ("Let right deeds be thy motive, not the fruit which comes from them.", "Bhagavad Gita", "II, trans. Edwin Arnold"),
    ("All that we are is the result of what we have thought.", "The Buddha", "Dhammapada 1"),
    ("Hatred does not cease by hatred at any time: hatred ceases by love. This is an old rule.", "The Buddha", "Dhammapada 5"),
    ("If one man conquer in battle a thousand times thousand men, and if another conquer himself, he is the greatest of conquerors.", "The Buddha", "Dhammapada 103"),
    ("If you shed tears when you miss the sun, you also miss the stars.", "Rabindranath Tagore", "Stray Birds 6"),
    ("Arise, awake, and stop not till the goal is reached.", "Swami Vivekananda", "after the Katha Upanishad"),
    ("When you know a thing, to hold that you know it; and when you do not know a thing, to allow that you do not know it; this is knowledge.", "Confucius", "Analects II.17"),
    ("Our life is frittered away by detail. Simplify, simplify.", "Henry David Thoreau", "Walden"),
    ("Trust thyself: every heart vibrates to that iron string.", "Ralph Waldo Emerson", "Self-Reliance"),
    ("If there is no struggle, there is no progress.", "Frederick Douglass", "West India Emancipation speech, 1857"),
]

WMO = {  # code -> (word, icon)
    0: ("Clear", "sun"), 1: ("Mostly clear", "sun"), 2: ("Partly cloudy", "part"),
    3: ("Overcast", "cloud"), 45: ("Fog", "fog"), 48: ("Freezing fog", "fog"),
    51: ("Light drizzle", "rain"), 53: ("Drizzle", "rain"), 55: ("Heavy drizzle", "rain"),
    56: ("Freezing drizzle", "rain"), 57: ("Freezing drizzle", "rain"),
    61: ("Light rain", "rain"), 63: ("Rain", "rain"), 65: ("Heavy rain", "rain"),
    66: ("Freezing rain", "rain"), 67: ("Freezing rain", "rain"),
    71: ("Light snow", "snow"), 73: ("Snow", "snow"), 75: ("Heavy snow", "snow"),
    77: ("Snow grains", "snow"), 80: ("Showers", "rain"), 81: ("Showers", "rain"),
    82: ("Heavy showers", "rain"), 85: ("Snow showers", "snow"), 86: ("Snow showers", "snow"),
    95: ("Thunderstorms", "storm"), 96: ("Thunderstorms", "storm"), 99: ("Thunderstorms", "storm"),
}

_CLOUD = '<path d="M18 46h30a11 11 0 0 0 0-22 16 16 0 0 0-30-3 12 12 0 0 0 0 25z"/>'
ICONS = {
    "sun": '<circle cx="32" cy="32" r="11"/><path d="M32 7v8M32 49v8M7 32h8M49 32h8M14 14l6 6M44 44l6 6M14 50l6-6M44 20l6-6"/>',
    "part": '<circle cx="23" cy="22" r="8"/><path d="M23 6v4M7 22h4M11 10l3 3M35 10l-3 3"/>'
            '<path d="M24 52h24a9 9 0 0 0 0-18 13 13 0 0 0-24-2 10 10 0 0 0 0 20z" fill="#fff"/>',
    "cloud": _CLOUD,
    "rain": _CLOUD + '<path d="M22 52l-3 7M33 52l-3 7M44 52l-3 7"/>',
    "snow": _CLOUD + '<path d="M21 55h.1M32 58h.1M43 55h.1" stroke-width="6"/>',
    "storm": _CLOUD + '<path d="M34 48l-6 9h8l-5 8"/>',
    "fog": '<path d="M16 30a12 12 0 0 1 1-1 16 16 0 0 1 30 3 9 9 0 0 1 5 6"/><path d="M10 44h44M16 53h34"/>',
}

_wx_cache = {}  # rounded "lat,lon" -> (fetched_at, data)


def esc(s):
    return html.escape(str(s))


def icon(name, size):
    return (f'<svg class="ic" width="{size}" height="{size}" viewBox="0 0 64 64" fill="none" '
            f'stroke="#000" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round">'
            f'{ICONS.get(name, ICONS["cloud"])}</svg>')


def clock(iso):
    t = datetime.fromisoformat(iso)
    return t.strftime("%-I:%M")


def weather(loc=None):
    """Open-Meteo forecast for loc (default: wherever the owner is now), cached 30 min."""
    loc = loc or location.current()
    if not loc:
        return None
    key = f"{loc['lat']:.2f},{loc['lon']:.2f}"
    at, data = _wx_cache.get(key, (0, None))
    if data and time.time() - at < 1800:
        return data
    url = ("https://api.open-meteo.com/v1/forecast"
           f"?latitude={loc['lat']:.4f}&longitude={loc['lon']:.4f}&timezone=auto&forecast_days=2"
           "&temperature_unit=fahrenheit&wind_speed_unit=mph"
           "&hourly=temperature_2m,weather_code,precipitation_probability"
           "&daily=weather_code,temperature_2m_max,temperature_2m_min,"
           "precipitation_probability_max,sunrise,sunset,wind_speed_10m_max")
    try:
        with urlopen(url, timeout=10) as r:
            data = json.load(r)
    except Exception as e:
        print(f"kindle: weather fetch failed: {e}", flush=True)
        return data
    data["place"] = location.label(loc)
    _wx_cache[key] = (time.time(), data)
    return data


def weather_html(wx):
    if not wx:
        return "", ""
    d = wx["daily"]
    word, ic = WMO.get(d["weather_code"][0], ("", "cloud"))
    rain = d["precipitation_probability_max"][0]
    head = f"""
      <div class="wx">
        {icon(ic, 132)}
        <div class="wx-temp"><span class="hi">{round(d["temperature_2m_max"][0])}<sup>°</sup></span><span class="lo">{round(d["temperature_2m_min"][0])}<sup>°</sup></span></div>
        <div class="wx-word">{esc(word)}</div>
        <div class="wx-meta">Chance of rain {rain}% &middot; Wind {round(d["wind_speed_10m_max"][0])} mph</div>
        <div class="wx-meta">Sunrise {clock(d["sunrise"][0])} &middot; Sunset {clock(d["sunset"][0])}</div>
        {f'<div class="wx-meta wx-place">{esc(wx["place"])}</div>' if wx.get("place") else ""}
      </div>"""
    h = wx["hourly"]
    today = d["time"][0]
    cells = []
    for hour in (8, 11, 14, 17, 20, 23):
        key = f"{today}T{hour:02d}:00"
        if key not in h["time"]:
            continue
        i = h["time"].index(key)
        label = datetime(2000, 1, 1, hour).strftime("%-I %p").lower()
        p = h["precipitation_probability"][i]
        cells.append(f"""
        <div class="hr">
          <div class="hr-t">{label}</div>
          {icon(WMO.get(h["weather_code"][i], ("", "cloud"))[1], 64)}
          <div class="hr-v">{round(h["temperature_2m"][i])}°</div>
          <div class="hr-p">{f"{p}%" if p >= 20 else "&nbsp;"}</div>
        </div>""")
    strip = f'<section class="hours">{"".join(cells)}</section>' if cells else ""
    return head, strip


def short_due(iso):
    try:
        return date.fromisoformat(iso).strftime("%b %-d")
    except ValueError:
        return ""


def trim(text, n=58):
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rsplit(" ", 1)[0] + "…"


REMINDERS_JSON = Path(__file__).resolve().parent / "out" / "reminders.json"
WORK_LIST = os.environ.get("KINDLE_WORK_LIST", "Work")
# The left column shows only these Reminders lists (comma list).
LEFT_LISTS = {x.strip().lower() for x in os.environ.get("KINDLE_LEFT_LISTS", "Reminders").split(",") if x.strip()}
# Lists to leave off the page entirely (comma list), and how old an overdue item can be
# before it stops crowding out today's (older ones only count toward "and N more").
SKIP_LISTS = {x.strip().lower() for x in os.environ.get("KINDLE_SKIP_LISTS", "").split(",") if x.strip()}
OVERDUE_DAYS = int(os.environ.get("KINDLE_OVERDUE_DAYS", "14"))
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]+")
_LIVE = object()  # page() default: read the reminders file


def _local_day(stamp):
    """'2026-10-13T01:30:00Z' -> the local calendar day, as YYYY-MM-DD."""
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone().date().isoformat()
    except (AttributeError, ValueError):
        return ""


def _field(r, *names):
    for n in names:
        if r.get(n) not in (None, ""):
            return r[n]
    return None


def reminders(data=None, today=None):
    """(everything else, work) open reminders, or None if there are none to read.

    data is remindctl's `show all --json` output; by default it comes from out/reminders.json,
    which kindle_render.sh refreshes right before each render. A missing file, or one older
    than 36 hours, returns None so the page falls back to the notes folder."""
    if data is None:
        try:
            if time.time() - REMINDERS_JSON.stat().st_mtime > 36 * 3600:
                return None
            data = json.loads(REMINDERS_JSON.read_text())
        except (OSError, ValueError):
            return None
    if isinstance(data, dict):
        data = _field(data, "reminders", "items", "data") or []
    today = today or date.today()
    day = today.isoformat()
    mine, work = [], []
    for r in data if isinstance(data, list) else []:
        if not isinstance(r, dict) or _field(r, "isCompleted", "completed"):
            continue
        text = " ".join(_EMOJI.sub("", _field(r, "title", "name", "text") or "").split())
        if not text:
            continue
        due = _local_day(str(_field(r, "dueDate", "due") or ""))
        lst = _field(r, "listName", "list", "calendar", "calendarTitle") or ""
        if isinstance(lst, dict):
            lst = _field(lst, "title", "name") or ""
        name = str(lst).strip().lower()
        if name in SKIP_LISTS:
            continue
        item = {"text": text, "due": due, "overdue": bool(due and due < day)}
        if name == WORK_LIST.lower():
            work.append(item)
        elif name in LEFT_LISTS:
            mine.append(item)
    # Recent overdue first, then today, then upcoming by date, then undated. Long-overdue
    # items go last so they only show up in the "and N more" count.
    cutoff = (today - timedelta(days=OVERDUE_DAYS)).isoformat()
    order = lambda t: (t["overdue"] and t["due"] < cutoff, not t["overdue"], t["due"] != day,
                       t["due"] or "9999", t["text"].lower())
    return sorted(mine, key=order), sorted(work, key=order)


def task_rows(items, cap=3):
    rows = []
    for t in items[:cap]:
        meta = ""
        if t.get("overdue"):
            meta = f'<span class="meta"><b>Overdue</b> since {short_due(t["due"])}</span>'
        elif t.get("due"):
            meta = f'<span class="meta">Due {short_due(t["due"])}</span>'
        rows.append(f'<li><span class="box"></span><span class="txt">{esc(trim(t["text"], 64))}{meta}</span></li>')
    if len(items) > cap:
        rows.append(f'<li class="more">and {len(items) - cap} more in Reminders</li>')
    return "".join(rows)


def habit_rows(habits):
    rows = []
    for h in habits:
        streak = h["streak"]
        s = f'<span class="meta">{streak} day streak</span>' if streak else ""
        rows.append(f'<li><span class="box"></span><span class="txt">{esc(trim(h["text"], 48))}{s}</span></li>')
    return "".join(rows)


def health_html(health, today=None):
    if not health or not health.get("day"):
        return ""
    if date.fromisoformat(health["day"]) < (today or date.today()) - timedelta(days=1):
        return ""  # stale: the import has stopped, so say nothing rather than mislead
    stats = []
    if "sleep_min" in health:
        m = int(float(health["sleep_min"]))
        stats.append((f"{m // 60}<small>h</small> {m % 60:02d}<small>m</small>", "Sleep"))
    if "rhr" in health:
        stats.append((f'{round(float(health["rhr"]))}', "Resting HR"))
    if "hrv" in health:
        stats.append((f'{round(float(health["hrv"]))}', "HRV"))
    if "steps" in health:
        stats.append((f'{int(float(health["steps"])):,}', "Steps"))
    if not stats:
        return ""
    cells = "".join(f'<div class="stat"><div class="sv">{v}</div><div class="sl">{l}</div></div>' for v, l in stats)
    return f'<section class="health"><h2>Last night</h2><div class="stats">{cells}</div></section>'


def page(habits, todos, wx=None, now=None, rem=_LIVE):
    """The morning page. wx, now and rem are for the demo; live calls fetch the weather
    and read out/reminders.json. rem is reminders() output, or None for the notes layout."""
    now = now or datetime.now()
    today = now.date()
    # Step through the list 9 at a time (9 and 22 share no factor, so every quote still comes
    # up once per cycle) so neighbouring days land on different traditions.
    q_text, q_who, q_src = QUOTES[(today.toordinal() * 9) % len(QUOTES)]
    wx_head, wx_strip = weather_html(wx if wx is not None else weather())

    # Left column: today's note tasks, then anything overdue.
    today_items = [{"text": t["text"]} for t in habits.get("tasks", []) if not t["done"]]
    later = []
    for t in todos.get("overdue", []) + todos.get("upcoming", []):
        if t["due"] and t["due"] < today.isoformat():
            today_items.append({**t, "overdue": True})
        else:
            later.append(t)
    later.sort(key=lambda t: t["due"] or "9999")

    if rem is _LIVE:
        rem = reminders(today=today)
    if rem is not None:
        # Reminders straight from Apple Reminders: the Work list on the right, every other list on the left.
        mine, work = rem
        note_tasks = [{"text": t["text"]} for t in habits.get("tasks", []) if not t["done"]]
        cutoff = (today - timedelta(days=OVERDUE_DAYS)).isoformat()
        recent = [t for t in mine if t["overdue"] and t["due"] >= cutoff]
        old = [t for t in mine if t["overdue"] and t["due"] < cutoff]
        mine = recent + note_tasks + [t for t in mine if not t["overdue"]] + old
        left = (f'<h2>Reminders</h2><ul class="list">{task_rows(mine, 3)}</ul>' if mine
                else '<h2>Reminders</h2><p class="empty">A clear day.</p>')
        right = (f'<h2>Work</h2><ul class="list">{task_rows(work, 3)}</ul>' if work
                 else '<h2>Work</h2><p class="empty">Nothing for work.</p>')
    elif habits.get("habits"):
        right = f'<h2>Habits</h2><ul class="list">{habit_rows(habits["habits"])}</ul>'
    else:
        right = f'<h2>Coming up</h2><ul class="list">{task_rows(later)}</ul>' if later else ""
    if rem is None:
        left = (f'<h2>Today</h2><ul class="list">{task_rows(today_items)}</ul>' if today_items
                else '<h2>Today</h2><p class="empty">A clear day.</p>')

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Morning page</title>
<style>
  @page {{ size: 1236px 1648px; margin: 0; }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; width: 1236px; height: 1648px; background: #fff; color: #000; overflow: hidden; }}
  body {{ font-family: "Iowan Old Style", "Hoefler Text", Georgia, serif; padding: 84px 92px 64px;
         display: flex; flex-direction: column; -webkit-font-smoothing: antialiased; }}
  .sc {{ font-variant: small-caps; letter-spacing: .14em; text-transform: lowercase; }}
  header {{ display: flex; justify-content: space-between; align-items: flex-start; }}
  .dow {{ font-size: 40px; letter-spacing: .32em; text-transform: uppercase; font-weight: 600; }}
  .day {{ font-family: Didot, "Bodoni 72", serif; font-size: 272px; line-height: .82; margin: 18px 0 8px -8px; }}
  .month {{ font-size: 68px; font-style: italic; line-height: 1.05; }}
  .yr {{ font-size: 30px; color: #555; letter-spacing: .2em; text-transform: uppercase; margin-top: 12px; }}
  .wx {{ text-align: right; padding-top: 4px; }}
  .wx .ic {{ display: block; margin: 0 0 6px auto; }}
  .wx-temp {{ font-family: Didot, "Bodoni 72", serif; line-height: 1; }}
  .hi {{ font-size: 132px; }}
  .wx-temp sup {{ font-family: "Iowan Old Style", Georgia, serif; font-size: .42em; vertical-align: .95em; margin-left: 2px; }}
  .lo {{ font-size: 64px; color: #555; margin-left: 14px; }}
  .wx-word {{ font-size: 44px; font-style: italic; margin: 10px 0 14px; }}
  .wx-meta {{ font-size: 28px; color: #333; line-height: 1.5; }}
  .wx-place {{ font-style: italic; color: #555; }}
  .rule {{ border-top: 5px solid #000; border-bottom: 2px solid #000; height: 11px; margin: 40px 0 0; }}
  .hours {{ display: flex; justify-content: space-between; padding: 26px 6px 20px; border-bottom: 2px solid #000; }}
  .hr {{ text-align: center; width: 140px; }}
  .hr-t {{ font-size: 26px; letter-spacing: .14em; text-transform: uppercase; color: #333; }}
  .hr .ic {{ margin: 8px 0 2px; }}
  .hr-v {{ font-size: 46px; }}
  .hr-p {{ font-size: 24px; color: #555; font-style: italic; }}
  main {{ display: grid; grid-template-columns: 1fr 1fr; gap: 64px; margin-top: 36px; flex: 1; min-height: 0; overflow: hidden; }}
  h2 {{ font-size: 30px; font-weight: 600; letter-spacing: .3em; text-transform: uppercase; margin: 0 0 22px;
        padding-bottom: 12px; border-bottom: 2px solid #000; }}
  .list {{ list-style: none; margin: 0; padding: 0; }}
  .list li {{ display: flex; align-items: baseline; gap: 18px; font-size: 32px; line-height: 1.2; padding: 4px 0; }}
  .box {{ flex: none; width: 28px; height: 28px; border: 3px solid #000; transform: translateY(3px); }}
  .txt {{ flex: 1; }}
  .list li.more {{ display: block; padding-left: 46px; font-size: 26px; font-style: italic; color: #555; }}
  .meta {{ display: block; font-size: 25px; font-style: italic; color: #555; margin-top: 2px; }}
  .meta b {{ font-style: normal; font-weight: 600; color: #000; letter-spacing: .14em; text-transform: uppercase; font-size: 21px; }}
  .empty {{ font-size: 36px; font-style: italic; color: #555; margin: 8px 0; }}
  .health {{ margin-top: 36px; }}
  .stats {{ display: flex; justify-content: space-between; }}
  .stat {{ text-align: center; flex: 1; }}
  .sv {{ font-family: Didot, "Bodoni 72", serif; font-size: 76px; line-height: 1.05; }}
  .sv small {{ font-size: 40px; }}
  .sl {{ font-size: 24px; letter-spacing: .18em; text-transform: uppercase; color: #555; margin-top: 6px; }}
  blockquote {{ margin: 32px 0 0; padding: 36px 0 0 84px; border-top: 2px solid #000; position: relative; }}
  blockquote::before {{ content: "\\201C"; position: absolute; left: -4px; top: 26px; font-family: Didot, serif;
                        font-size: 150px; line-height: 1; }}
  .q {{ font-size: 44px; font-style: italic; line-height: 1.3; margin: 0; }}
  cite {{ display: block; font-style: normal; font-size: 26px; letter-spacing: .2em; text-transform: uppercase;
          margin-top: 22px; color: #333; }}
  cite span {{ letter-spacing: 0; text-transform: none; font-style: italic; color: #555; font-size: 28px; }}
  footer {{ font-size: 20px; color: #888; letter-spacing: .12em; text-transform: uppercase; margin-top: 28px;
            display: flex; justify-content: space-between; }}
</style></head>
<body>
  <header>
    <div>
      <div class="dow">{today.strftime("%A")}</div>
      <div class="day">{today.day}</div>
      <div class="month">{today.strftime("%B")}</div>
      <div class="yr">{today.year} &middot; Week {today.isocalendar()[1]}</div>
    </div>
    {wx_head}
  </header>
  <div class="rule"></div>
  {wx_strip}
  <main>
    <section>{left}</section>
    <section>{right}</section>
  </main>
  {health_html(habits.get("health"), today)}
  <blockquote>
    <p class="q">{esc(q_text)}</p>
    <cite>{esc(q_who)} <span>&middot; {esc(q_src)}</span></cite>
  </blockquote>
  <footer><span>Printed {now.strftime("%-I:%M %p")}</span><span>Day {today.timetuple().tm_yday} of {366 if today.year % 4 == 0 else 365}</span></footer>
</body></html>"""


def demo(path):
    """Render the page from a fixtures file: {now, weather, habits, todos, reminders}.

    Reminders come from the fixture's remindctl-shaped list, never the real file; without
    one the page shows the notes layout (Today and Habits)."""
    with open(path) as f:
        fx = json.load(f)
    wx = fx["weather"]
    wx.setdefault("place", fx.get("place", ""))
    now = datetime.fromisoformat(fx["now"])
    rem = reminders(fx["reminders"], now.date()) if "reminders" in fx else None
    return page(fx.get("habits", {}), fx.get("todos", {}), wx=wx, now=now, rem=rem)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--demo":
        sys.stdout.write(demo(sys.argv[2]))
    else:
        sys.exit("usage: kindle.py --demo FIXTURES.json > page.html")
