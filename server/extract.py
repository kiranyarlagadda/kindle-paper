"""Fetching helpers for the paper: HTTP, RSS/Atom feeds, article text, images.

article() is a readability-lite extractor on html.parser: it scores block
containers by the paragraph text inside them (parent full weight, grandparent
half) and keeps the paragraphs under the best one. Paywalled or bot-blocked
pages return None and the caller falls back to the feed summary; nothing here
tries to get around a paywall.

Google News RSS links are redirect stubs; resolve_gnews() asks Google for the
publisher URL the same way its own web page does.
"""

import gzip
import json
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")
MAX_BYTES = 6 * 1024 * 1024


def fetch(url, timeout=15, ua=UA, data=None, headers=None):
    h = {"User-Agent": ua, "Accept-Encoding": "gzip", "Accept-Language": "en-US,en;q=0.8"}
    h.update(headers or {})
    with urlopen(Request(url, data=data, headers=h), timeout=timeout) as r:
        body = r.read(MAX_BYTES)
        if r.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        return body


def fetch_json(url, timeout=15, ua=UA, headers=None):
    return json.loads(fetch(url, timeout=timeout, ua=ua, headers=headers))


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def strip_html(s):
    if not s or "<" not in s:
        return " ".join(unescape(s or "").split())
    p = _Text()
    p.feed(s)
    return " ".join(" ".join(p.parts).split())


def _date(s):
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def parse_feed(body, source=""):
    """RSS 2.0 or Atom -> [{title, link, summary, published, source}] in feed order."""
    root = ET.fromstring(body)
    items = []
    for el in root.iter():
        name = _local(el.tag)
        if name not in ("item", "entry"):
            continue
        f = {"title": "", "link": "", "summary": "", "published": None, "source": source, "image": ""}
        for c in el:
            n = _local(c.tag)
            if n == "title":
                f["title"] = strip_html(c.text or "")
            elif n == "link" and not f["link"] and c.get("rel", "alternate") == "alternate":
                f["link"] = (c.get("href") or c.text or "").strip()
            elif n in ("thumbnail", "content") and c.get("url"):
                f["image"] = f["image"] or c.get("url")
            elif n in ("description", "summary", "content", "encoded") and not f["summary"]:
                f["summary"] = strip_html(c.text or "")
            elif n in ("pubDate", "published", "updated", "date") and not f["published"]:
                f["published"] = _date(c.text)
            elif n == "source" and c.text:
                f["source"] = c.text.strip()
            elif n == "enclosure" and (c.get("type") or "").startswith("image") and not f["image"]:
                f["image"] = c.get("url", "")
        if f["title"]:
            items.append(f)
    return items


def feed(url, source="", timeout=15):
    """Fetched and parsed feed, or [] on any failure (logged)."""
    try:
        return parse_feed(fetch(url, timeout=timeout), source)
    except Exception as e:
        print(f"feed: {source or url[:60]} failed: {type(e).__name__}", flush=True)
        return []


