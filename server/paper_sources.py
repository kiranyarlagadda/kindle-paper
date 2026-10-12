"""Non-sports sections of Kiran's Paper: world, local, markets, science, Reddit,
new Plex media and today's calendar.

Each function returns a plain dict (or None when there is nothing to print) and
never raises for a missing source; paper.py hides empty sections. Every LLM
job has a deterministic fallback, so a busy GPU only costs polish.

Env: PAPER_TICKERS, REDDIT_FEED_URL, PLEX_URL, PLEX_TOKEN.
"""

import os
import re
import subprocess
import time
from datetime import date, datetime, timedelta, timezone

import extract
import llm

STOP = set("""a an the and or but of to in on for at by with from as is are was were be been has have
had it its this that these those after before over under into about amid says said say new more
than not no will would could can may might his her their they them he she we you who what when
where why how all also just up out off than then there here one two three first last year years
week day days time news live update updates report reports latest""".split())


def _recent(items, hours):
    cut = datetime.now(timezone.utc) - timedelta(hours=hours)
    return [it for it in items if it["published"] is None or it["published"] >= cut]


def _keywords(text):
    words = re.findall(r"[A-Za-z][A-Za-z'\-]+", text)
    return {w.lower().strip("'") for w in words if len(w) > 3 and w.lower() not in STOP}


def _mentions(text, names):
    t = text.lower()
    return any(n and n.lower() in t for n in names)


def _first_sentence(text, cap=240):
    text = " ".join((text or "").split())
    m = re.match(r"(.{30,}?[.!?])(\s|$)", text)
    s = m.group(1) if m else text
    return s if len(s) <= cap else s[: cap - 1].rsplit(" ", 1)[0] + "."


def _paras(text):
    return [p.strip() for p in re.split(r"\n\s*\n|\n", text or "") if p.strip()]


def _full_text(link):
    """Article body for a feed link (resolving Google News stubs), or None."""
    url = extract.resolve_gnews(link) if "news.google.com" in (link or "") else link
    return extract.article(url) if url else None


def _summarize(kind, title, text, sources, words="120 to 180"):
    """Qwen summary paragraphs + one-sentence glance, or None."""
    out = llm.chat_json(
        f"You write the {kind} page of a personal morning newspaper. Summarize the report you are "
        "given. Use only facts stated in it. Credit claims to who made them. Keep numbers exact.",
        f"Headline: {title}\nSources: {', '.join(sources)}\n\nReport:\n{text[:14000]}\n\n"
        f'Return {{"headline": "plain factual headline, under 90 characters", '
        f'"glance": "one sentence, under 30 words, for the front page", '
        f'"summary": ["paragraph", ...] ({words} words in 2 to 4 short paragraphs)}}',
        max_tokens=2500)
    if not out or not out.get("summary") or not out.get("glance"):
        return None
    summary = out["summary"] if isinstance(out["summary"], list) else _paras(out["summary"])
    return {"headline": out.get("headline") or title, "glance": out["glance"], "llm": True,
            "summary": [s for s in summary if isinstance(s, str) and s.strip()]}


# World ---------------------------------------------------------------------

WORLD_FEEDS = [
    ("BBC", lambda: extract.feed("https://feeds.bbci.co.uk/news/world/rss.xml", "BBC")),
    ("AP", lambda: extract.gnews("site:apnews.com when:1d", "AP")),
    ("Reuters", lambda: extract.gnews("site:reuters.com world when:1d", "Reuters")),
]


