"""Sports section of Kiran's Paper, from ESPN's free site API (plus Jolpica for F1).

Covers yesterday's finished games. NBA leads (scores, standings in the regular
season, the favorite player's stat line), then NFL, MLB, F1, tennis (Grand
Slams only) and the favorite soccer club. Leagues with nothing to show drop out.

Worth watching: every finished game gets a watchability score (margin, overtime
or extra innings, comebacks, lead changes, playoff stakes, star lines, red
cards). Games involving a favorite team or player need a lower score. Flagged
games are spoiler-free across the whole edition: paper.py hides their scores and
only reveals them on a separate "Show score" page.

Env: PAPER_FAV_TEAMS (comma list of full team names), PAPER_FAV_PLAYER,
PAPER_SOCCER_TEAM.
"""

import os
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone

import extract
import llm

ESPN = "https://site.api.espn.com/apis/site/v2/sports"
ESPN_V2 = "https://site.api.espn.com/apis/v2/sports"
FAV_TEAMS = [t.strip() for t in os.environ.get(
    "PAPER_FAV_TEAMS", "Real Madrid,Los Angeles Dodgers,Los Angeles Lakers").split(",") if t.strip()]
FAV_PLAYER = os.environ.get("PAPER_FAV_PLAYER", "Luka Doncic")
SOCCER_TEAM = os.environ.get("PAPER_SOCCER_TEAM", "Real Madrid")
SOCCER_COMPS = [("esp.1", "La Liga"), ("uefa.champions", "Champions League"),
                ("esp.copa_del_rey", "Copa del Rey")]
LEAGUES = [("nba", "basketball/nba", "NBA"), ("nfl", "football/nfl", "NFL"),
           ("mlb", "baseball/mlb", "MLB")]
SLAMS = re.compile(r"australian open|roland garros|french open|wimbledon|us open", re.I)
SPOILER = re.compile(r"\b(won|wins?|winning|beat|beats|beaten|defeat\w*|lost|loses|losing|victory|"
                     r"victorious|edged?|rout\w*|upset|clinch\w*|eliminat\w*|advanc\w*|sweep|swept|"
                     r"triumph\w*|held on|walk-?off|shut ?out|blowout)\b|\d+\s*[-–]\s*\d+", re.I)


