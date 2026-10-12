#!/usr/bin/env python3
"""Kiran's Paper: a daily EPUB newspaper for KOReader on the Kindle.

Built once a morning by launchd (via paper_build.sh):
gathers every section, renders XHTML pages, and zips an EPUB3 to
out/paper/YYYY-MM-DD.epub plus latest.epub. brain_api.py serves the files and
a browser preview (serve() below). Any section with no data is left out, and a
failed source never fails the build.

Spoilers: games flagged as worth watching (sports.py) never show a score on
any normal page. Their scores live on score-N.xhtml pages that sit outside the
reading order (linear="no"), reached only from the "Show score" link.

Usage: paper.py build [--no-llm] [--date YYYY-MM-DD]
       paper.py build --demo [fixtures.json]   (no network, writes out/demo/)

The demo path builds an edition from a fixtures file (examples/demo-edition.json
by default) instead of the live sources. Everything in that file is made up; it
exists so the layout can be built, checked and screenshotted on any machine.
"""

import argparse
import html
import json
import os
import re
import shutil
import sys
import tempfile
import time
import traceback
import uuid
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import extract
import kindle
import llm
import location
import paper_sources as src
import sports

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out" / "paper"
DEMO_OUT = ROOT / "out" / "demo"
DEMO_FIXTURES = ROOT.parent / "examples" / "demo-edition.json"
NAME = os.environ.get("PAPER_NAME", "Kiran's Paper")
KEEP_DAYS = 7
IMAGE_BUDGET = 4_000_000  # bytes of images per edition, keeps the EPUB under ~5 MB
CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FAV = ' class="fav"'
NOT_LINEAR = ' linear="no"'

CSS = """
body { font-family: serif; line-height: 1.45; margin: 0 0.5em; color: #000; background: #fff; }
p { margin: 0 0 0.65em; }
a { color: #000; text-decoration: underline; }
h1 { font-size: 1.55em; line-height: 1.15; margin: 0.2em 0 0.35em; }
h2 { font-size: 1em; font-weight: bold; text-transform: uppercase; letter-spacing: 0.08em;
     border-bottom: 1px solid #000; margin: 1.2em 0 0.45em; padding-bottom: 0.1em; }
h3 { font-size: 1em; margin: 0.9em 0 0.2em; }
.mast { text-align: center; border-bottom: 4px double #000; padding-bottom: 0.25em; margin-bottom: 0.3em; }
.mast h1 { font-size: 2.3em; margin: 0.1em 0 0; letter-spacing: 0.02em; }
.dateline { text-align: center; font-size: 0.8em; text-transform: uppercase; letter-spacing: 0.08em; margin: 0.2em 0 0.5em; }
.links { text-align: center; font-size: 0.9em; margin: 0.3em 0 0.8em; }
.links a { margin: 0 0.35em; }
.kicker { font-size: 0.8em; text-transform: uppercase; letter-spacing: 0.1em; margin: 0.6em 0 0; }
.credit, .small { font-size: 0.8em; font-style: italic; }
.nav { font-size: 0.85em; margin: 0.4em 0; }
.box { border: 2px solid #000; padding: 0.2em 0.6em 0.4em; margin: 1em 0; }
.box h2 { border: none; margin-top: 0.3em; }
ul { margin: 0 0 0.6em; padding-left: 1.1em; }
li { margin: 0 0 0.35em; }
ul.plain { list-style: none; padding-left: 0; }
.wx { font-size: 1.05em; margin: 0.2em 0 0.4em; }
.wx b { font-size: 1.3em; }
table { border-collapse: collapse; width: 100%; font-size: 0.88em; margin: 0.3em 0 0.8em; }
th, td { padding: 0.15em 0.25em; border-bottom: 1px solid #777; text-align: left; vertical-align: top; }
th { border-bottom: 2px solid #000; font-weight: bold; }
td.n, th.n { text-align: right; }
table.hours td { text-align: center; border: none; }
table.bar { width: 100%; margin: 0.15em 0 0; border: 1px solid #000; }
table.bar td { padding: 0; height: 0.45em; border: none; }
table.bar td.f { border-right: 4px solid #000; }
img { max-width: 100%; height: auto; display: block; margin: 0.6em auto; }
.fav { font-weight: bold; }
.score { font-size: 1.6em; text-align: center; margin: 1.2em 0; }
.foot { font-size: 0.75em; font-style: italic; border-top: 1px solid #000; padding-top: 0.3em; margin-top: 1.5em; }
"""

PREVIEW_CSS = "body { max-width: 40em; margin: 0 auto; padding: 0 16px 2em; font-size: 18px; }"


def t(s):
    """Escaped, de-dashed text, safe inside XHTML."""
    return html.escape(CTRL.sub("", llm.dedash(str(s or ""))), quote=True)


def link(href, text):
    return f'<a href="{t(href)}">{text}</a>'


def nav_back(extra=""):
    return f'<p class="nav">{link("front.xhtml", "Front page")}{extra}</p>'


