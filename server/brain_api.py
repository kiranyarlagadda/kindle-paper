#!/usr/bin/env python3
"""brain-api: a small JSON API and habit widget over a notes folder, plus the Kindle endpoints.

Read endpoints (bearer token + strict IP allowlist, for a server-side dashboard):
  /todos /projects /today /health

Habit widget (LAN/tailnet browsers, HABIT_TOKEN as ?key= query param):
  GET  /habits       -> interactive checkbox page (embedded in a dashboard iframe)
  GET  /habits.json  -> today's habit/task checkbox lines
  POST /toggle       -> toggle one checkbox in today's daily note
  POST /create-note  -> create today's note from the Daily template
  GET  /kindle       -> Kindle e-ink morning page (HTML, see kindle.py)
  GET  /kindle.png   -> that page rendered by kindle_render.sh
  GET  /paper        -> browser preview of the latest Kiran's Paper (see paper.py)
  GET  /paper/view/<page>, /paper/latest.epub, /paper/<date>.epub, /paper/editions.json
  GET  /paper/tzfile -> zoneinfo file for the current location's time zone
  POST /kindle/log   -> the reader posts its power log here after each morning wake

Writes are line-anchored (exact text +
ordinal), scoped to the dailynag habits block or the ## Tasks section of
today's note only, atomic temp+rename, never rewrites the whole note.

Stdlib only. Env: VAULT_PATH, PORT, BRAIN_API_TOKEN, ALLOWED_IPS,
HABIT_TOKEN, HABIT_NETS.
"""

import ipaddress
import json
import os
import re
import tempfile
import time
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import kindle
import paper

VAULT = Path(os.environ.get("VAULT_PATH", os.path.expanduser("~/vault")))
PORT = int(os.environ.get("PORT", "8484"))
TOKEN = os.environ.get("BRAIN_API_TOKEN", "")
ALLOWED_IPS = {ip.strip() for ip in os.environ.get("ALLOWED_IPS", "").split(",") if ip.strip()}
ALLOWED_IPS.add("127.0.0.1")
HABIT_TOKEN = os.environ.get("HABIT_TOKEN", "")
HABIT_NETS = [ipaddress.ip_network(n.strip()) for n in os.environ.get(
    "HABIT_NETS", "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,100.64.0.0/10,127.0.0.0/8").split(",")]

TASK_RE = re.compile(r"^\s*- \[( |x)\] (.*)$")
DUE_RE = re.compile(r"\*\(due: ([0-9-]+)\)\*")
ID_RE = re.compile(r"`id:[A-F0-9-]+`")
STAMP_RE = re.compile(r"\s*✅ \d{1,2}:\d{2}\s*$")
HAB_START = "<!-- dailynag:habits:start -->"
HAB_END = "<!-- dailynag:habits:end -->"
KINDLE_PNG = Path(__file__).resolve().parent / "out" / "kindle.png"


def clean_task(text):
    text = ID_RE.sub("", text)
    text = DUE_RE.sub("", text)
    return text.strip()


def todos():
    path = VAULT / "99 System" / "Todo.md"
    out = {"overdue": [], "upcoming": []}
    section = None
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        if line.startswith("## "):
            name = line[3:].strip().lower()
            section = name if name in ("overdue", "upcoming") else None
            continue
        m = TASK_RE.match(line)
        if section and m and m.group(1) == " ":
            due = DUE_RE.search(m.group(2))
            out[section].append({
                "text": clean_task(m.group(2)),
                "due": due.group(1) if due else "",
            })
    return out


def frontmatter(path):
    fm = {}
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return fm
    if not lines or lines[0].strip() != "---":
        return fm
    for line in lines[1:30]:
        if line.strip() == "---":
            break
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm


def projects():
    items = []
    root = VAULT / "20 Projects"
    for idx in sorted(root.glob("*/_index.md")):
        fm = frontmatter(idx)
        status = fm.get("status", "")
        if status not in ("active", "todo", "in-progress"):
            continue
        try:
            rank = int(fm.get("rank", "9"))
        except ValueError:
            rank = 9
        items.append({"name": idx.parent.name, "status": status, "rank": rank})
    items.sort(key=lambda p: (0 if p["status"] != "todo" else 1, p["rank"], p["name"]))
    return {"projects": items}