def norm(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower().strip()


def _get(url):
    try:
        return extract.fetch_json(url, timeout=15)
    except Exception as e:
        print(f"sports: {url.split('/sports/')[-1][:60]} failed: {type(e).__name__}", flush=True)
        return None


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _team(t):
    tm = t.get("team", {})
    rec = (t.get("records") or [{}])[0].get("summary", "") if t.get("records") else ""
    return {"id": tm.get("id"), "name": tm.get("displayName", ""), "short": tm.get("shortDisplayName", ""),
            "abbr": tm.get("abbreviation", ""), "score": _int(t.get("score")), "winner": t.get("winner"),
            "home": t.get("homeAway") == "home", "record": rec,
            "lines": [l.get("value") for l in t.get("linescores", [])]}


def _game(e, league):
    c = e["competitions"][0]
    st = e["status"]["type"]
    teams = sorted((_team(t) for t in c["competitors"]), key=lambda t: t["home"])
    notes = [n.get("headline", "") for n in c.get("notes", []) if n.get("headline")]
    g = {"id": e["id"], "league": league, "away": teams[0], "home": teams[-1],
         "final": bool(st.get("completed")), "state": st.get("state"),
         "detail": st.get("shortDetail") or st.get("detail", ""), "period": e["status"].get("period") or 0,
         "season_type": (e.get("season") or {}).get("type"), "note": notes[0] if notes else "",
         "date": e.get("date", ""), "leaders": [], "red_cards": 0, "goals": [], "stars": [],
         "lead_changes": None, "max_deficit": None, "player_line": None}
    for L in c.get("leaders", []):  # NFL keeps leaders on the competition
        for x in L.get("leaders", [])[:1]:
            g["leaders"].append((L.get("name"), x["athlete"]["displayName"], x.get("value") or 0,
                                 x.get("displayValue", "")))
    for d in c.get("details", []):  # soccer events
        if d.get("redCard"):
            g["red_cards"] += 1
        if d.get("scoringPlay"):
            minute = _int(re.sub(r"\D.*", "", (d.get("clock") or {}).get("displayValue", "0"))) or 0
            g["goals"].append(((d.get("team") or {}).get("id"), minute))
    g["fav"] = any(norm(t["name"]) == norm(f) for t in teams for f in FAV_TEAMS)
    return g


def _winner_loser(g):
    a, h = g["away"], g["home"]
    if a["score"] is None or h["score"] is None or a["score"] == h["score"]:
        return None, None
    return (a, h) if a["score"] > h["score"] else (h, a)


def _cum_deficit(g):
    """Winner's biggest deficit at any period break (from linescores)."""
    w, l = _winner_loser(g)
    if not w or not w["lines"] or not l["lines"]:
        return 0
    worst, ws, ls = 0, 0, 0
    for a, b in zip(w["lines"], l["lines"]):
        ws += a or 0
        ls += b or 0
        worst = max(worst, ls - ws)
    return worst


def nba_details(g):
    """Lead changes, biggest comeback, star lines and the favorite player's line."""
    s = _get(f"{ESPN}/basketball/nba/summary?event={g['id']}")
    if not s:
        return
    diffs = [(_int(p.get("homeScore")) or 0) - (_int(p.get("awayScore")) or 0) for p in s.get("plays", [])]
    if diffs:
        signs = [1 if d > 0 else -1 for d in diffs if d]
        g["lead_changes"] = sum(1 for a, b in zip(signs, signs[1:]) if a != b)
        w, _ = _winner_loser(g)
        if w:
            g["max_deficit"] = max(0, max((-d if w["home"] else d) for d in diffs))
    fav = norm(FAV_PLAYER)
    for team in s.get("boxscore", {}).get("players", []):
        tname = team.get("team", {}).get("displayName", "")
        for block in team.get("statistics", [])[:1]:
            labels = block.get("labels", [])
            for a in block.get("athletes", []):
                st = dict(zip(labels, a.get("stats", [])))
                name = a.get("athlete", {}).get("displayName", "")
                pts, reb, ast = (_int(st.get(k)) or 0 for k in ("PTS", "REB", "AST"))
                if pts >= 40 or sum(x >= 10 for x in (pts, reb, ast)) >= 3:
                    g["stars"].append(f"{name} had a big night" if pts < 40 else f"{name} scored 40-plus")
                if fav and norm(name) == fav and st.get("MIN") not in (None, "", "0"):
                    g["player_line"] = {
                        "name": name, "team": tname,
                        "line": (f"{pts} pts, {reb} reb, {ast} ast, {st.get('STL', '0')} stl, "
                                 f"{st.get('BLK', '0')} blk, {st.get('FG', '')} FG, {st.get('3PT', '')} 3PT, "
                                 f"{st.get('TO', '0')} TO in {st.get('MIN', '')} min")}
                    g["fav"] = True


def watchability(g):
    """(score, [spoiler-free reasons]) for a finished game."""
    if not g["final"]:
        return 0, []
    w, l = _winner_loser(g)
    margin = abs((g["home"]["score"] or 0) - (g["away"]["score"] or 0))
    total = (g["home"]["score"] or 0) + (g["away"]["score"] or 0)
    L, s, why = g["league"], 0, []

    def add(n, reason):
        nonlocal s
        s += n
        why.append(reason)

    if L == "NBA":
        if margin <= 3:
            add(3, "came down to the final possessions")
        elif margin <= 6:
            add(2, "close at the finish")
        if g["period"] > 4:
            add(3, "went to overtime" if g["period"] == 5 else "went to multiple overtimes")
        if (g["lead_changes"] or 0) >= 12:
            add(2, f"the lead changed hands {g['lead_changes']} times")
        if (g["max_deficit"] or 0) >= 15:
            add(2, "featured a big comeback")
        if g["season_type"] == 1:
            s -= 4
    elif L == "NFL":
        if margin <= 3:
            add(3, "came down to the final drive")
        elif margin <= 8:
            add(2, "a one-score game")
        if g["period"] > 4:
            add(3, "went to overtime")
        if _cum_deficit(g) >= 14:
            add(2, "featured a big comeback")
        elif w and w["lines"] and len(w["lines"]) >= 3 and sum(w["lines"][:3]) < sum(l["lines"][:3]):
            add(2, "the lead flipped in the fourth quarter")
        for cat, name, val, _ in g["leaders"]:
            if (cat == "passingYards" and val >= 350) or (cat in ("rushingYards", "receivingYards") and val >= 150):
                g["stars"].append(f"a big day from {name}")
    elif L == "MLB":
        if margin <= 1:
            add(2, "a tight finish")
        if g["period"] > 9:
            add(3, "went to extra innings")
        if w and len(w["lines"]) >= 7 and sum(x or 0 for x in w["lines"][:7]) < sum(x or 0 for x in l["lines"][:7]):
            add(2, "a late rally")
        if total >= 15:
            add(1, "a slugfest")
    else:  # soccer
        if g["state"] == "post" and re.search(r"AET|Pen|ET", g["detail"] or ""):
            add(3, "went beyond 90 minutes")
        if margin <= 1:
            add(1, "a tight match")
        if total >= 4:
            add(2, "plenty of goals")
        if g["red_cards"]:
            add(min(2, g["red_cards"]), "a red card" if g["red_cards"] == 1 else "red cards")
        if w and g["goals"]:
            run = {w["id"]: 0, l["id"]: 0}
            trailed = False
            for tid, _ in sorted(g["goals"], key=lambda x: x[1]):
                if tid in run:
                    run[tid] += 1
                    trailed = trailed or run[l["id"]] > run[w["id"]]
            if trailed:
                add(2, "a comeback")
        if any(m >= 85 for _, m in g["goals"]):
            add(2, "late drama")
    if g["season_type"] == 3 or re.search(r"playoff|wild card|series|final|NLCS|ALCS|NLDS|ALDS|knockout",
                                          g["note"], re.I):
        add(2, "playoff stakes")
    if re.search(r"game 7|game 5", g["note"], re.I) and g["season_type"] == 3:
        add(1, "a possible deciding game")
    if g["stars"]:
        add(2, g["stars"][0])
    return s, why


def nba_standings():
    d = _get(f"{ESPN_V2}/basketball/nba/standings")
    if not d:
        return []
    out = []
    for conf in d.get("children", []):
        rows = []
        for e in conf.get("standings", {}).get("entries", []):
            st = {x.get("name"): x.get("displayValue") for x in e.get("stats", [])}
            rows.append({"team": e["team"].get("shortDisplayName") or e["team"].get("displayName"),
                         "full": e["team"].get("displayName", ""),
                         "w": st.get("wins", ""), "l": st.get("losses", ""), "gb": st.get("gamesBehind", ""),
                         "seed": _int(st.get("playoffSeed")) or 99})
        rows.sort(key=lambda r: r["seed"])
        out.append({"name": conf.get("name", ""), "rows": rows[:10]})
    return out


def f1():
    now = datetime.now(timezone.utc)
    out = {}
    d = _get(f"{ESPN}/racing/f1/scoreboard")
    for e in (d or {}).get("events", []):
        race = next((c for c in e.get("competitions", [])
                     if (c.get("type") or {}).get("abbreviation") == "Race"), None)
        if not race or not race["status"]["type"].get("completed"):
            continue
        when = datetime.fromisoformat(race["date"].replace("Z", "+00:00"))
        if now - when < timedelta(hours=48):
            podium = sorted(race.get("competitors", []), key=lambda x: x.get("order", 99))[:3]
            out["race"] = e.get("name", "")
            out["podium"] = [x.get("athlete", {}).get("displayName", "") for x in podium]
    nxt = _get("https://api.jolpi.ca/ergast/f1/current/next.json")
    races = (nxt or {}).get("MRData", {}).get("RaceTable", {}).get("Races", [])
    if races:
        r = races[0]
        when = datetime.fromisoformat(f"{r['date']}T{r.get('time', '12:00:00Z').replace('Z', '+00:00')}")
        days = (when.date() - now.date()).days
        if 0 <= days <= 45:
            out["next"] = {"name": r.get("raceName", ""), "where": r.get("Circuit", {}).get("Location", {}).get("locality", ""),
                           "when": when, "days": days}
    return out or None


def tennis():
    now = datetime.now(timezone.utc)
    results = []
    for tour in ("atp", "wta"):
        d = _get(f"{ESPN}/tennis/{tour}/scoreboard")
        for e in (d or {}).get("events", []):
            if not SLAMS.search(e.get("name", "")):
                continue
            for grp in e.get("groupings", []):
                if "Singles" not in grp.get("grouping", {}).get("displayName", ""):
                    continue
                for c in grp.get("competitions", []):
                    if not c.get("status", {}).get("type", {}).get("completed"):
                        continue
                    rnd = c.get("round", {}).get("displayName", "")
                    if "Qualifying" in rnd:
                        continue
                    when = datetime.fromisoformat(c.get("date", "1970-01-01T00:00Z").replace("Z", "+00:00"))
                    if now - when > timedelta(hours=30):
                        continue
                    ps = c.get("competitors", [])
                    if len(ps) != 2:
                        continue
                    w = next((p for p in ps if p.get("winner")), ps[0])
                    l = ps[1] if w is ps[0] else ps[0]
                    sets = " ".join(f"{_int(a.get('value'))}-{_int(b.get('value'))}"
                                    for a, b in zip(w.get("linescores", []), l.get("linescores", [])))
                    results.append({"event": e["name"], "draw": grp["grouping"]["displayName"], "round": rnd,
                                    "text": f"{w.get('athlete', {}).get('displayName', '')} d. "
                                            f"{l.get('athlete', {}).get('displayName', '')} {sets}"})
    rank = {"Final": 0, "Semifinal": 1, "Quarterfinal": 2}
    results.sort(key=lambda r: next((v for k, v in rank.items() if k in r["round"]), 5))
    return results[:12]


def soccer(day):
    """Favorite club: yesterday's result, today's or next fixture, league table."""
    out = {"team": SOCCER_TEAM, "results": [], "next": None, "table": []}
    team_id = None
    for slug, comp in SOCCER_COMPS:
        d = _get(f"{ESPN}/soccer/{slug}/scoreboard?dates={day:%Y%m%d}")
        for e in (d or {}).get("events", []):
            g = _game(e, comp)
            if norm(SOCCER_TEAM) in (norm(g["home"]["name"]), norm(g["away"]["name"])):
                g["fav"] = True
                team_id = g["home"]["id"] if norm(g["home"]["name"]) == norm(SOCCER_TEAM) else g["away"]["id"]
                out["results"].append(g)
    if not team_id:
        teams = _get(f"{ESPN}/soccer/esp.1/teams")
        for t in ((teams or {}).get("sports") or [{}])[0].get("leagues", [{}])[0].get("teams", []):
            if norm(t["team"].get("displayName")) == norm(SOCCER_TEAM):
                team_id = t["team"]["id"]
    if team_id:
        for slug, comp in SOCCER_COMPS[:2]:
            sch = _get(f"{ESPN}/soccer/{slug}/teams/{team_id}/schedule?fixture=true")
            for e in (sch or {}).get("events", []):
                when = datetime.fromisoformat(e["date"].replace("Z", "+00:00"))
                if when > datetime.now(timezone.utc) and (not out["next"] or when < out["next"]["when"]):
                    out["next"] = {"name": e.get("name", ""), "comp": comp, "when": when}
                    break
    if out["next"] and (out["next"]["when"] - datetime.now(timezone.utc)).days > 14:
        out["next"] = None
    tbl = _get(f"{ESPN_V2}/soccer/esp.1/standings")
    for grp in (tbl or {}).get("children", [{}])[:1]:
        for e in grp.get("standings", {}).get("entries", []):
            st = {x.get("name"): x.get("displayValue") for x in e.get("stats", [])}
            out["table"].append({"rank": _int(st.get("rank")) or 99, "team": e["team"].get("displayName", ""),
                                 "pts": st.get("points", ""), "gp": _int(st.get("gamesPlayed")) or 0})
    out["table"].sort(key=lambda r: r["rank"])
    if not any(r["gp"] for r in out["table"]):
        out["table"] = []
    if not (out["results"] or out["next"] or out["table"]):
        return None
    return out


def today_fixtures(day):
    """Favorites playing today (scheduled games only), for the sports page."""
    out = []
    for key, path, name in LEAGUES:
        d = _get(f"{ESPN}/{path}/scoreboard?dates={day:%Y%m%d}")
        for e in (d or {}).get("events", []):
            g = _game(e, name)
            if g["fav"] and g["state"] == "pre":
                out.append({"league": name, "text": f"{g['away']['short']} at {g['home']['short']}",
                            "when": datetime.fromisoformat(g["date"].replace("Z", "+00:00")),
                            "note": g["note"]})
    return out


def watch_lines(flagged):
    """One spoiler-free line per flagged game from Qwen, else from the reasons."""
    if not flagged:
        return
    facts = "\n".join(
        f"{i}. {g['league']}: {g['away']['name']} at {g['home']['name']}"
        f"{' (' + g['note'] + ')' if g['note'] else ''}. Facts: {'; '.join(g['why'])}."
        for i, g in enumerate(flagged))
    out = llm.chat_json(
        "For each game, write one line telling a fan why it is worth watching on replay. It must be "
        "spoiler-free: never reveal the score, the winner, the margin, or who came back. Do not use "
        "the words won, beat, lost, victory, upset or clinched.",
        f"{facts}\n\nReturn {{\"lines\": [\"line for game 0\", ...]}} with under 25 words each.",
        max_tokens=1500)
    lines = out.get("lines", []) if isinstance(out, dict) else []
    for i, g in enumerate(flagged):
        line = lines[i] if i < len(lines) and isinstance(lines[i], str) else ""
        if not line or SPOILER.search(line):
            reasons = g["why"][:3]
            line = ", ".join(reasons[:-1]) + " and " + reasons[-1] if len(reasons) > 1 else reasons[0]
            line = line[0].upper() + line[1:] + "."
        g["watch_line"] = line


def build(day=None):
    """Everything for the sports pages. day = the date of the games (default yesterday)."""
    day = day or date.today() - timedelta(days=1)
    out = {"day": day, "leagues": [], "flagged": [], "player": None, "f1": None, "tennis": [],
           "soccer": None, "today": []}
    allgames = []
    for key, path, name in LEAGUES:
        d = _get(f"{ESPN}/{path}/scoreboard?dates={day:%Y%m%d}")
        if not d:
            continue
        season = (d.get("leagues") or [{}])[0].get("season", {}).get("type") or {}
        games = [_game(e, name) for e in d.get("events", [])]
        games = [g for g in games if g["final"]]
        if key == "nba":
            for g in games:
                nba_details(g)
                if g["player_line"]:
                    out["player"] = {**g["player_line"], "game": g}
        if not games:
            continue
        lg = {"key": key, "name": name, "phase": season.get("name", "") if season.get("type") in (1, 3) else "",
              "games": games, "standings": []}
        if key == "nba" and season.get("type") == 2:
            lg["standings"] = nba_standings()
        out["leagues"].append(lg)
        allgames += games
    out["soccer"] = soccer(day)
    if out["soccer"]:
        allgames += out["soccer"]["results"]
    out["f1"] = f1()
    out["tennis"] = tennis()
    out["today"] = today_fixtures(day + timedelta(days=1))

    for g in allgames:
        g["watch"], g["why"] = watchability(g)
    flagged = [g for g in allgames if g["why"] and g["watch"] >= (3 if g["fav"] else 5)]
    flagged.sort(key=lambda g: (-g["fav"], -g["watch"]))
    out["flagged"] = flagged[:4]
    for g in out["flagged"]:
        g["flagged"] = True
    watch_lines(out["flagged"])
    # Team names to keep out of news picks so a flagged result can't leak through a headline.
    out["avoid"] = sorted({n for g in out["flagged"] for t in (g["home"], g["away"])
                           for n in (t["name"], t["short"]) if n and len(n) > 3})
    print(f"sports: {len(allgames)} finished games, {len(out['flagged'])} worth watching", flush=True)
    return out