def xhtml(title, body):
    return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
            'lang="en" xml:lang="en">\n<head>\n<meta charset="utf-8"/>\n'
            f'<title>{t(title)}</title>\n<link rel="stylesheet" type="text/css" href="style.css"/>\n'
            f'</head>\n<body>\n{body}\n</body>\n</html>\n')


def safe(name, fn, *args):
    """Run one section builder; log and return None on any failure."""
    t0 = time.time()
    try:
        out = fn(*args)
        print(f"section {name}: {'ok' if out else 'empty'} ({time.time() - t0:.0f}s)", flush=True)
        return out
    except Exception:
        print(f"section {name}: FAILED\n{traceback.format_exc(limit=4)}", flush=True)
        return None


# Formatting ----------------------------------------------------------------

def fmt_num(v, kind="index"):
    if kind == "yield":
        return f"{v:.2f}%"
    if kind == "price":
        return f"${v:,.0f}" if v >= 1000 else f"${v:,.2f}"
    return f"{v:,.2f}"


def fmt_chg(pct, absv, kind):
    if kind == "yield":
        return f"{absv * 100:+.0f} bp"
    return f"{pct:+.2f}%"


def local_time(dt, tz):
    return dt.astimezone(tz).strftime("%a %b %-d, %-I:%M %p").replace(":00 ", " ")


def score_line(g):
    a, h = g["away"], g["home"]
    detail = g["detail"] if g["detail"] and g["detail"] not in ("Final", "FT") else ""
    return f"{a['short']} {a['score']}, {h['short']} {h['score']}" + (f" ({detail})" if detail else "")


def matchup(g):
    return f"{g['away']['short']} at {g['home']['short']}"


# Sections ------------------------------------------------------------------

def article_page(kicker, a, img=None):
    body = [nav_back(), f'<p class="kicker">{t(kicker)}</p>', f"<h1>{t(a['headline'])}</h1>"]
    names = [s["name"] for s in a.get("sources", [])]
    if a.get("llm"):
        body.append(f'<p class="credit">Summary of reporting by {t(", ".join(names))}, written by {t(llm.LABEL)}.</p>')
    elif names:
        body.append(f'<p class="credit">From {t(", ".join(names))}.</p>')
    if img:
        body.append(f'<img src="{img}" alt=""/>')
    body += [f"<p>{t(p)}</p>" for p in a.get("summary", [])]
    full = a.get("full")
    if full and full.get("paras") and a.get("llm"):
        body.append(f"<h2>Full report{', ' + t(full.get('outlet')) if full.get('outlet') else ''}</h2>")
        body += [f"<p>{t(p)}</p>" for p in full["paras"][:60]]
    if a.get("sources"):
        body.append("<h2>Sources</h2><ul>")
        body += [f"<li>{t(s['name'])}: {link(s['link'], t(s['title']))}</li>" for s in a["sources"]]
        body.append("</ul>")
    body.append(nav_back())
    return "\n".join(body)


def weather_block(wx):
    if not wx:
        return ""
    d = wx["daily"]
    word = kindle.WMO.get(d["weather_code"][0], ("", ""))[0]
    out = [f'<p class="wx"><b>{round(d["temperature_2m_max"][0])}°</b> / {round(d["temperature_2m_min"][0])}° '
           f'{t(word)}. Rain {d["precipitation_probability_max"][0]}%, wind {round(d["wind_speed_10m_max"][0])} mph. '
           f'Sunrise {kindle.clock(d["sunrise"][0])}, sunset {kindle.clock(d["sunset"][0])}.</p>']
    h = wx["hourly"]
    cells = []
    for hour in (8, 11, 14, 17, 20):
        key = f"{d['time'][0]}T{hour:02d}:00"
        if key in h["time"]:
            i = h["time"].index(key)
            p = h["precipitation_probability"][i]
            label = datetime(2000, 1, 1, hour).strftime("%-I %p").lower()
            cells.append(f"<td>{label}<br/><b>{round(h['temperature_2m'][i])}°</b>"
                         f"<br/>{t(kindle.WMO.get(h['weather_code'][i], ('', ''))[0])}"
                         f"{'<br/>' + str(p) + '%' if p >= 20 else ''}</td>")
    if cells:
        out.append(f'<table class="hours"><tr>{"".join(cells)}</tr></table>')
    return "\n".join(out)


