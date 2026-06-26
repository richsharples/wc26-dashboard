#!/usr/bin/env python3
"""
Deterministic FIFA World Cup 2026 dashboard refresher.

No LLM calls. Pulls match data straight from ESPN's public scoreboard JSON
feed, recomputes group standings / third-place ranking / Round of 32 slots
from raw results, and rewrites index.html in place. Designed to run inside
GitHub Actions (cron + manual workflow_dispatch) with zero Claude usage.

Usage: python3 refresh.py
Reads:  groups_data.json, index.html (as the template to patch)
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
GROUP_START = datetime.date(2026, 6, 11)
GROUP_END = datetime.date(2026, 6, 27)
USER_AGENT = "Mozilla/5.0 (wc26-dashboard refresh bot; github actions)"


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
    d = GROUP_START
    while d <= GROUP_END:
        try:
            events.extend(fetch_day(d))
        except Exception as e:
            print(f"warning: failed to fetch {d}: {e}", file=sys.stderr)
        d += datetime.timedelta(days=1)
    return events


def empty_stats():
    return {"GP": 0, "W": 0, "D": 0, "L": 0, "F": 0, "A": 0}


FAV_DEFAULT_COLORS = ("#0b2545", "#13315c")


def parse_favorites(md_text):
    """Parse favorites.md into [{name, flag, label, note, colors}, ...].

    Format: each favourite is a `## Team Name` heading followed by
    `- key: value` lines (flag, label, color, note). Everything is optional
    except the heading. Lines outside a heading (the intro/docs) are ignored.
    """
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
        group = team_to_group.get(home_name) or team_to_group.get(away_name)

        fixtures.append({
            "group": group, "home": home_name, "away": away_name,
            "home_score": home.get("score"), "away_score": away.get("score"),
            "completed": completed, "state": state,
            "venue": venue, "city": city, "date": date_iso,
        })

        if not completed:
            continue
        try:
            hs, asc = int(home.get("score", 0)), int(away.get("score", 0))
        except (TypeError, ValueError):
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
    """Return dict group_letter -> {"status": str, "rows": [[name,gp,w,d,l,f,a,pts,cls], ...]}"""
    out = {}
    for letter, teams in groups_def.items():
        rows = []
        for name in teams:
            s = stats.get(name, empty_stats())
            rows.append((name, s))
        rows.sort(key=lambda t: sort_key(t[1]))
        all_played = all(s["GP"] >= 3 for _, s in rows)
        final_rows = []
        for i, (name, s) in enumerate(rows):
            cls = "q" if i < 2 else ("bub" if i == 2 else "out")
            pts = s["W"] * 3 + s["D"]
            final_rows.append([name, s["GP"], s["W"], s["D"], s["L"], s["F"], s["A"], pts, cls])
        out[letter] = {
            "status": "Final" if all_played else None,  # None = caller fills in "N left"
            "rows": final_rows,
            "winner": final_rows[0][0] if all_played else None,
            "runnerup": final_rows[1][0] if all_played else None,
        }
    return out


def build_thirds(group_tables):
    entries = []
    for letter, g in group_tables.items():
        if len(g["rows"]) < 3:
            continue
        name, gp, w, d, l, f, a, pts, cls = g["rows"][2]
        entries.append([name, letter, gp, pts, f - a, f])
    entries.sort(key=lambda e: (-e[3], -e[4], -e[5]))
    return entries  # [name, group, gp, pts, gd, gf]


def resolve_r32(r32_def, group_tables, thirds):
    qualified_thirds = {e[0] for e in thirds[:8]}
    rows = []
    for fx in r32_def:
        a_label = resolve_slot(fx["a"], group_tables, qualified_thirds)
        b_label = resolve_slot(fx["b"], group_tables, qualified_thirds)
        rows.append({"date": fx["date"], "venue": fx["venue"], "utc": fx.get("utc", ""), "a": a_label, "b": b_label})
    return rows


def render_r32_js(r32):
    rows_js = ",\n  ".join(
        "{ date: " + json.dumps(m["date"]) +
        ", venue: " + json.dumps(m["venue"]) +
        ", utc: " + json.dumps(m["utc"]) +
        ", a: " + json.dumps(m["a"], ensure_ascii=False) +
        ", b: " + json.dumps(m["b"], ensure_ascii=False) + " }"
        for m in r32
    )
    return "[\n  " + rows_js + "\n]"


def resolve_slot(slot, group_tables, qualified_thirds):
    kind = slot["kind"]
    if kind == "seed":
        g = group_tables[slot["group"]]
        if slot["pos"] == 1 and g["winner"]:
            return f"{g['winner']} ({slot['group']}1)"
        if slot["pos"] == 2 and g["runnerup"]:
            return f"{g['runnerup']} ({slot['group']}2)"
        return f"Group {slot['group']} {'winner' if slot['pos']==1 else 'runner-up'} — TBD"
    if kind == "winner":
        g = group_tables[slot["group"]]
        return f"{g['winner']} ({slot['group']}1)" if g["winner"] else f"Group {slot['group']} winner — TBD"
    if kind == "runnerup":
        g = group_tables[slot["group"]]
        return f"{g['runnerup']} ({slot['group']}2)" if g["runnerup"] else f"Group {slot['group']} runner-up — TBD"
    if kind == "thirdpool":
        # Not auto-resolved to a specific team in v1 — needs the full FIFA
        # third-place bracket-assignment table to do safely. Left descriptive.
        return slot["label"] + " — TBD"
    return "TBD"


def group_status_text(letter, g, fixtures):
    if g["status"] == "Final":
        return "Final"
    remaining = [f for f in fixtures if f["group"] == letter and not f["completed"]]
    if not remaining:
        return "In progress"
    remaining.sort(key=lambda f: f.get("date") or "")
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


def render_favteams_js(favs):
    entries = []
    for fav in favs:
        c1 = (fav["colors"] or FAV_DEFAULT_COLORS)[0]
        entries.append(
            json.dumps(fav["name"], ensure_ascii=False) +
            ": { flag: " + json.dumps(fav["flag"], ensure_ascii=False) +
            ", color: " + json.dumps(c1) + " }"
        )
    return "{ " + ", ".join(entries) + " }"


def render_thirds_js(thirds):
    rows_js = ",\n  ".join(
        "[" + json.dumps(e[0], ensure_ascii=False) + "," + json.dumps(e[1], ensure_ascii=False) + "," + ",".join(str(x) for x in e[2:]) + "]"
        for e in thirds
    )
    return "[\n  " + rows_js + "\n]"


def render_favorites_html(favs, group_tables, team_to_group):
    cards = []
    for fav in favs:
        name = fav["name"]
        letter = team_to_group.get(name)
        gp = pts = gd = 0
        rank = None
        if letter and letter in group_tables:
            for i, r in enumerate(group_tables[letter]["rows"]):
                if r[0] == name:
                    gp, pts, gd, rank = r[1], r[7], r[5] - r[6], i + 1
                    break
        c1, c2 = fav["colors"] or FAV_DEFAULT_COLORS
        label = fav["label"] or name
        grp_txt = f" — Group {letter}" if letter else ""
        gd_txt = f"{'+' if gd > 0 else ''}{gd}"
        cards.append(
            f'<div class="fav-card" style="background:linear-gradient(135deg,{c1},{c2})">'
            f'<div><span class="flag">{fav["flag"]}</span>'
            f'<span class="name">{label}{grp_txt}</span></div>'
            f'<div class="stat-row">'
            f'<div>Played<b>{gp}</b></div><div>Pts<b>{pts}</b></div>'
            f'<div>GD<b>{gd_txt}</b></div><div>Rank<b>{ordinal(rank)}</b></div>'
            f'</div>'
            f'<div class="note">{fav["note"]}</div></div>'
        )
    return "\n".join(cards)


def render_fixtures_data(fixtures, now):
    window_end = now + datetime.timedelta(hours=60)
    upcoming = []
    for f in fixtures:
        if not f.get("date"):
            continue
        try:
            dt = datetime.datetime.fromisoformat(f["date"].replace("Z", "+00:00"))
        except ValueError:
            continue
        if f["state"] == "in" or now - datetime.timedelta(hours=4) <= dt <= window_end:
            upcoming.append((dt, f))
    upcoming.sort(key=lambda x: x[0])

    items = []
    for _dt, f in upcoming[:24]:
        items.append({
            "utc": f["date"],
            "group": f.get("group") or "",
            "home": f["home"],
            "away": f["away"],
            "homeScore": f.get("home_score"),
            "awayScore": f.get("away_score"),
            "completed": f["completed"],
            "state": f["state"] or "",
            "venue": f.get("city") or f.get("venue") or "",
        })
    return json.dumps(items, ensure_ascii=False)


def patch_html(html, groups_js, thirds_js, r32_js, fixtures_json, favorites_html, favteams_js, now):
    html = re.sub(
        r'(<section class="fav-banner">)(.*?)(</section>)',
        lambda m: m.group(1) + "\n" + favorites_html + "\n" + m.group(3),
        html, count=1, flags=re.S,
    )
    html = re.sub(r"const favTeams = \{.*?\};", lambda m: f"const favTeams = {favteams_js};", html, count=1, flags=re.S)
    html = re.sub(r"const groups = \[.*?\];", lambda m: f"const groups = {groups_js};", html, count=1, flags=re.S)
    html = re.sub(r"const thirds = \[.*?\];", lambda m: f"const thirds = {thirds_js};", html, count=1, flags=re.S)
    html = re.sub(r"const r32 = \[.*?\];", lambda m: f"const r32 = {r32_js};", html, count=1, flags=re.S)
    html = re.sub(
        r'const fixturesData = \[.*?\];',
        f'const fixturesData = {fixtures_json};',
        html, count=1, flags=re.S,
    )
    utc_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    utc_text = now.strftime("%b %-d, %Y, %H:%M UTC") + " — auto-refreshed"
    html = re.sub(
        r'<b id="updated-at"[^>]*>.*?</b>',
        f'<b id="updated-at" data-utc="{utc_iso}">{utc_text}</b>',
        html, count=1, flags=re.S,
    )
    return html


def main():
    data = json.loads((HERE / "groups_data.json").read_text())
    groups_def = data["groups"]
    team_to_group = {team: g for g, teams in groups_def.items() for team in teams}

    events = collect_events()
    stats, fixtures = parse_events(events, team_to_group)
    group_tables = build_group_tables(groups_def, stats)
    thirds = build_thirds(group_tables)
    r32 = resolve_r32(data["r32"], group_tables, thirds)

    favs = parse_favorites((HERE / "favorites.md").read_text(encoding="utf-8"))

    groups_js = render_groups_js(groups_def, group_tables, fixtures)
    thirds_js = render_thirds_js(thirds)
    r32_js = render_r32_js(r32)
    now = datetime.datetime.now(datetime.timezone.utc)
    fixtures_json = render_fixtures_data(fixtures, now)
    favorites_html = render_favorites_html(favs, group_tables, team_to_group)
    favteams_js = render_favteams_js(favs)

    index_path = HERE / "index.html"
    html = index_path.read_text(encoding="utf-8")
    new_html = patch_html(html, groups_js, thirds_js, r32_js, fixtures_json, favorites_html, favteams_js, now)
    index_path.write_text(new_html, encoding="utf-8")
    print(f"Wrote {index_path} — {len(stats)} teams with results, {len(fixtures)} fixtures parsed.")


if __name__ == "__main__":
    main()
