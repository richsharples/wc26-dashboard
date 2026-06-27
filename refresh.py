#!/usr/bin/env python3
"""
Deterministic FIFA World Cup 2026 dashboard refresher (knockout phase).

No LLM calls. Pulls match data straight from ESPN's public scoreboard JSON
feed, recomputes group standings (kept as a collapsed archive) AND resolves
the single-elimination bracket from raw results, then rewrites index.html in
place. Designed to run inside GitHub Actions (cron + manual workflow_dispatch)
with zero Claude usage.

Bracket strategy: groups_data.json defines the official bracket topology
(match ids + feeders). Each tie's teams/scores are filled from ESPN by
matching the known seed in the tie, so third-place slots resolve themselves
once ESPN seeds the Round of 32. Downstream rounds chain winners forward and
show "Winner M##" placeholders until their feeder matches finish.

Usage: python3 refresh.py
Reads:  groups_data.json, favorites.md, index.html (as the template to patch)
Writes: index.html
"""
import json
import re
import sys
import datetime
import urllib.request
import urllib.parse
from pathlib import Path

HERE = Path(__file__).parent
SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"
TOURN_START = datetime.date(2026, 6, 11)
TOURN_END = datetime.date(2026, 7, 19)
GROUP_END = datetime.date(2026, 6, 27)
USER_AGENT = "Mozilla/5.0 (wc26-dashboard refresh bot; github actions)"

# Round boundaries (inclusive) used to label fixtures by phase.
ROUND_WINDOWS = [
    ("R32", datetime.date(2026, 6, 28), datetime.date(2026, 7, 3)),
    ("R16", datetime.date(2026, 7, 4), datetime.date(2026, 7, 7)),
    ("QF", datetime.date(2026, 7, 9), datetime.date(2026, 7, 11)),
    ("SF", datetime.date(2026, 7, 14), datetime.date(2026, 7, 15)),
    ("3P", datetime.date(2026, 7, 18), datetime.date(2026, 7, 18)),
    ("F", datetime.date(2026, 7, 19), datetime.date(2026, 7, 19)),
]
ROUND_LABELS = {
    "group": "Group stage", "R32": "Round of 32", "R16": "Round of 16",
    "QF": "Quarter-final", "SF": "Semi-final", "3P": "Third place", "F": "Final",
}
ROUND_SHORT = {"R32": "R32", "R16": "R16", "QF": "QF", "SF": "SF", "3P": "3rd", "F": "Final"}
NEXT_ROUND = {"R32": "R16", "R16": "QF", "QF": "SF", "SF": "F"}


def http_get_json(url, params):
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{qs}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_day(d):
    data = http_get_json(SCOREBOARD, {"dates": d.strftime("%Y%m%d"), "limit": 100})
    return data.get("events", [])


def collect_events():
    events = []
    d = TOURN_START
    while d <= TOURN_END:
        try:
            events.extend(fetch_day(d))
        except Exception as e:
            print(f"warning: failed to fetch {d}: {e}", file=sys.stderr)
        d += datetime.timedelta(days=1)
    return events


def round_for_date(d):
    if d <= GROUP_END:
        return "group"
    for key, start, end in ROUND_WINDOWS:
        if start <= d <= end:
            return key
    return "group"


def empty_stats():
    return {"GP": 0, "W": 0, "D": 0, "L": 0, "F": 0, "A": 0}


FAV_DEFAULT_COLORS = ("#0b2545", "#13315c")


def parse_favorites(md_text):
    """Parse favorites.md into [{name, flag, label, note, colors}, ...]."""
    favs = []
    current = None
    for raw in md_text.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            if current:
                favs.append(current)
            current = {"name": line[3:].strip(), "flag": "", "label": "", "note": "", "colors": None}
        elif current is not None and line.startswith("- ") and ":" in line:
            key, _, val = line[2:].partition(":")
            key, val = key.strip().lower(), val.strip()
            if key == "flag":
                current["flag"] = val
            elif key == "label":
                current["label"] = val
            elif key == "note":
                current["note"] = val
            elif key in ("color", "colors"):
                parts = [p.strip() for p in val.split(",") if p.strip()]
                if len(parts) == 1:
                    parts = [parts[0], parts[0]]
                if len(parts) >= 2:
                    current["colors"] = (parts[0], parts[1])
    if current:
        favs.append(current)
    return favs