def world(avoid=()):
    """Story covered by the most of AP, Reuters and BBC in the last day."""
    items = []
    for outlet, get in WORLD_FEEDS:
        for rank, it in enumerate(_recent(get(), 30)[:40]):
            if _mentions(it["title"], avoid):
                continue
            it.update(outlet=outlet, rank=rank, kw=_keywords(it["title"] + " " + it["summary"][:160]))
            items.append(it)
    if not items:
        return None

    def similar(a, b):
        shared = a["kw"] & b["kw"]
        return len(shared) >= 2 and len(shared) / max(1, min(len(a["kw"]), len(b["kw"]))) >= 0.3

    best = None
    for it in items:
        group = [it] + [o for o in items if o is not it and o["outlet"] != it["outlet"] and similar(it, o)]
        outlets = {g["outlet"] for g in group}
        score = (len(outlets), -sum(min(g["rank"] for g in group if g["outlet"] == o) for o in outlets))
        if best is None or score > best[0]:
            best = (score, it, group)
    _, lead, group = best
    by_outlet = {}
    for g in sorted(group, key=lambda g: g["rank"]):
        by_outlet.setdefault(g["outlet"], g)
    sources = [{"name": o, "title": g["title"], "link": g["link"]} for o, g in by_outlet.items()]
    print(f"world: lead story carried by {len(by_outlet)} of 3 outlets", flush=True)

    # Full text: BBC links are direct; AP and Reuters pages block automated readers.
    full = None
    for g in by_outlet.values():
        full = _full_text(g["link"])
        if full:
            full["outlet"] = g["outlet"]
            break
    material = "\n".join(f"{s['name']}: {s['title']}" for s in sources)
    summaries = "\n".join(g["summary"] for g in by_outlet.values() if g["summary"])
    text = material + "\n\n" + summaries + ("\n\n" + "\n\n".join(full["paras"]) if full else "")
    s = _summarize("world news", lead["title"], text, list(by_outlet))
    if not s:
        base = by_outlet.get("BBC", lead)
        s = {"headline": base["title"], "glance": _first_sentence(base["summary"] or base["title"]),
             "summary": [base["summary"]] if base["summary"] else []}
    return {**s, "sources": sources, "full": full}


# Local ---------------------------------------------------------------------

CRIME = re.compile(r"\b(arrest|arrested|shot|shooting|killed|murder|stabb|police|sheriff|charged|"
                   r"inmate|jail|prison|crash|wreck|dies|died|death|obituar|homicide|robbery|"
                   r"sentenced|convicted|indicted|deputies|fatal)", re.I)
NOT_CIVIC = re.compile(r"\b(football|basketball|baseball|softball|soccer|volleyball|recruit|commit|"
                       r"odds|realtor|for sale|listing|sponsored|coupon|deal|deals|market forecast|"
                       r"concert|tour|musical|press release)\b", re.I)
CIVIC = re.compile(r"\b(council|commission|county|city|school|board|mayor|budget|tax|vote|election|"
                   r"zoning|development|road|water|hospital|library|park|plan|permit|housing|transit|"
                   r"storm|weather|power|utility|grant|ordinance|legislat)", re.I)


def local(loc, avoid=()):
    if not loc or not (loc.get("county") or loc.get("city")):
        return None
    state, county, city = loc.get("state", ""), loc.get("county", ""), loc.get("city", "")
    queries = []
    if county:
        queries.append(f'"{county}" "{state}" when:1d')
    if city:
        queries.append(f'"{city}" "{state}" when:1d')
        queries.append(f'"{city}" (council OR commission OR schools OR county) when:2d')
    seen, items = set(), []
    for q in queries:
        for it in _recent(extract.gnews(q, "Google News"), 36):
            key = it["title"].lower()[:70]
            if key in seen or _mentions(it["title"], avoid):
                continue
            seen.add(key)
            items.append(it)
    items = items[:45]
    if not items:
        return None
    place = ", ".join(x for x in (city, county, state) if x)
    listing = "\n".join(f"{i}. {it['title']} ({it['source']})" for i, it in enumerate(items))
    pick = llm.chat_json(
        "You are the local editor of a morning newspaper. From headlines found by a news search, "
        f"pick the single most significant civic story for a resident of {place}: local government, "
        "schools, infrastructure, development, public health, utilities, elections, or weather impacts. "
        "Skip crime and police blotter, crashes, obituaries, sports, recruiting, real estate listings, "
        "advertisements, press releases, and stories that are not about this place.",
        f"{listing}\n\nReturn {{\"index\": number, or -1 if none qualify, \"why\": \"short reason\"}}",
        max_tokens=1500)
    idx = pick.get("index") if isinstance(pick, dict) else None
    if not isinstance(idx, int) or not (-1 <= idx < len(items)):
        names = [n for n in (city, county.replace(" County", "")) if n]
        idx = next((i for i, it in enumerate(items) if CIVIC.search(it["title"])
                    and not CRIME.search(it["title"]) and not NOT_CIVIC.search(it["title"])
                    and _mentions(it["title"] + it["summary"], names)), -1)
    if idx < 0:
        print("local: no civic story today", flush=True)
        return None
    it = items[idx]
    full = _full_text(it["link"])
    text = "\n\n".join(full["paras"]) if full else (it["summary"] or it["title"])
    s = _summarize("local news", it["title"], text, [it["source"]], words="80 to 140") if full else None
    if not s:
        paras = full["paras"][:3] if full else ([it["summary"]] if it["summary"] else [])
        s = {"headline": it["title"], "glance": _first_sentence(paras[0] if paras else it["title"]),
             "summary": paras}
    link = full["url"] if full else it["link"]
    return {**s, "sources": [{"name": it["source"], "title": it["title"], "link": link}],
            "full": full, "place": place}