def sports_page(sp, tz):
    hidden = (f' Scores of games flagged worth watching are hidden; see {link("watch.xhtml", "Worth watching")}.'
              if sp["flagged"] else "")
    body = [nav_back(), "<h1>Sports</h1>",
            f'<p class="small">Results from {sp["day"].strftime("%A, %B %-d")}.{hidden}</p>']
    if sp["today"]:
        body.append("<h2>Today</h2><ul>")
        body += [f"<li class=\"fav\">{t(x['league'])}: {t(x['text'])}, {t(local_time(x['when'], tz))}"
                 f"{' (' + t(x['note']) + ')' if x['note'] else ''}</li>" for x in sp["today"]]
        body.append("</ul>")
    for lg in sp["leagues"]:
        body.append(f'<h2 id="{lg["key"]}">{t(lg["name"])}{" " + t(lg["phase"]) if lg["phase"] else ""}</h2>')
        if lg["key"] == "nba" and sp.get("player"):
            pl = sp["player"]
            if pl["game"].get("flagged"):
                body.append(f'<p><b>{t(pl["name"])}</b> played; his line is on the '
                            f'{link("watch.xhtml", "score page")} to keep the game spoiler-free.</p>')
            else:
                body.append(f'<p><b>{t(pl["name"])}</b> ({t(pl["team"])}): {t(pl["line"])}.</p>')
        body.append('<ul class="plain">')
        for g in sorted(lg["games"], key=lambda g: (not g["fav"], not g.get("flagged"))):
            cls = ' class="fav"' if g["fav"] else ""
            if g.get("flagged"):
                body.append(f"<li{cls}>{t(matchup(g))}: score hidden, "
                            f"{link('watch.xhtml#' + g['anchor'], 'worth watching')}</li>")
            else:
                note = f" ({t(g['note'])})" if g["note"] else ""
                body.append(f"<li{cls}>{t(score_line(g))}{note}</li>")
        body.append("</ul>")
        for conf in lg["standings"]:
            body.append(f"<h3>{t(conf['name'])}</h3><table><tr><th>#</th><th>Team</th>"
                        '<th class="n">W</th><th class="n">L</th><th class="n">GB</th></tr>')
            for r in conf["rows"]:
                fav = any(sports.norm(r["full"]) == sports.norm(f) for f in sports.FAV_TEAMS)
                body.append(f'<tr{FAV if fav else ""}><td>{r["seed"] if r["seed"] < 99 else ""}</td>'
                            f'<td>{t(r["team"])}</td><td class="n">{t(r["w"])}</td><td class="n">{t(r["l"])}</td>'
                            f'<td class="n">{t(r["gb"])}</td></tr>')
            body.append("</table>")
    if sp.get("f1"):
        f = sp["f1"]
        body.append('<h2 id="f1">Formula 1</h2>')
        if f.get("podium"):
            body.append(f"<p>{t(f['race'])} podium:</p><ol>" + "".join(f"<li>{t(n)}</li>" for n in f["podium"]) + "</ol>")
        if f.get("next"):
            n = f["next"]
            when = "today" if n["days"] == 0 else ("tomorrow" if n["days"] == 1 else f"in {n['days']} days")
            body.append(f"<p>Next: {t(n['name'])}, {t(n['where'])}, {t(local_time(n['when'], tz))} ({when}).</p>")
    if sp.get("tennis"):
        body.append('<h2 id="tennis">Tennis</h2><ul class="plain">')
        body += [f"<li>{t(r['event'])}, {t(r['draw'])}, {t(r['round'])}: {t(r['text'])}</li>" for r in sp["tennis"]]
        body.append("</ul>")
    sc = sp.get("soccer")
    if sc:
        body.append(f'<h2 id="soccer">{t(sc["team"])}</h2>')
        for g in sc["results"]:
            if g.get("flagged"):
                body.append(f"<p class=\"fav\">{t(g['league'])}: {t(matchup(g))}, score hidden, "
                            f"{link('watch.xhtml#' + g['anchor'], 'worth watching')}</p>")
            else:
                body.append(f"<p class=\"fav\">{t(g['league'])}: {t(g['away']['name'])} {g['away']['score']}, "
                            f"{t(g['home']['name'])} {g['home']['score']}</p>")
        if sc["next"]:
            body.append(f"<p>Next: {t(sc['next']['name'])} ({t(sc['next']['comp'])}), "
                        f"{t(local_time(sc['next']['when'], tz))}.</p>")
        if sc["table"]:
            rows = sc["table"][:5]
            mine = next((r for r in sc["table"] if sports.norm(r["team"]) == sports.norm(sc["team"])), None)
            if mine and mine not in rows:
                rows.append(mine)
            body.append('<table><tr><th>#</th><th>La Liga</th><th class="n">P</th><th class="n">Pts</th></tr>')
            body += [f'<tr{FAV if r is mine else ""}><td>{r["rank"]}</td><td>{t(r["team"])}</td>'
                     f'<td class="n">{r["gp"]}</td><td class="n">{t(r["pts"])}</td></tr>' for r in rows]
            body.append("</table>")
    body.append(nav_back())
    return "\n".join(body)