def ordinal(n):
    return {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}.get(n, "—" if n is None else f"{n}th")


def parse_events(events, team_to_group):
    """Return (group_stats, fixtures). Stats accumulate group-stage results
    only; fixtures cover every match with round, winner side and shootout."""
    stats = {}
    fixtures = []
    for ev in events:
        try:
            comp = ev["competitions"][0]
        except (KeyError, IndexError):
            continue
        competitors = comp.get("competitors", [])
        home = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away = next((c for c in competitors if c.get("homeAway") == "away"), None)
        if not home or not away:
            continue
        home_name = home["team"]["displayName"]
        away_name = away["team"]["displayName"]
        status = comp.get("status", {}).get("type", {})
        completed = bool(status.get("completed"))
        state = status.get("state")  # pre | in | post
        venue = comp.get("venue", {}).get("fullName", "")
        city = comp.get("venue", {}).get("address", {}).get("city", "")
        date_iso = comp.get("date") or ev.get("date")

        dt = None
        if date_iso:
            try:
                dt = datetime.datetime.fromisoformat(date_iso.replace("Z", "+00:00"))
            except ValueError:
                dt = None
        rnd = round_for_date(dt.date()) if dt else "group"
        group = team_to_group.get(home_name) or team_to_group.get(away_name) if rnd == "group" else None

        try:
            hs = int(home.get("score")) if home.get("score") not in (None, "") else None
            asc = int(away.get("score")) if away.get("score") not in (None, "") else None
        except (TypeError, ValueError):
            hs = asc = None

        # Winner side (handles knockout draws decided on penalties via the
        # ESPN "winner" flag, falling back to the score line).
        if home.get("winner"):
            winner = "home"
        elif away.get("winner"):
            winner = "away"
        elif completed and hs is not None and asc is not None and hs != asc:
            winner = "home" if hs > asc else "away"
        else:
            winner = None

        def pen(c):
            v = c.get("shootoutScore")
            try:
                return int(v) if v not in (None, "") else None
            except (TypeError, ValueError):
                return None

        tv_us = []
        for b in comp.get("broadcasts") or []:
            for n in b.get("names") or []:
                n = "Telemundo" if n == "Tele" else n
                if n not in tv_us:
                    tv_us.append(n)

        fixtures.append({
            "round": rnd, "group": group,
            "home": home_name, "away": away_name,
            "home_score": hs, "away_score": asc,
            "home_pen": pen(home), "away_pen": pen(away),
            "completed": completed, "state": state, "winner": winner,
            "venue": venue, "city": city, "date": date_iso, "dt": dt,
            "tv_us": tv_us,
        })

        if rnd != "group" or not completed or hs is None or asc is None:
            continue
        for name in (home_name, away_name):
            stats.setdefault(name, empty_stats())
        stats[home_name]["GP"] += 1
        stats[away_name]["GP"] += 1
        stats[home_name]["F"] += hs
        stats[home_name]["A"] += asc
        stats[away_name]["F"] += asc
        stats[away_name]["A"] += hs
        if hs > asc:
            stats[home_name]["W"] += 1
            stats[away_name]["L"] += 1
        elif hs < asc:
            stats[away_name]["W"] += 1
            stats[home_name]["L"] += 1
        else:
            stats[home_name]["D"] += 1
            stats[away_name]["D"] += 1
    return stats, fixtures


def sort_key(team_stats):
    s = team_stats
    pts = s["W"] * 3 + s["D"]
    gd = s["F"] - s["A"]
    return (-pts, -gd, -s["F"])


def build_group_tables(groups_def, stats):
    out = {}
    for letter, teams in groups_def.items():
        rows = [(name, stats.get(name, empty_stats())) for name in teams]
        rows.sort(key=lambda t: sort_key(t[1]))
        all_played = all(s["GP"] >= 3 for _, s in rows)
        final_rows = []
        for i, (name, s) in enumerate(rows):
            cls = "q" if i < 2 else ("bub" if i == 2 else "out")
            pts = s["W"] * 3 + s["D"]
            final_rows.append([name, s["GP"], s["W"], s["D"], s["L"], s["F"], s["A"], pts, cls])
        out[letter] = {
            "status": "Final" if all_played else None,
            "rows": final_rows,
            "winner": final_rows[0][0] if all_played else None,
            "runnerup": final_rows[1][0] if all_played else None,
        }
    return out