# Markets -------------------------------------------------------------------

MARKETS = [  # symbol, name, kind
    ("^GSPC", "S&P 500", "index"), ("^IXIC", "Nasdaq", "index"), ("^DJI", "Dow", "index"),
    ("^RUT", "Russell 2000", "index"), ("^TNX", "10-year yield", "yield"),
    ("CL=F", "Crude oil", "price"), ("GC=F", "Gold", "price"), ("BTC-USD", "Bitcoin", "price"),
    ("DX-Y.NYB", "Dollar index", "index"),
]
ADVICE = re.compile(r"\b(you should|investors should|consider (buying|selling)|buy now|good time to|"
                    r"we expect|is expected to (rise|fall)|likely to (rise|fall|climb|drop)|"
                    r"recommend|undervalued|overvalued|price target|time to (buy|sell))\b|"
                    r"\b(buy|sell)\b(?![-\w])", re.I)


def quote(symbol):
    """Last close, day and week change, 52-week range from Yahoo's chart endpoint."""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol.replace('^', '%5E')}"
           "?range=1y&interval=1d")
    try:
        data = extract.fetch_json(url, ua="Mozilla/5.0", timeout=12)["chart"]["result"][0]
    except Exception as e:
        print(f"markets: {symbol} failed: {type(e).__name__}", flush=True)
        return None
    q = data["indicators"]["quote"][0]
    rows = [(t, c, h, lo) for t, c, h, lo in zip(data["timestamp"], q["close"], q["high"], q["low"])
            if c is not None]
    if len(rows) < 3:
        return None
    meta = data["meta"]
    last_t, last = rows[-1][0], meta.get("regularMarketPrice") or rows[-1][1]
    prev = rows[-2][1]
    week = next((c for t, c, _, _ in reversed(rows) if t <= last_t - 7 * 86400 + 3600), rows[0][1])
    hi = max(h for _, _, h, _ in rows if h is not None)
    lo = min(lo for _, _, _, lo in rows if lo is not None)
    return {"symbol": symbol, "name": meta.get("shortName") or symbol, "last": last, "prev": prev,
            "day": (last - prev) / prev * 100, "week": (last - week) / week * 100,
            "day_abs": last - prev, "week_abs": last - week, "hi": max(hi, last), "lo": min(lo, last),
            "asof": datetime.fromtimestamp(meta.get("regularMarketTime") or last_t).date().isoformat()}