def watch_pages(sp):
    """Worth watching page and one hidden score page per flagged game."""
    body = [nav_back(), "<h1>Worth watching</h1>",
            '<p class="small">Picked for drama, not results. Scores are one page turn away, only if you want them.</p>']
    scores = []
    for i, g in enumerate(sp["flagged"], 1):
        fname = f"score-{i}.xhtml"
        body.append(f'<h3 id="{g["anchor"]}">{t(g["league"])}: {t(g["away"]["name"])} at {t(g["home"]["name"])}</h3>')
        if g["note"]:
            body.append(f'<p class="small">{t(g["note"])}</p>')
        body.append(f"<p>{t(g['watch_line'])}</p>")
        body.append(f'<p class="nav">{link(fname, "Show score")}</p>')
        extra = ""
        pl = sp.get("player")
        if pl and pl["game"] is g:
            extra = f'<p><b>{t(pl["name"])}</b>: {t(pl["line"])}.</p>'
        sbody = (f'<p class="kicker">{t(g["league"])}</p><h1>{t(matchup(g))}</h1>'
                 f'<p class="score">{t(score_line(g))}</p>{extra}'
                 f'<p class="nav">{link("watch.xhtml#" + g["anchor"], "Back to Worth watching")} · '
                 f'{link("front.xhtml", "Front page")}</p>')
        scores.append({"id": f"score{i}", "file": fname, "title": f"Score: {matchup(g)}", "body": sbody,
                       "linear": False, "toc": False})
    body.append(nav_back())
    return "\n".join(body), scores


def range_bar(q):
    span = q["hi"] - q["lo"]
    pct = 50 if span <= 0 else max(1, min(99, round((q["last"] - q["lo"]) / span * 100)))
    return (f'<table class="bar"><tr><td class="f" style="width:{pct}%"></td><td></td></tr></table>'
            f'<span class="small">{fmt_num(q["lo"])} to {fmt_num(q["hi"])}</span>')


def markets_page(m, img=None):
    body = [nav_back(), "<h1>Markets</h1>"]
    if m["board"]:
        spx = next((b for b in m["board"] if b["kind"] == "index"), m["board"][0])
        asof = date.fromisoformat(spx["asof"]).strftime("%A, %B %-d")
        body.append(f'<p class="small">Stocks as of the close on {asof}; bitcoin and futures as of build time. '
                    "Week is the change over seven days.</p>")
        body.append('<table><tr><th></th><th class="n">Last</th><th class="n">Day</th><th class="n">Week</th></tr>')
        for b in m["board"]:
            body.append(f'<tr><td>{t(b["name"])}</td><td class="n">{fmt_num(b["last"], b["kind"])}</td>'
                        f'<td class="n">{fmt_chg(b["day"], b["day_abs"], b["kind"])}</td>'
                        f'<td class="n">{fmt_chg(b["week"], b["week_abs"], b["kind"])}</td></tr>')
        body.append("</table>")
    if m["why"]:
        body.append(f"<h2>Why it moved</h2><p>{t(m['why'])}</p>")
    if m["watch"]:
        body.append('<h2>Watchlist</h2><table><tr><th></th><th class="n">Last</th><th class="n">Day</th>'
                    '<th class="n">Week</th><th>52 weeks</th></tr>')
        for q in m["watch"]:
            body.append(f'<tr><td>{t(q["symbol"])}</td><td class="n">{fmt_num(q["last"])}</td>'
                        f'<td class="n">{q["day"]:+.2f}%</td><td class="n">{q["week"]:+.2f}%</td>'
                        f"<td>{range_bar(q)}</td></tr>")
        body.append("</table>")
    st = m.get("story")
    if st:
        body.append(f"<h2>Business</h2><h3>{t(st['headline'])}</h3>")
        names = ", ".join(s["name"] for s in st["sources"])
        body.append(f'<p class="credit">{"Summary of reporting by " + t(names) + ", written by " + t(llm.LABEL) + "." if st.get("llm") else "From " + t(names) + "."}</p>')
        if img:
            body.append(f'<img src="{img}" alt=""/>')
        body += [f"<p>{t(p)}</p>" for p in st["summary"]]
        body += [f'<p class="small">{t(s["name"])}: {link(s["link"], t(s["title"]))}</p>' for s in st["sources"]]
    body.append('<p class="foot">Reported for information only. Nothing here is investment advice or a '
                "prediction. Prices from Yahoo Finance.</p>")
    body.append(nav_back())
    return "\n".join(body)


def reddit_page(r):
    body = [nav_back(), "<h1>Reddit</h1><ul>"]
    for p in r["posts"]:
        body.append(f"<li>{link(p['link'], t(p['title']))}<br/><span class=\"small\">r/{t(p['sub'])}"
                    f"{'. ' + t(p['reason']) if p['reason'] else ''}</span></li>")
    body.append("</ul>" + nav_back())
    return "\n".join(body)