def group_status_text(letter, g, fixtures):
    if g["status"] == "Final":
        return "Final"
    remaining = [f for f in fixtures if f["group"] == letter and not f["completed"]]
    if not remaining:
        return "In progress"
    n = len(remaining)
    return f"{n} game{'s' if n != 1 else ''} left"


def render_groups_js(groups_def, group_tables, fixtures):
    parts = []
    for letter in sorted(groups_def.keys()):
        g = group_tables[letter]
        status = group_status_text(letter, g, fixtures)
        rows_js = ", ".join(
            "[" + json.dumps(r[0], ensure_ascii=False) + "," + ",".join(str(x) for x in r[1:8]) + "," + json.dumps(r[8], ensure_ascii=False) + "]"
            for r in g["rows"]
        )
        parts.append(f'{{ name: "{letter}", status: {json.dumps(status, ensure_ascii=False)}, teams: [{rows_js}] }}')
    return "[\n  " + ",\n  ".join(parts) + "\n]"


# ---------------------------------------------------------------------------
# Bracket
# ---------------------------------------------------------------------------

def seed_team(slot, group_tables):
    """Resolve a concrete team name for a slot, or None if not yet known."""
    if "seed" in slot:
        s = slot["seed"]
        pos, letter = int(s[0]), s[1:]
        g = group_tables.get(letter)
        if not g or not g["winner"]:
            return None
        return g["winner"] if pos == 1 else g["runnerup"]
    return None  # third / from / loser resolve via ESPN or chaining


def seed_label(slot):
    if "seed" in slot:
        s = slot["seed"]
        pos, letter = s[0], s[1:]
        return f"Group {letter} {'winner' if pos == '1' else 'runner-up'}"
    if "third" in slot:
        return f"3rd: {slot['third']}"
    if "from" in slot:
        return f"Winner M{slot['from']}"
    if "loser" in slot:
        return f"Loser M{slot['loser']}"
    return "TBD"


def find_knockout_fixture(rnd, expected, fixtures, used):
    """Find an unused ESPN fixture in `rnd` whose teams intersect `expected`."""
    if not expected:
        return None
    for i, f in enumerate(fixtures):
        if i in used or f["round"] != rnd:
            continue
        if expected & {f["home"], f["away"]}:
            used.add(i)
            return f
    return None


def build_bracket(bracket_def, group_tables, fixtures):
    """Return ordered list of resolved bracket nodes for rendering."""
    nodes = {}
    winner_team, loser_team = {}, {}
    used = set()

    def resolve_slot_team(slot):
        if "from" in slot:
            return winner_team.get(slot["from"])
        if "loser" in slot:
            return loser_team.get(slot["loser"])
        return seed_team(slot, group_tables)

    for d in sorted(bracket_def, key=lambda n: n["id"]):
        nid, rnd = d["id"], d["round"]
        a_team = resolve_slot_team(d["a"])
        b_team = resolve_slot_team(d["b"])
        expected = {t for t in (a_team, b_team) if t}
        fx = find_knockout_fixture(rnd, expected, fixtures, used)

        node = {"id": nid, "round": rnd, "date": d["date"], "city": d["city"],
                "state": "pre", "winner": "", "tv_us": [], "tv_uk": d.get("tv_uk", "")}

        if fx:
            node["tv_us"] = fx.get("tv_us") or []
            ht, at = fx["home"], fx["away"]
            # Orient ESPN home/away onto the tie's a/b slots.
            if a_team and a_team == at:
                a_is_home = False
            elif b_team and b_team == ht:
                a_is_home = False
            else:
                a_is_home = True
            if a_is_home:
                a_name, b_name = ht, at
                a_sc, b_sc = fx["home_score"], fx["away_score"]
                a_pen, b_pen = fx["home_pen"], fx["away_pen"]
                a_win = fx["winner"] == "home"
                b_win = fx["winner"] == "away"
            else:
                a_name, b_name = at, ht
                a_sc, b_sc = fx["away_score"], fx["home_score"]
                a_pen, b_pen = fx["away_pen"], fx["home_pen"]
                a_win = fx["winner"] == "away"
                b_win = fx["winner"] == "home"
            node["a"] = {"name": a_name, "score": a_sc, "pen": a_pen}
            node["b"] = {"name": b_name, "score": b_sc, "pen": b_pen}
            node["state"] = fx["state"] or "pre"
            node["when"] = fx["dt"]
            node["city"] = fx["city"] or d["city"]
            if a_win:
                node["winner"] = "a"
            elif b_win:
                node["winner"] = "b"
            if node["winner"] and fx["completed"]:
                w = a_name if node["winner"] == "a" else b_name
                l = b_name if node["winner"] == "a" else a_name
                winner_team[nid] = w
                loser_team[nid] = l
        else:
            node["a"] = {"name": a_team or seed_label(d["a"]), "score": None, "pen": None,
                         "tbd": a_team is None}
            node["b"] = {"name": b_team or seed_label(d["b"]), "score": None, "pen": None,
                         "tbd": b_team is None}
            node["when"] = None

        nodes[nid] = node
    return [nodes[k] for k in sorted(nodes)]