def markets(avoid=()):
    board = []
    for sym, name, kind in MARKETS:
        q = quote(sym)
        if q:
            board.append({**q, "name": name, "kind": kind})
        time.sleep(0.3)
    tickers = [t.strip().upper() for t in os.environ.get("PAPER_TICKERS", "").split(",") if t.strip()]
    watch = []
    for t in tickers[:25]:
        q = quote(t)
        if q:
            watch.append(q)
        time.sleep(0.3)
    if not board and not watch:
        return None

    heads = [it for it in _recent(extract.feed(
        "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en",
        "Google News"), 30) if not _mentions(it["title"], avoid)][:30]
    for it in heads:
        if it["source"] and it["title"].endswith(" - " + it["source"]):
            it["title"] = it["title"][: -len(it["source"]) - 3]
    moves = "\n".join(f"{b['name']}: {b['day']:+.2f}% on the day, {b['week']:+.2f}% on the week "
                      f"(close of {date.fromisoformat(b['asof']).strftime('%A, %B %-d')})" for b in board)
    listing = "\n".join(f"{i}. {it['title']} ({it['source']})" for i, it in enumerate(heads))
    rules = ("Hard rule: report only. Never give buy, sell or hold advice, never recommend anything, "
             "never predict or forecast prices or the economy, never say what investors should do.")
    out = llm.chat_json(
        "You write the markets page of a personal morning newspaper. " + rules,
        f"Market moves:\n{moves}\n\nBusiness headlines from the last day:\n{listing}\n\n"
        'Return {"why": "one paragraph of 50 to 90 words describing what moved and the reported '
        'reasons, using only headlines that plausibly explain the moves; do not repeat forecasts, '
        'warnings or opinions from headlines; say plainly when no clear reason was reported", '
        '"story_indexes": indexes of the three most significant business stories, best first '
        '(reported events only: not opinion, forecasts, stock tips, listicles or personal finance), '
        '"glance": "one sentence under 30 words about the day in markets for the front page; '
        'if you name a day, use the weekday from the market moves, never one from a headline"}',
        max_tokens=2000)
    out = out if isinstance(out, dict) else {}
    why, glance = out.get("why", ""), out.get("glance", "")
    if not why or ADVICE.search(why):
        why = ""
    if not glance or ADVICE.search(glance):
        spx = next((b for b in board if b["symbol"] == "^GSPC"), board[0] if board else None)
        glance = (f"The {spx['name']} {'rose' if spx['day'] >= 0 else 'fell'} {abs(spx['day']):.1f}% "
                  f"in the last session and is {'up' if spx['week'] >= 0 else 'down'} "
                  f"{abs(spx['week']):.1f}% over the week.") if spx else ""
    story = None
    ranked = [i for i in (out.get("story_indexes") or []) if isinstance(i, int) and 0 <= i < len(heads)]
    if heads:
        # First pick whose publisher serves the text; paywalled picks fall back to headline only.
        it, full = heads[ranked[0] if ranked else 0], None
        for i in ranked[:3] or [0]:
            full = _full_text(heads[i]["link"])
            if full:
                it = heads[i]
                break
        s = None
        if full:
            s = _summarize("business news", it["title"], "\n\n".join(full["paras"]), [it["source"]],
                           words="80 to 140")
            if s and any(ADVICE.search(p) for p in s["summary"]):
                s = None
        if not s:
            paras = full["paras"][:3] if full else []
            s = {"headline": it["title"], "glance": _first_sentence(paras[0]) if paras else it["title"],
                 "summary": paras}
        story = {**s, "sources": [{"name": it["source"], "title": it["title"],
                                   "link": full["url"] if full else it["link"]}], "full": full}
    return {"board": board, "watch": watch, "why": why, "glance": glance, "story": story}


# Science -------------------------------------------------------------------

SCIENCE_FEEDS = [("Quanta", "https://www.quantamagazine.org/feed/"),
                 ("ScienceDaily", "https://www.sciencedaily.com/rss/top/science.xml"),
                 ("Phys.org", "https://phys.org/rss-feed/")]


def science():
    items = []
    for name, url in SCIENCE_FEEDS:
        items += _recent(extract.feed(url, name), 60)[:15]
    if not items:
        return None
    listing = "\n".join(f"{i}. [{it['source']}] {it['title']}: {it['summary'][:220]}" for i, it in enumerate(items))
    pick = llm.chat_json(
        "You are the science editor of a morning newspaper. Pick one genuinely new research finding: "
        "a specific result from a new study or observation. Skip explainers, opinion, profiles, "
        "anniversaries, event recaps, product launches and press about funding.",
        f"{listing}\n\nReturn {{\"index\": number}}", max_tokens=1500)
    idx = pick.get("index") if isinstance(pick, dict) else None
    if not isinstance(idx, int) or not (0 <= idx < len(items)):
        idx = next((i for i, it in enumerate(items)
                    if re.search(r"\b(study|finds|found|discover|researchers|scientists)\b",
                                 it["title"] + it["summary"], re.I)), 0)
    it = items[idx]
    full = _full_text(it["link"])
    text = "\n\n".join(full["paras"]) if full else it["summary"]
    s = _summarize("science", it["title"], text, [it["source"]])
    if not s:
        s = {"headline": it["title"], "glance": _first_sentence(it["summary"] or it["title"]),
             "summary": full["paras"][:3] if full else [it["summary"]]}
    return {**s, "sources": [{"name": it["source"], "title": it["title"], "link": it["link"]}],
            "full": full, "image_hint": it.get("image", "")}


# Reddit --------------------------------------------------------------------