def media_page(m):
    body = [nav_back(), "<h1>New in the library</h1>", '<p class="small">Added to Plex in the last day.</p>']
    if m["movies"]:
        body.append("<h2>Movies</h2><ul>")
        for x in m["movies"]:
            yr = f" ({x['year']})" if x.get("year") else ""
            summ = f"<br/><span class=\"small\">{t(src._first_sentence(x['summary'], 200))}</span>" if x["summary"] else ""
            body.append(f"<li><b>{t(x['title'])}</b>{yr}{summ}</li>")
        body.append("</ul>")
    if m["shows"]:
        body.append("<h2>Shows</h2><ul>")
        body += [f"<li><b>{t(x['title'])}</b>{', ' + t(x['what']) if x['what'] else ''}</li>" for x in m["shows"]]
        body.append("</ul>")
    body.append(nav_back())
    return "\n".join(body)


def front_page(ed):
    d, loc, sp = ed["date"], ed["loc"], ed["sports"]
    body = [f'<div class="mast"><h1>{t(NAME)}</h1></div>',
            f'<p class="dateline">{d.strftime("%A, %B %-d, %Y")}'
            f'{" · " + t(location.label(loc, long=True)) if loc else ""}</p>']
    quick = [(f, n) for f, n, ok in (("sports.xhtml", "Sports", sp and (sp["leagues"] or sp["f1"] or sp["soccer"])),
                                     ("markets.xhtml", "Markets", ed["markets"]),
                                     ("reddit.xhtml", "Reddit", ed["reddit"]),
                                     ("media.xhtml", "New media", ed["media"])) if ok]
    if quick:
        body.append('<p class="links">' + " · ".join(link(f, n) for f, n in quick) + "</p>")
    wx = weather_block(ed["weather"])
    if wx:
        body.append(wx)
    if ed["calendar"]:
        body.append("<h2>Today</h2><ul>")
        for e in ed["calendar"]:
            when = f"{t(e['time'])}: " if e["time"] else "All day: "
            body.append(f"<li>{when}<b>{t(e['title'])}</b>{', ' + t(e['where']) if e['where'] else ''}</li>")
        body.append("</ul>")
    glance = [(n, f, a["glance"]) for n, f, a in (("World", "world.xhtml", ed["world"]),
                                                   ("Local", "local.xhtml", ed["local"]),
                                                   ("Markets", "markets.xhtml", ed["markets"]),
                                                   ("Science", "science.xhtml", ed["science"]))
              if a and a.get("glance")]
    if glance:
        body.append("<h2>At a glance</h2><ul class=\"plain\">")
        body += [f"<li><b>{n}.</b> {link(f, t(g))}</li>" for n, f, g in glance]
        body.append("</ul>")
    if sp and sp["flagged"]:
        body.append('<div class="box"><h2>Worth watching</h2><ul class="plain">')
        body += [f"<li><b>{t(g['league'])}: {t(matchup(g))}.</b> {link('watch.xhtml#' + g['anchor'], t(g['watch_line']))}</li>"
                 for g in sp["flagged"]]
        body.append("</ul></div>")
    if sp and (sp["leagues"] or sp["soccer"] or sp["f1"]):
        body.append("<h2>Scores</h2><ul class=\"plain\">")
        for lg in sp["leagues"]:
            games = sorted(lg["games"], key=lambda g: (not g["fav"], not g.get("flagged")))[:5]
            bits = [(t(matchup(g)) + " (hidden)") if g.get("flagged") else t(score_line(g)) for g in games]
            more = f" and {len(lg['games']) - 5} more" if len(lg["games"]) > 5 else ""
            body.append(f"<li><b>{t(lg['name'])}{' ' + t(lg['phase']) if lg['phase'] else ''}:</b> "
                        f"{'; '.join(bits)}{more}.</li>")
        if sp.get("player") and not sp["player"]["game"].get("flagged"):
            body.append(f"<li><b>{t(sp['player']['name'])}:</b> {t(sp['player']['line'])}.</li>")
        sc = sp.get("soccer")
        if sc:
            for g in sc["results"]:
                body.append(f"<li><b>{t(g['league'])}:</b> "
                            f"{t(matchup(g)) + ' (hidden)' if g.get('flagged') else t(score_line(g))}.</li>")
        f1 = sp.get("f1")
        if f1 and f1.get("podium"):
            body.append(f"<li><b>F1:</b> {t(f1['race'])}, 1. {t(f1['podium'][0])}.</li>")
        body.append(f"</ul><p class=\"nav\">{link('sports.xhtml', 'All sports')}</p>")
    m = ed["media"]
    if m:
        names = [x["title"] for x in m["movies"]] + [f"{x['title']} {x['what']}".strip() for x in m["shows"]]
        more = f" and {len(names) - 5} more" if len(names) > 5 else ""
        body.append(f"<h2>New in the library</h2><p>{link('media.xhtml', t('; '.join(names[:5])))}{more}.</p>")
    stats = llm.stats()
    printed = ed.get("printed") or datetime.now()
    body.append(f'<p class="foot">Printed {printed.strftime("%-I:%M %p")} on the Mac mini. '
                f'Summaries by {t(llm.LABEL)} ({stats["calls"] - stats["fails"]} of {stats["calls"]} model calls succeeded). '
                "Free sources only; full text appears only where the publisher serves it."
                f'{" " + t(ed["note"]) if ed.get("note") else ""}</p>')
    return "\n".join(body)