def render_bracket_js(nodes):
    parts = []
    for n in nodes:
        when = n.get("when")
        utc = when.astimezone(datetime.timezone.utc).isoformat() if when else ""
        day = when.strftime("%b %-d") if when else _fmt_date(n["date"])
        a, b = n["a"], n["b"]
        parts.append(json.dumps({
            "id": n["id"], "round": n["round"], "utc": utc, "day": day, "city": n["city"],
            "state": n["state"], "winner": n["winner"],
            "tvUs": ", ".join(n.get("tv_us") or []), "tvUk": n.get("tv_uk", ""),
            "a": {"name": a["name"], "score": a["score"], "pen": a.get("pen"), "tbd": a.get("tbd", False)},
            "b": {"name": b["name"], "score": b["score"], "pen": b.get("pen"), "tbd": b.get("tbd", False)},
        }, ensure_ascii=False))
    return "[\n  " + ",\n  ".join(parts) + "\n]"


def _fmt_date(iso):
    try:
        return datetime.date.fromisoformat(iso).strftime("%b %-d")
    except ValueError:
        return iso


# ---------------------------------------------------------------------------
# Favorites (knockout-aware)
# ---------------------------------------------------------------------------

def _score_text(side, other):
    if side["score"] is None or other["score"] is None:
        return ""
    base = f"{side['score']}–{other['score']}"
    if side.get("pen") is not None and other.get("pen") is not None:
        base += f" (p {side['pen']}–{other['pen']})"
    return base


def fav_status(name, nodes):
    """Return (status_class, status_text, detail) from bracket involvement,
    or None if the team is not yet placed in a resolved tie."""
    mine = []
    for n in nodes:
        for side, opp in (("a", "b"), ("b", "a")):
            if n[side].get("name") == name and not n[side].get("tbd"):
                mine.append((n, side, opp))
    if not mine:
        return None

    live = next((m for m in mine if m[0]["state"] == "in"), None)
    completed = [m for m in mine if m[0]["state"] == "post"]
    upcoming = [m for m in mine if m[0]["state"] == "pre"]

    # Eliminated?
    for n, side, opp in completed:
        if n["winner"] and n["winner"] != side:
            r = ROUND_LABELS[n["round"]]
            sc = _score_text(n[side], n[opp])
            return ("out", "Out", f"Lost {r} {sc} v {n[opp]['name']}".rstrip())

    # Won the final?
    for n, side, opp in completed:
        if n["round"] == "F" and n["winner"] == side:
            return ("champ", "🏆 Champions", "Winners of the World Cup")

    if live:
        n, side, opp = live
        sc = _score_text(n[side], n[opp])
        return ("live", f"LIVE · {ROUND_LABELS[n['round']]}", f"{sc} v {n[opp]['name']}".strip())

    if upcoming:
        n, side, opp = min(upcoming, key=lambda m: m[0]["id"])
        when = n["when"].strftime("%a %b %-d") if n.get("when") else _fmt_date(n["date"])
        opp_name = n[opp]["name"]
        return ("alive", ROUND_LABELS[n["round"]], f"Next: v {opp_name} · {when} · {n['city']}")

    # Won latest, next round not seeded yet.
    if completed:
        n, side, opp = max(completed, key=lambda m: m[0]["id"])
        nxt = NEXT_ROUND.get(n["round"])
        nxt_txt = ROUND_LABELS.get(nxt, "next round") if nxt else "next round"
        sc = _score_text(n[side], n[opp])
        return ("alive", f"Through to {nxt_txt}", f"Beat {n[opp]['name']} {sc}".rstrip())
    return None