def reddit():
    url = os.environ.get("REDDIT_FEED_URL", "")
    if not url:
        return None
    items = _recent(extract.feed(url, "Reddit"), 36)[:40]
    if not items:
        return None
    for it in items:
        m = re.search(r"/r/([^/]+)/", it["link"])
        it["sub"] = m.group(1) if m else ""
    listing = "\n".join(f"{i}. r/{it['sub']}: {it['title']}" for i, it in enumerate(items))
    out = llm.chat_json(
        "Pick the five Reddit posts most worth reading over coffee from this personal feed: favor "
        "substance, news and genuinely useful or delightful posts over memes and rage bait.",
        f"{listing}\n\nReturn {{\"picks\": [{{\"index\": number, \"reason\": \"one line, under 20 words\"}}]}}",
        max_tokens=1500)
    picks = []
    for p in (out or {}).get("picks", []) if isinstance(out, dict) else []:
        if isinstance(p, dict) and isinstance(p.get("index"), int) and 0 <= p["index"] < len(items):
            if all(x["post"] is not items[p["index"]] for x in picks):
                picks.append({"post": items[p["index"]], "reason": p.get("reason", "")})
    if not picks:
        picks = [{"post": it, "reason": ""} for it in items[:5]]
    return {"posts": [{"title": p["post"]["title"], "sub": p["post"]["sub"], "link": p["post"]["link"],
                       "reason": p["reason"]} for p in picks[:5]]}


# New media (Plex) ----------------------------------------------------------

def new_media(hours=24):
    url, token = os.environ.get("PLEX_URL", "").rstrip("/"), os.environ.get("PLEX_TOKEN", "")
    if not (url and token):
        return None
    try:
        data = extract.fetch_json(f"{url}/library/recentlyAdded",
                                  headers={"X-Plex-Token": token, "Accept": "application/json"})
    except Exception as e:
        print(f"plex: failed: {type(e).__name__}", flush=True)
        return None
    cut = time.time() - hours * 3600
    movies, shows = [], []
    for m in data.get("MediaContainer", {}).get("Metadata", []):
        if int(m.get("addedAt", 0)) < cut:
            continue
        lib = m.get("librarySectionTitle", "")
        if m.get("type") == "movie":
            movies.append({"title": m.get("title", ""), "year": m.get("year"), "library": lib,
                           "summary": m.get("summary", "")})
        elif m.get("type") in ("season", "episode", "show"):
            show = m.get("parentTitle") if m.get("type") == "season" else (m.get("grandparentTitle") or m.get("title"))
            what = m.get("title", "") if m.get("type") == "season" else ""
            shows.append({"title": show or m.get("title", ""), "what": what, "library": lib,
                          "count": m.get("leafCount")})
    if not movies and not shows:
        return None
    return {"movies": movies, "shows": shows}


# Calendar ------------------------------------------------------------------

ICALBUDDY = os.environ.get("ICALBUDDY", "/opt/homebrew/bin/icalBuddy")
# Sports calendars repeat the sports section; override with PAPER_CAL_EXCLUDE.
CAL_EXCLUDE = os.environ.get("PAPER_CAL_EXCLUDE", "Lakers,Dodgers,F1")

# Kindle fonts have no emoji glyphs; subscribed sports calendars put them in titles.
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]+")


def strip_emoji(s):
    return re.sub(r"\s{2,}", " ", _EMOJI.sub("", s)).strip()


def calendar():
    """Today's Apple Calendar events via icalBuddy. [] when access isn't granted."""
    try:
        r = subprocess.run([ICALBUDDY, "-nc", "-nrd", "-ec", CAL_EXCLUDE, "-b", "* ", "-iep", "title,datetime,location",
                            "-po", "title,datetime,location", "-df", "", "-tf", "%-I:%M %p",
                            "eventsToday"], capture_output=True, text=True, timeout=20)
    except Exception as e:
        print(f"calendar: icalBuddy failed: {type(e).__name__}", flush=True)
        return []
    if r.returncode != 0 or "No calendars" in (r.stdout + r.stderr):
        print("calendar: no access (grant Calendars permission, see docs/architecture.md)", flush=True)
        return []
    events = []
    for line in r.stdout.splitlines():
        if line.startswith("* "):
            events.append({"title": strip_emoji(line[2:]), "time": "", "where": ""})
        elif events and line.strip():
            s = line.strip()
            if s.lower().startswith("location:"):
                events[-1]["where"] = s.split(":", 1)[1].strip()
            elif re.search(r"\d:\d\d", s):
                events[-1]["time"] = s.replace(" - ", " to ")
    return events