def daily_path():
    return VAULT / "10 Daily" / "Daily" / f"{date.today().isoformat()}.md"


def editable_regions(lines):
    """Line indices inside the dailynag habits block or the ## Tasks section."""
    idxs = {"habits": [], "tasks": []}
    in_block = False
    in_tasks = False
    for i, line in enumerate(lines):
        s = line.strip()
        if s == HAB_START:
            in_block = True
            continue
        if s == HAB_END:
            in_block = False
            continue
        if s.startswith("## "):
            in_tasks = s == "## Tasks"
            continue
        if TASK_RE.match(line):
            if in_block:
                idxs["habits"].append(i)
            elif in_tasks:
                idxs["tasks"].append(i)
    return idxs


def day_habit_states(d):
    """{label: done} for the habits block of an arbitrary day, or None if no note."""
    p = VAULT / "10 Daily" / "Daily" / f"{d.isoformat()}.md"
    if not p.exists():
        return None
    out = {}
    in_block = False
    for line in p.read_text().splitlines():
        s = line.strip()
        if s == HAB_START:
            in_block = True
            continue
        if s == HAB_END:
            break
        if in_block:
            m = TASK_RE.match(line)
            if m:
                label = STAMP_RE.sub("", m.group(2)).strip()
                if label:
                    out[label] = m.group(1) == "x"
    return out


def habit_streak(label, done_today):
    """Consecutive checked days ending today (if done) or yesterday."""
    streak = 1 if done_today else 0
    d = date.today() - timedelta(days=1)
    for _ in range(90):
        states = day_habit_states(d)
        if not states or not states.get(label, False):
            break
        streak += 1
        d -= timedelta(days=1)
    return streak


def health_today():
    p = VAULT / "10 Daily" / "Health" / f"{date.today().isoformat()}-health.md"
    if not p.exists():
        p = VAULT / "10 Daily" / "Health" / f"{(date.today() - timedelta(days=1)).isoformat()}-health.md"
        if not p.exists():
            return {}
    fm = frontmatter(p)
    out = {}
    for key, label in (("steps", "steps"), ("sleep_total_min", "sleep_min"),
                       ("resting_hr", "rhr"), ("hrv", "hrv")):
        if key in fm:
            out[label] = fm[key]
    out["day"] = p.name.replace("-health.md", "")
    return out


def habits_data():
    path = daily_path()
    out = {"date": date.today().isoformat(), "note_exists": path.exists(),
           "habits": [], "tasks": [], "health": health_today()}
    if not path.exists():
        return out
    lines = path.read_text().splitlines()
    regions = editable_regions(lines)
    for section in ("habits", "tasks"):
        seen = {}
        for i in regions[section]:
            m = TASK_RE.match(lines[i])
            raw = lines[i]
            ordinal = seen.get(raw, 0)
            seen[raw] = ordinal + 1
            label = STAMP_RE.sub("", m.group(2)).strip()
            if not label:
                continue
            item = {
                "line": raw, "ordinal": ordinal,
                "text": label, "done": m.group(1) == "x",
            }
            if section == "habits":
                item["streak"] = habit_streak(label, item["done"])
            out[section].append(item)
    return out


def atomic_write(path, text):
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".brainapi-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def toggle(body):
    path = daily_path()
    if not path.exists():
        return 409, {"error": "no daily note"}
    section = body.get("section")
    target = body.get("line", "")
    ordinal = int(body.get("ordinal", 0))
    done = bool(body.get("done"))
    if section not in ("habits", "tasks"):
        return 400, {"error": "bad section"}
    lines = path.read_text().splitlines(keepends=False)
    ends_nl = True
    regions = editable_regions(lines)
    seen = 0
    for i in regions[section]:
        if lines[i] != target:
            continue
        if seen != ordinal:
            seen += 1
            continue
        m = TASK_RE.match(lines[i])
        rest = STAMP_RE.sub("", m.group(2)).rstrip()
        indent = lines[i][:lines[i].index("- [")]
        if done:
            stamp = datetime.now().strftime("%H:%M")
            lines[i] = f"{indent}- [x] {rest} ✅ {stamp}"
        else:
            lines[i] = f"{indent}- [ ] {rest}"
        atomic_write(path, "\n".join(lines) + ("\n" if ends_nl else ""))
        return 200, {"ok": True, "line": lines[i]}
    return 409, {"error": "line not found (note changed?)"}