def fav_group_fallback(name, group_tables, team_to_group):
    """Pre-knockout context from the group table."""
    letter = team_to_group.get(name)
    if not letter or letter not in group_tables:
        return ("group", "Group stage", "")
    g = group_tables[letter]
    pos = pts = None
    for i, r in enumerate(g["rows"]):
        if r[0] == name:
            pos, pts = i + 1, r[7]
            break
    if pos is None:
        return ("group", "Group stage", "")
    final = g["status"] == "Final"
    if final and pos <= 2:
        return ("alive", "Qualified", f"Group {letter} · {ordinal(pos)} · awaiting Round of 32")
    if final and pos == 3:
        return ("group", "3rd place", f"Group {letter} · best-thirds watch")
    if final:
        return ("out", "Out", f"Eliminated in Group {letter}")
    return ("group", f"Group {letter} · {ordinal(pos)}", f"{pts} pts · group stage in progress")


def render_fav_games(name, fixtures):
    """A team's completed matches, oldest first, with result/score/opponent."""
    played = [f for f in fixtures if f["completed"] and name in (f["home"], f["away"])]
    epoch = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
    played.sort(key=lambda f: f.get("dt") or epoch)
    if not played:
        return '<div class="fc-games-empty">No games played yet.</div>'
    rows = []
    for f in played:
        is_home = f["home"] == name
        ts = f["home_score"] if is_home else f["away_score"]
        os_ = f["away_score"] if is_home else f["home_score"]
        opp = f["away"] if is_home else f["home"]
        if f["winner"]:
            res = "W" if (f["winner"] == "home") == is_home else "L"
        elif ts is not None and os_ is not None:
            res = "W" if ts > os_ else ("L" if ts < os_ else "D")
        else:
            res = "D"
        tp = f["home_pen"] if is_home else f["away_pen"]
        op = f["away_pen"] if is_home else f["home_pen"]
        pen = f" (p {tp}–{op})" if tp is not None and op is not None else ""
        if f["round"] == "group":
            tag = f"Grp {f['group']}" if f["group"] else "Grp"
        else:
            tag = ROUND_SHORT.get(f["round"], f["round"])
        score = f"{ts}–{os_}{pen}" if ts is not None and os_ is not None else "—"
        rows.append(
            f'<li class="g-{res.lower()}"><span class="g-res">{res}</span>'
            f'<span class="g-score">{score}</span>'
            f'<span class="g-opp">v {opp}</span>'
            f'<span class="g-tag">{tag}</span></li>'
        )
    return '<ul class="fc-games">' + "".join(rows) + "</ul>"


def render_team_cards(teams, group_tables, team_to_group, nodes, fixtures, flags, overrides):
    """Precompute the favorite-card HTML for every team. The browser shows
    whichever cards the visitor has picked (stored in localStorage), so all
    the card logic lives here and nothing is reimplemented in JS.

    `overrides` (from favorites.md, keyed by team name) can customise the
    flag, label, colours and note for specific teams."""
    cards = {}
    for name in teams:
        ov = overrides.get(name, {})
        flag = ov.get("flag") or flags.get(name, "")
        label = ov.get("label") or name
        c1, c2 = ov.get("colors") or FAV_DEFAULT_COLORS
        note = ov.get("note") or ""
        letter = team_to_group.get(name)
        grp_txt = f"Group {letter}" if letter else ""

        scls, stext, detail = fav_status(name, nodes) or fav_group_fallback(name, group_tables, team_to_group)
        games_html = render_fav_games(name, fixtures)
        note_html = f'<div class="note">{note}</div>' if note else ""

        cards[name] = (
            f'<details class="fav-card {scls}" data-fav="{name}" style="background:linear-gradient(135deg,{c1},{c2})">'
            f'<summary class="fc-head"><span class="flag">{flag}</span>'
            f'<span class="name">{label}</span>'
            f'<span class="grp">{grp_txt}</span>'
            f'<button class="fc-remove" data-remove="{name}" title="Remove" aria-label="Remove">×</button>'
            f'<span class="chev">▶</span></summary>'
            f'<div class="fc-body">'
            f'<div class="fc-status"><span class="pill">{stext}</span></div>'
            f'<div class="fc-detail">{detail}</div>'
            f'{games_html}'
            f'{note_html}'
            f'</div></details>'
        )
    return cards