# Build ---------------------------------------------------------------------

def gather(day):
    loc = safe("location", location.current, 0)
    ed = {"date": day, "loc": loc}
    ed["weather"] = safe("weather", kindle.weather, loc) if loc else None
    ed["calendar"] = safe("calendar", src.calendar) or []
    ed["sports"] = safe("sports", sports.build, day - timedelta(days=1))
    avoid = (ed["sports"] or {}).get("avoid", [])
    ed["world"] = safe("world", src.world, avoid)
    ed["local"] = safe("local", src.local, loc, avoid)
    ed["markets"] = safe("markets", src.markets, avoid)
    ed["science"] = safe("science", src.science)
    ed["reddit"] = safe("reddit", src.reddit)
    ed["media"] = safe("media", src.new_media)
    return ed


def fetch_images(ed, tmp):
    """At most one grayscale image per article, within the edition's image budget."""
    imgs, used = {}, 0
    wanted = [("world", (ed["world"] or {}).get("full")), ("local", (ed["local"] or {}).get("full")),
              ("science", (ed["science"] or {}).get("full")),
              ("business", ((ed["markets"] or {}).get("story") or {}).get("full"))]
    for key, full in wanted:
        url = (full or {}).get("image")
        if not url:
            continue
        out = Path(tmp) / f"{key}.jpg"
        if extract.gray_jpeg(url, out) and used + out.stat().st_size <= IMAGE_BUDGET:
            used += out.stat().st_size
            imgs[key] = f"images/{key}.jpg"
        elif out.exists():
            out.unlink()
    return imgs


def pages_for(ed, imgs):
    tz = ZoneInfo((ed["weather"] or {}).get("timezone") or "America/New_York")
    sp = ed["sports"]
    if sp:
        for i, g in enumerate(sp["flagged"], 1):
            g["anchor"] = f"g{i}"
    pages = [{"id": "front", "file": "front.xhtml", "title": "Front page", "body": front_page(ed)}]
    for key, kicker in (("world", "World"), ("local", "Local")):
        if ed[key]:
            k = kicker + (f": {ed['local']['place']}" if key == "local" else "")
            pages.append({"id": key, "file": f"{key}.xhtml", "title": kicker,
                          "body": article_page(k, ed[key], imgs.get(key))})
    if ed["markets"]:
        pages.append({"id": "markets", "file": "markets.xhtml", "title": "Markets",
                      "body": markets_page(ed["markets"], imgs.get("business"))})
    if ed["science"]:
        pages.append({"id": "science", "file": "science.xhtml", "title": "Science",
                      "body": article_page("Science", ed["science"], imgs.get("science"))})
    scores = []
    if sp and (sp["leagues"] or sp["f1"] or sp["soccer"] or sp["tennis"]):
        pages.append({"id": "sports", "file": "sports.xhtml", "title": "Sports", "body": sports_page(sp, tz)})
    if sp and sp["flagged"]:
        wbody, scores = watch_pages(sp)
        pages.append({"id": "watch", "file": "watch.xhtml", "title": "Worth watching", "body": wbody})
    if ed["reddit"]:
        pages.append({"id": "reddit", "file": "reddit.xhtml", "title": "Reddit", "body": reddit_page(ed["reddit"])})
    if ed["media"]:
        pages.append({"id": "media", "file": "media.xhtml", "title": "New in the library", "body": media_page(ed["media"])})
    for p in pages + scores:
        p.setdefault("linear", True)
        p.setdefault("toc", True)
        p["xhtml"] = xhtml(p["title"], p["body"])
        try:
            ET.fromstring(p["xhtml"].encode())
        except ET.ParseError as e:
            print(f"render: {p['file']} is not valid XML ({e}); replaced with a stub", flush=True)
            p["xhtml"] = xhtml(p["title"], f"{nav_back()}<h1>{t(p['title'])}</h1><p>This page failed to render.</p>")
    return pages + scores