def add_task(body):
    text = str(body.get("text", "")).strip().replace("\n", " ").replace("\r", "")[:300]
    if not text:
        return 400, {"error": "empty task"}
    path = daily_path()
    if not path.exists():
        return 409, {"error": "no daily note"}
    lines = path.read_text().splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == "## Tasks")
    except StopIteration:
        return 409, {"error": "no ## Tasks section"}
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break
    insert_at = start + 1
    for i in range(start + 1, end):
        if TASK_RE.match(lines[i]) or lines[i].strip() == "":
            if TASK_RE.match(lines[i]):
                insert_at = i + 1
            elif lines[i].strip() == "" and insert_at == start + 1:
                insert_at = i + 1
    lines.insert(insert_at, f"- [ ] {text}")
    atomic_write(path, "\n".join(lines) + "\n")
    return 200, {"ok": True}


def create_note():
    path = daily_path()
    if path.exists():
        return 200, {"ok": True, "existed": True}
    tpl = VAULT / "40 Templates" / "Daily.md"
    if not tpl.exists():
        return 500, {"error": "template missing"}
    d = date.today()
    iso_year, iso_week, _ = d.isocalendar()
    text = tpl.read_text()
    for pat, val in (
        ("{{title}}", d.isoformat()),
        ("{{date:YYYY-MM-DD}}", d.isoformat()),
        ("{{date:GGGG-[W]WW}}", f"{iso_year}-W{iso_week:02d}"),
        ("{{date:YYYY-MM}}", d.strftime("%Y-%m")),
    ):
        text = text.replace(pat, val)
    text = f"---\ndate: {d.isoformat()}\n---\n\n" + text
    atomic_write(path, text)
    return 200, {"ok": True, "existed": False}


def today():
    d = date.today()
    path = daily_path()
    tasks, done = [], 0
    exists = path.exists()
    if exists:
        for line in path.read_text().splitlines():
            m = TASK_RE.match(line)
            if m:
                checked = m.group(1) == "x"
                done += checked
                tasks.append({"text": clean_task(STAMP_RE.sub("", m.group(2))), "done": checked})
    inbox = VAULT / "00 Inbox"
    inbox_count = sum(1 for f in inbox.iterdir() if not f.name.startswith(".")) if inbox.exists() else 0
    h = habits_data()
    return {
        "date": d.isoformat(),
        "note_exists": exists,
        "tasks": tasks[:8],
        "open": sum(1 for t in tasks if not t["done"]),
        "done": done,
        "inbox": inbox_count,
        "habits_done": sum(1 for x in h["habits"] if x["done"]),
        "habits_total": len(h["habits"]),
    }