def render_fixtures_data(fixtures, now):
    """Emit a JSON array of fixtures (in-progress + recent + next ~5 days).
    The browser groups these into Today / Tomorrow / Upcoming panes using the
    viewer's local date, so grouping must happen client-side."""
    window_end = now + datetime.timedelta(days=5)
    chosen = []
    for f in fixtures:
        if not f.get("dt"):
            continue
        dt = f["dt"]
        if f["state"] == "in" or now - datetime.timedelta(hours=4) <= dt <= window_end:
            chosen.append((dt, f))
    chosen.sort(key=lambda x: x[0])

    items = []
    for dt, f in chosen[:40]:
        if f["round"] == "group":
            tag = f"Group {f['group']}" if f["group"] else "Group stage"
        else:
            tag = ROUND_LABELS.get(f["round"], f["round"])
        items.append({
            "utc": dt.astimezone(datetime.timezone.utc).isoformat(),
            "tag": tag,
            "home": f["home"], "away": f["away"],
            "homeScore": f["home_score"], "awayScore": f["away_score"],
            "homePen": f["home_pen"], "awayPen": f["away_pen"],
            "completed": f["completed"], "state": f["state"] or "",
            "venue": f["city"] or f["venue"],
        })
    return json.dumps(items, ensure_ascii=False)


def patch_html(html, repl):
    html = re.sub(r"const groups = \[.*?\];", lambda m: f"const groups = {repl['groups']};", html, count=1, flags=re.S)
    html = re.sub(r"const bracket = \[.*?\];", lambda m: f"const bracket = {repl['bracket']};", html, count=1, flags=re.S)
    html = re.sub(r"const teamCards = \{.*?\};", lambda m: f"const teamCards = {repl['teamcards']};", html, count=1, flags=re.S)
    html = re.sub(r"const allTeams = \[.*?\];", lambda m: f"const allTeams = {repl['allteams']};", html, count=1, flags=re.S)
    html = re.sub(r"const fixturesData = \[.*?\];", lambda m: f"const fixturesData = {repl['fixtures']};", html, count=1, flags=re.S)
    html = re.sub(
        r'<b id="updated-at"[^>]*>.*?</b>',
        f'<b id="updated-at" data-utc="{repl["updated_iso"]}">{repl["timestamp"]}</b>',
        html, count=1, flags=re.S,
    )
    return html


def main():
    data = json.loads((HERE / "groups_data.json").read_text())
    groups_def = data["groups"]
    bracket_def = data["bracket"]
    team_to_group = {team: g for g, teams in groups_def.items() for team in teams}

    events = collect_events()
    stats, fixtures = parse_events(events, team_to_group)
    group_tables = build_group_tables(groups_def, stats)
    nodes = build_bracket(bracket_def, group_tables, fixtures)

    favs = parse_favorites((HERE / "favorites.md").read_text(encoding="utf-8"))
    overrides = {f["name"]: f for f in favs}
    flags = data.get("flags", {})
    all_teams = sorted(team_to_group)
    team_cards = render_team_cards(all_teams, group_tables, team_to_group, nodes, fixtures, flags, overrides)

    now = datetime.datetime.now(datetime.timezone.utc)
    repl = {
        "groups": render_groups_js(groups_def, group_tables, fixtures),
        "bracket": render_bracket_js(nodes),
        "teamcards": json.dumps(team_cards, ensure_ascii=False),
        "allteams": json.dumps(all_teams, ensure_ascii=False),
        "fixtures": render_fixtures_data(fixtures, now),
        "timestamp": now.strftime("%b %-d, %Y, %H:%M UTC") + " — auto-refreshed",
        "updated_iso": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    index_path = HERE / "index.html"
    html = index_path.read_text(encoding="utf-8")
    index_path.write_text(patch_html(html, repl), encoding="utf-8")
    print(f"Wrote {index_path} — {len(stats)} teams with group results, "
          f"{len(fixtures)} fixtures, {len(nodes)} bracket ties.")


if __name__ == "__main__":
    main()