def write_epub(ed, pages, img_dir, path):
    d = ed["date"]
    title = f"{NAME}, {d.strftime('%A, %B %-d, %Y')}"
    uid = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, 'kirans-paper/' + d.isoformat())}"
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    images = sorted(Path(img_dir).glob("*.jpg"))
    toc = [p for p in pages if p["toc"]]
    nav = xhtml("Contents", '<nav epub:type="toc" id="toc"><h1>Contents</h1><ol>' +
                "".join(f'<li><a href="{p["file"]}">{t(p["title"])}</a></li>' for p in toc) +
                '</ol></nav>\n<nav epub:type="landmarks" hidden="hidden"><ol>'
                '<li><a epub:type="bodymatter" href="front.xhtml">Front page</a></li></ol></nav>')
    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
                '<item id="css" href="style.css" media-type="text/css"/>']
    manifest += [f'<item id="{p["id"]}" href="{p["file"]}" media-type="application/xhtml+xml"/>' for p in pages]
    manifest += [f'<item id="img-{im.stem}" href="images/{im.name}" media-type="image/jpeg"/>' for im in images]
    spine = "".join(f'<itemref idref="{p["id"]}"{"" if p["linear"] else NOT_LINEAR}/>' for p in pages)
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid" xml:lang="en">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="uid">{uid}</dc:identifier>
<dc:title>{t(title)}</dc:title>
<dc:creator>brain-api</dc:creator>
<dc:language>en</dc:language>
<dc:date>{d.isoformat()}</dc:date>
<meta property="dcterms:modified">{modified}</meta>
</metadata>
<manifest>
{chr(10).join(manifest)}
</manifest>
<spine toc="ncx">{spine}</spine>
</package>
"""
    ncx = ('<?xml version="1.0" encoding="utf-8"?>\n<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
           f'<head><meta name="dtb:uid" content="{uid}"/></head><docTitle><text>{t(title)}</text></docTitle><navMap>' +
           "".join(f'<navPoint id="np{i}" playOrder="{i}"><navLabel><text>{t(p["title"])}</text></navLabel>'
                   f'<content src="{p["file"]}"/></navPoint>' for i, p in enumerate(toc, 1)) + "</navMap></ncx>")
    container = ('<?xml version="1.0" encoding="utf-8"?>\n<container version="1.0" '
                 'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                 '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                 "</rootfiles></container>")
    for name, doc in (("content.opf", opf), ("nav.xhtml", nav), ("toc.ncx", ncx)):
        ET.fromstring(doc.encode())  # raises on a bug in this file, not in source data
    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", container, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/nav.xhtml", nav, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/toc.ncx", ncx, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/style.css", CSS, compress_type=zipfile.ZIP_DEFLATED)
        for p in pages:
            z.writestr(f"OEBPS/{p['file']}", p["xhtml"], compress_type=zipfile.ZIP_DEFLATED)
        for im in images:
            z.write(im, f"OEBPS/images/{im.name}", compress_type=zipfile.ZIP_STORED)
    tmp.replace(path)


def prune(today):
    cut = today - timedelta(days=KEEP_DAYS)
    for f in OUT.glob("*.epub"):
        if DATE_RE.match(f.stem) and date.fromisoformat(f.stem) < cut:
            f.unlink()
            print(f"prune: removed {f.name}", flush=True)


def demo_edition(path):
    """An edition dict from a fixtures file, shaped like gather()'s output. No network."""
    raw = json.loads(Path(path).read_text())
    ed = {k: raw.get(k) for k in ("loc", "weather", "world", "local", "markets", "science",
                                   "reddit", "media", "note")}
    ed["date"] = date.fromisoformat(raw["date"])
    ed["calendar"] = raw.get("calendar") or []
    if raw.get("printed"):
        ed["printed"] = datetime.fromisoformat(raw["printed"])
    sp = raw.get("sports")
    if sp:
        when = datetime.fromisoformat
        sp["day"] = date.fromisoformat(sp["day"])
        for x in sp.get("today", []):
            x["when"] = when(x["when"])
        if (sp.get("f1") or {}).get("next"):
            sp["f1"]["next"]["when"] = when(sp["f1"]["next"]["when"])
        sc = sp.get("soccer")
        if sc and sc.get("next"):
            sc["next"]["when"] = when(sc["next"]["when"])
        games = [g for lg in sp.get("leagues", []) for g in lg["games"]] + ((sc or {}).get("results") or [])
        for g in games:
            g.setdefault("note", "")
            g.setdefault("detail", "Final")
            g.setdefault("fav", False)
        # The same game objects, as in a live build, so "flagged" hides them everywhere.
        sp["flagged"] = [g for g in games if g.get("flagged")]
        pl = sp.get("player")
        if pl:
            pl["game"] = next(g for g in games if g["id"] == pl.pop("game_id"))
        for k, v in (("leagues", []), ("tennis", []), ("today", []), ("f1", None), ("soccer", None)):
            sp.setdefault(k, v)
    ed["sports"] = sp
    return ed, raw.get("llm_stats") or {}


def build_demo(path=None):
    path = Path(path or DEMO_FIXTURES)
    ed, stats = demo_edition(path)
    llm.start(disabled=True)
    llm._state.update(calls=stats.get("calls", 0), fails=stats.get("fails", 0))
    DEMO_OUT.mkdir(parents=True, exist_ok=True)
    out = DEMO_OUT / f"{ed['date'].isoformat()}.epub"
    with tempfile.TemporaryDirectory() as tmp:
        pages = pages_for(ed, {})
        write_epub(ed, pages, tmp, out)
    print(f"paper: demo edition from {path.name} -> {out} ({len(pages)} pages)", flush=True)
    return out