HABITS_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body { margin: 0; padding: 6px 4px; background: transparent; color: #d6d6d9;
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
.wrap { display: flex; gap: 20px; flex-wrap: wrap; }
.col { flex: 1; min-width: 240px; }
.hero { display: flex; align-items: center; gap: 16px; margin: 0 0 10px; }
.ring { position: relative; width: 92px; height: 92px; flex: none; }
.ring svg { transform: rotate(-90deg); }
.ring .pct { position: absolute; inset: 0; display: flex; align-items: center;
  justify-content: center; font-size: 20px; font-weight: 600; }
.hero .sub { color: #8a8a91; font-size: 13px; }
.hero .big { font-size: 17px; font-weight: 600; color: #d6d6d9; }
.hdr { font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;
  color: #8a8a91; margin: 0 0 4px; }
label { display: flex; gap: 10px; align-items: baseline; padding: 5px 0;
  cursor: pointer; border-bottom: 1px solid #232329; }
label.done span.t { color: #6c6c73; text-decoration: line-through; }
input[type=checkbox] { width: 17px; height: 17px; accent-color: #4ade80; cursor: pointer; }
.meta { font-size: 12px; color: #6c6c73; margin-left: auto; white-space: nowrap; }
.streak { color: #f59e0b; }
.health { display: flex; gap: 14px; margin: 8px 0 0; font-size: 13px; color: #8a8a91; }
.health b { color: #d6d6d9; font-weight: 600; }
.msg { color: #8a8a91; }
button { background: #2a2a30; color: #d6d6d9; border: 1px solid #3a3a41;
  border-radius: 6px; padding: 6px 12px; cursor: pointer; }
.addrow { display: flex; gap: 8px; margin-top: 8px; }
.addrow input { flex: 1; background: #1c1c21; border: 1px solid #3a3a41;
  border-radius: 6px; color: #d6d6d9; padding: 6px 10px; font-size: 14px; }
</style></head><body>
<div id="app" class="msg">Loading…</div>
<script>
const key = new URLSearchParams(location.search).get("key") || "";
const q = "?key=" + encodeURIComponent(key);
let data = null;

async function load() {
  const r = await fetch("/habits.json" + q);
  data = await r.json();
  render();
}

function esc(s) { return s.replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }

function ring(pct) {
  const r = 40, c = 2 * Math.PI * r;
  return '<div class="ring"><svg width="92" height="92">' +
    '<circle cx="46" cy="46" r="' + r + '" fill="none" stroke="#2a2a30" stroke-width="9"/>' +
    '<circle cx="46" cy="46" r="' + r + '" fill="none" stroke="' + (pct >= 100 ? "#4ade80" : "#38bdf8") +
    '" stroke-width="9" stroke-linecap="round" stroke-dasharray="' + c +
    '" stroke-dashoffset="' + (c * (1 - pct / 100)) + '"/></svg>' +
    '<div class="pct">' + pct + "%</div></div>";
}

function row(sec, it, n) {
  const stamp = (it.line.match(/✅ (\\d{1,2}:\\d{2})/) || [])[1];
  let meta = "";
  if (sec === "habits" && it.streak > 1) meta += '<span class="streak">' + it.streak + "d</span> ";
  if (stamp) meta += stamp;
  return '<label class="' + (it.done ? "done" : "") + '"><input type="checkbox" ' +
    (it.done ? "checked" : "") + ' onchange="tog(\\'' + sec + '\\',' + n + ', this.checked)">' +
    '<span class="t">' + esc(it.text) + "</span>" +
    (meta ? '<span class="meta">' + meta + "</span>" : "") + "</label>";
}

function render() {
  const app = document.getElementById("app");
  app.classList.remove("msg");
  if (!data.note_exists) {
    app.innerHTML = '<p class="msg">No daily note for ' + data.date +
      ' yet.</p><button onclick="createNote()">Create from template</button>';
    return;
  }
  const hab = data.habits, tasks = data.tasks;
  const done = hab.filter(i => i.done).length;
  const pct = hab.length ? Math.round(100 * done / hab.length) : 0;
  const best = Math.max(0, ...hab.map(i => i.streak || 0));

  let health = "";
  const hd = data.health || {};
  if (hd.steps || hd.sleep_min || hd.rhr) {
    health = '<div class="health">' +
      (hd.steps ? "<span><b>" + Number(hd.steps).toLocaleString() + "</b> steps</span>" : "") +
      (hd.sleep_min ? "<span><b>" + (hd.sleep_min / 60).toFixed(1) + "h</b> sleep</span>" : "") +
      (hd.rhr ? "<span><b>" + hd.rhr + "</b> rhr</span>" : "") +
      (hd.hrv ? "<span><b>" + hd.hrv + "</b> hrv</span>" : "") + "</div>";
  }

  let h = '<div class="wrap"><div class="col">' +
    '<div class="hero">' + ring(pct) +
    '<div><div class="big">' + done + " of " + hab.length + " done</div>" +
    '<div class="sub">' + (best > 1 ? "best streak " + best + " days" : "build a streak today") + "</div>" +
    health + "</div></div>";
  hab.forEach((it, n) => h += row("habits", it, n));
  h += "</div>";

  h += '<div class="col"><p class="hdr">Note tasks · ' +
    tasks.filter(i => i.done).length + "/" + tasks.length + "</p>";
  tasks.forEach((it, n) => h += row("tasks", it, n));
  h += '<div class="addrow"><input id="newtask" placeholder="Add a task…" ' +
    'onkeydown="if(event.key===\\'Enter\\')addTask()"><button onclick="addTask()">Add</button></div></div></div>';

  app.innerHTML = h;
}

async function tog(sec, n, checked) {
  const it = data[sec][n];
  await fetch("/toggle" + q, {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({section: sec, line: it.line, ordinal: it.ordinal, done: checked}),
  });
  await load();
}

async function addTask() {
  const el = document.getElementById("newtask");
  const text = el.value.trim();
  if (!text) return;
  await fetch("/add-task" + q, {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({text: text}),
  });
  el.value = "";
  await load();
}

async function createNote() {
  await fetch("/create-note" + q, {method: "POST"});
  await load();
}

load();
setInterval(load, 60000);
</script></body></html>"""


ROUTES = {
    "/todos": todos,
    "/projects": projects,
    "/today": today,
    "/health": lambda: {"ok": True, "vault": str(VAULT), "vault_exists": VAULT.exists()},
}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _habit_auth(self, query):
        try:
            ip = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            return False
        if not any(ip in net for net in HABIT_NETS):
            print(f"habit-auth deny (net): {ip}", flush=True)
            return False
        if not (HABIT_TOKEN and query.get("key", [""])[0] == HABIT_TOKEN):
            print(f"habit-auth deny (key): {ip}", flush=True)
            return False
        return True

    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path
        if route in ("/kindle", "/kindle.png"):
            if not self._habit_auth(parse_qs(parsed.query)):
                self.send_error(403)
                return
            try:
                if route == "/kindle":
                    self._send(200, kindle.page(habits_data(), todos()), "text/html; charset=utf-8")
                else:
                    self._send(200, KINDLE_PNG.read_bytes(), "image/png")
            except FileNotFoundError:
                self.send_error(404, "not rendered yet")
            except Exception as e:
                self.send_error(500, str(e))
            return
        if route == "/paper" or route.startswith("/paper/"):
            query = parse_qs(parsed.query)
            if not self._habit_auth(query):
                self.send_error(403)
                return
            try:
                code, body, ctype, headers = paper.serve(route, query["key"][0])
                self._send(code, body, ctype, headers)
            except Exception as e:
                self.send_error(500, str(e))
            return
        if route in ("/habits", "/habits.json"):
            if not self._habit_auth(parse_qs(parsed.query)):
                self.send_error(403)
                return
            if route == "/habits":
                self._send(200, HABITS_HTML, "text/html; charset=utf-8")
            else:
                self._send(200, habits_data())
            return
        if self.client_address[0] not in ALLOWED_IPS:
            self.send_error(403)
            return
        if TOKEN and route != "/health":
            if self.headers.get("Authorization", "") != f"Bearer {TOKEN}":
                self.send_error(401)
                return
        fn = ROUTES.get(route)
        if not fn:
            self.send_error(404)
            return
        try:
            self._send(200, fn())
        except Exception as e:
            self.send_error(500, str(e))

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/kindle/log":
            # The Kindle posts its power log after each alarm refresh, so mornings can be checked
            # while it sleeps. Kept to the last 64 KB in out/kindle-power.log.
            if not self._habit_auth(parse_qs(parsed.query)):
                self.send_error(403)
                return
            data = self.rfile.read(min(int(self.headers.get("Content-Length", "0")), 16384))
            log = KINDLE_PNG.parent / "kindle-power.log"
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            old = log.read_bytes()[-49152:] if log.exists() else b""
            log.write_bytes(old + f"--- received {stamp}\n".encode() + data + b"\n")
            self._send(200, {"ok": True})
            return
        if parsed.path not in ("/toggle", "/create-note", "/add-task"):
            self.send_error(404)
            return
        if not self._habit_auth(parse_qs(parsed.query)):
            self.send_error(403)
            return
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 65536)
            body = json.loads(self.rfile.read(length) or b"{}")
            if parsed.path == "/toggle":
                code, resp = toggle(body)
            elif parsed.path == "/add-task":
                code, resp = add_task(body)
            else:
                code, resp = create_note()
            self._send(code, resp)
        except Exception as e:
            self.send_error(500, str(e))

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