def gnews(query, source=""):
    """Google News RSS search. Titles lose their ' - Publisher' suffix."""
    url = "https://news.google.com/rss/search?" + urlencode(
        {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    items = feed(url, source)
    for it in items:
        if it["source"] and it["title"].endswith(" - " + it["source"]):
            it["title"] = it["title"][: -len(it["source"]) - 3].strip()
        if it["summary"].startswith(it["title"]):  # GN descriptions just repeat the headline
            it["summary"] = ""
    return items


def resolve_gnews(url):
    """Publisher URL behind a news.google.com/rss/articles link, or None."""
    m = re.search(r"/articles/([^?/]+)", url or "")
    if not m or "news.google.com" not in url:
        return url
    aid = m.group(1)
    try:
        page = fetch(f"https://news.google.com/rss/articles/{aid}", timeout=10).decode("utf-8", "replace")
        sg = re.search(r'data-n-a-sg="([^"]+)"', page).group(1)
        ts = re.search(r'data-n-a-ts="([^"]+)"', page).group(1)
        inner = ["garturlreq", [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None,
                                 None, None, None, 0, 1], "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0,
                                None, 0], aid, int(ts), sg]
        freq = json.dumps([[["Fbv4je", json.dumps(inner), None, "generic"]]])
        body = fetch("https://news.google.com/_/DotsSplashUi/data/batchexecute",
                     data=urlencode({"f.req": freq}).encode(), timeout=10,
                     headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
        found = re.search(r'garturlres\\",\\"(.*?)\\"', body.decode("utf-8", "replace"))
        return found.group(1).encode().decode("unicode_escape") if found else None
    except Exception as e:
        print(f"gnews: resolve failed: {type(e).__name__}", flush=True)
        return None


SKIP_TAGS = {"script", "style", "noscript", "nav", "header", "footer", "aside", "form", "figcaption",
             "button", "svg", "select", "template", "iframe"}
BLOCKS = {"div", "article", "section", "main", "body", "td", "blockquote"}
VOID = {"br", "img", "meta", "link", "input", "hr", "source", "wbr", "area", "base", "col", "embed",
        "param", "track"}
JUNK = re.compile(r"(cookie|newsletter|sign up|subscribe|all rights reserved|copyright|follow us|"
                  r"advertisement|click here|share this|related stories|read more|download the app)", re.I)


class _Article(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []        # (tag, node_id, hidden) of open elements
        self.next_id = 0
        self.skip = 0
        self.para = None       # [chain, parts] while inside <p>
        self.paras = []        # (chain, text)
        self.meta = {}
        self.title = []
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta":
            k = (a.get("property") or a.get("name") or "").lower()
            if k in ("og:image", "twitter:image", "og:title", "description", "og:description") and a.get("content"):
                self.meta.setdefault(k, a["content"])
            return
        if tag == "title":
            self.in_title = True
        if tag in VOID:
            return
        self.next_id += 1
        hide = tag in SKIP_TAGS or "hidden" in a or a.get("aria-hidden") == "true"
        self.stack.append((tag, self.next_id, hide))
        self.skip += hide
        if tag == "p" and not self.skip:
            chain = tuple(i for t, i, _ in self.stack[:-1] if t in BLOCKS)
            self.para = [chain, []]

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag in VOID or not any(e[0] == tag for e in self.stack):
            return
        while self.stack:
            t, _, hide = self.stack.pop()
            self.skip -= hide
            if t == "p" and self.para:
                text = " ".join("".join(self.para[1]).split())
                if text:
                    self.paras.append((self.para[0], text))
                self.para = None
            if t == tag:
                break

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)
        if self.para is not None and not self.skip:
            self.para[1].append(data)


def article(url, min_chars=600):
    """{"paras", "image", "title", "url"} from a publisher page, or None."""
    try:
        raw = fetch(url, timeout=15)
    except Exception as e:
        print(f"article: fetch failed ({type(e).__name__}) for {url.split('/')[2] if '//' in url else url}", flush=True)
        return None
    p = _Article()
    try:
        p.feed(raw.decode("utf-8", "replace"))
    except Exception:
        return None
    scores = {}
    for chain, text in p.paras:
        if chain:
            scores[chain[-1]] = scores.get(chain[-1], 0) + len(text)
        if len(chain) > 1:
            scores[chain[-2]] = scores.get(chain[-2], 0) + len(text) / 2
    if not scores:
        return None
    best = max(scores, key=scores.get)
    paras = [t for chain, t in p.paras if best in chain and len(t) >= 40 and not (len(t) < 160 and JUNK.search(t))]
    if sum(len(t) for t in paras) < min_chars:
        return None
    image = p.meta.get("og:image") or p.meta.get("twitter:image") or ""
    return {"paras": paras, "image": urljoin(url, image) if image else "",
            "title": p.meta.get("og:title") or " ".join("".join(p.title).split()), "url": url}


GRAY = "/System/Library/ColorSync/Profiles/Generic Gray Gamma 2.2 Profile.icc"


def gray_jpeg(url, out_path, max_width=1072, quality=60):
    """Download an image and write a grayscale JPEG at most max_width wide. True on success."""
    try:
        data = fetch(url, timeout=15)
    except Exception:
        return False
    if len(data) < 2000:
        return False
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src"
        src.write_bytes(data)
        try:
            info = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(src)],
                                  capture_output=True, text=True, timeout=30).stdout
            w = int(re.search(r"pixelWidth: (\d+)", info).group(1))
            h = int(re.search(r"pixelHeight: (\d+)", info).group(1))
            if w < 300 or h < 150:
                return False  # icons and tracking pixels
            cmd = ["sips", "-m", GRAY, "-s", "format", "jpeg", "-s", "formatOptions", str(quality)]
            if w > max_width:
                cmd += ["--resampleWidth", str(max_width)]
            r = subprocess.run(cmd + [str(src), "--out", str(out_path)], capture_output=True, timeout=60)
            return r.returncode == 0 and Path(out_path).exists()
        except Exception:
            return False