def build(day=None, use_llm=True):
    t0 = time.time()
    day = day or date.today()
    OUT.mkdir(parents=True, exist_ok=True)
    llm.start(disabled=not use_llm)
    print(f"paper: building {day.isoformat()}", flush=True)
    ed = gather(day)
    with tempfile.TemporaryDirectory() as tmp:
        imgs = fetch_images(ed, tmp)
        pages = pages_for(ed, imgs)
        path = OUT / f"{day.isoformat()}.epub"
        write_epub(ed, pages, tmp, path)
    shutil.copyfile(path, OUT / "latest.tmp")
    (OUT / "latest.tmp").replace(OUT / "latest.epub")
    prune(day)
    s = llm.stats()
    print(f"paper: wrote {path.name}, {path.stat().st_size / 1e6:.2f} MB, {len(pages)} pages, "
          f"{len(imgs)} images, llm {s['calls'] - s['fails']}/{s['calls']} ok in {s['spent']:.0f}s, "
          f"total {time.time() - t0:.0f}s", flush=True)
    return path


# Serving (called from brain_api.py) ----------------------------------------

def editions():
    out = []
    for f in sorted(OUT.glob("*.epub"), reverse=True):
        if DATE_RE.match(f.stem):
            st = f.stat()
            out.append({"date": f.stem, "file": f.name, "path": f"/paper/{f.name}", "bytes": st.st_size,
                        "built": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")})
    tz = location.timezone()
    return {"latest": out[0]["date"] if out else None, "editions": out,
            "tz": tz["name"] if tz else None, "tz_posix": tz["posix"] if tz else None}


def _preview(doc, key):
    """An EPUB page made browsable: links routed through /paper/view with the key kept."""
    k = html.escape(quote(key, safe=""))

    def fix(m):
        attr, target, frag = m.group(1), m.group(2), m.group(3) or ""
        return f'{attr}="/paper/view/{target}?key={k}{frag}"'
    doc = re.sub(r'^<\?xml[^>]*\?>\s*', "", doc)
    doc = re.sub(r'(href|src)="((?:images/)?[a-z0-9-]+\.(?:xhtml|css|jpg))(#[^"]*)?"', fix, doc)
    return doc.replace("</head>", '<meta name="viewport" content="width=device-width, initial-scale=1"/>\n'
                       f"<style>{PREVIEW_CSS}</style>\n</head>", 1)


def serve(route, key):
    """(status, body, content type, extra headers) for a /paper route."""
    latest = OUT / "latest.epub"
    if route in ("/paper", "/paper/"):
        route = "/paper/view/front.xhtml"
    if route == "/paper/editions.json":
        return 200, json.dumps(editions()), "application/json", {}
    if route == "/paper/tzfile":
        # The Kindle has no zoneinfo; its /etc/localtime points at a file sync.sh writes from this.
        tz = location.timezone()
        if not tz:
            return 503, b"timezone unknown", "text/plain", {}
        return 200, Path(f"/usr/share/zoneinfo/{tz['name']}").read_bytes(), "application/octet-stream", {}
    m = re.fullmatch(r"/paper/(latest|\d{4}-\d{2}-\d{2})\.epub", route)
    if m:
        f = OUT / f"{m.group(1)}.epub"
        if not f.exists():
            return 404, b"no such edition", "text/plain", {}
        name = f"kirans-paper-{editions()['latest'] if m.group(1) == 'latest' else m.group(1)}.epub"
        return 200, f.read_bytes(), "application/epub+zip", {"Content-Disposition": f'attachment; filename="{name}"'}
    m = re.fullmatch(r"/paper/view/((?:images/)?[a-z0-9-]+\.(xhtml|css|jpg))", route)
    if not m:
        return 404, b"not found", "text/plain", {}
    if not latest.exists():
        return 404, b"no edition built yet", "text/plain", {}
    with zipfile.ZipFile(latest) as z:
        try:
            data = z.read(f"OEBPS/{m.group(1)}")
        except KeyError:
            return 404, b"not in this edition", "text/plain", {}
    if m.group(2) == "xhtml":
        return 200, _preview(data.decode(), key).encode(), "text/html; charset=utf-8", {}
    return 200, data, "text/css" if m.group(2) == "css" else "image/jpeg", {}


def main():
    ap = argparse.ArgumentParser(description=f"Build {NAME}")
    ap.add_argument("cmd", choices=["build"])
    ap.add_argument("--no-llm", action="store_true", help="skip the model, use fallbacks only")
    ap.add_argument("--date", help="edition date, YYYY-MM-DD (default today)")
    ap.add_argument("--demo", nargs="?", const=str(DEMO_FIXTURES), metavar="FIXTURES",
                    help="build a made-up edition from a fixtures file, no network")
    args = ap.parse_args()
    if args.demo:
        build_demo(args.demo)
        return
    build(date.fromisoformat(args.date) if args.date else None, use_llm=not args.no_llm)


if __name__ == "__main__":
    sys.exit(main())
