#!/usr/bin/env python3
"""Render README.md and its SVG cards from TEMPLATE.md + profile.json using live GitHub stats.

Uses only the standard library. Requires a token in GH_TOKEN (the Actions
GITHUB_TOKEN works for public data; a classic PAT with `repo` + `read:user`
also counts private contributions).

Outputs:
  README.md                      from TEMPLATE.md
  assets/header-{light,dark}.svg stats and language bar
  assets/projects/*.svg          one row per project in profile.json
"""

import json
import os
import shutil
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

USERNAME = os.environ.get("GH_USERNAME", "irfanalimd")
ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "TEMPLATE.md"
README = ROOT / "README.md"
PROFILE = ROOT / "profile.json"
ASSETS = ROOT / "assets"

# Markup and notebook output inflate byte counts without saying much about
# what someone actually writes, so leave them out of the language breakdown.
IGNORED_LANGUAGES = {
    "Jupyter Notebook", "HTML", "CSS", "SCSS", "Sass", "Less", "Stylus", "CMake", "Makefile", "Dockerfile", "Shell",
}
TOP_LANGUAGES = 6

# GitHub's own light/dark palettes, so the cards sit naturally on the page.
THEMES = {
    "light": {"bg": "#ffffff", "border": "#d0d7de", "ink": "#1f2328", "muted": "#59636e", "faint": "#818b98",
              "chip": "#f6f8fa"},
    "dark": {"bg": "#0d1117", "border": "#30363d", "ink": "#e6edf3", "muted": "#9198a1", "faint": "#7d8590",
             "chip": "#161b22"},
}
# Category accents (GitHub Primer blue / purple / orange), light and dark steps.
ACCENTS = {
    "blue": {"light": "#0969da", "dark": "#4493f8"},
    "purple": {"light": "#8250df", "dark": "#ab7df8"},
    "orange": {"light": "#bc4c00", "dark": "#f0883e"},
}

# One hand-drawn 24x24 line icon per project: strokes inherit the accent colour,
# "dots" are small filled circles (x, y, r).
ICONS = {
    "arm":     {"d": "M3 21h11 M5.5 21v-2.5h6V21 M8.5 18.5L11 8.5 M11 8.5l8 2.5 M19 11l1.2-4 M19 11l3.2 2.4",
                "dots": [(8.5, 18.5, 1.6), (11, 8.5, 1.6)]},
    "axes":    {"d": "M8 16h12 M8 16V4 M8 16l-5 5 M18 14l2 2-2 2 M6 6l2-2 2 2", "dots": [(8, 16, 1.5)]},
    "slam":    {"d": "M3 19c3-1 4-6 8-7s6-6 10-8",
                "dots": [(6, 8, 1.1), (15, 17, 1.1), (18.5, 13, 1.1), (11, 5, 1.1), (20, 19, 1.1), (3, 12, 1.1)]},
    "tree":    {"d": "M4 20l5-5 M9 15l2-6 M9 15l6 1 M11 9l-4-4 M11 9l6-3 M15 16l5-3 M15 16l1 5",
                "dots": [(20, 4, 1.7), (4, 20, 1.4)]},
    "sfm":     {"d": "M3 9.5h4v5H3z M7 10.5l4-2.5v8l-4-2.5",
                "dots": [(15, 6, 1.1), (18.5, 9.5, 1.1), (16, 13, 1.1), (20.5, 14.5, 1.1), (14.5, 18, 1.1), (19, 19.5, 1.1)]},
    "grid":    {"d": "M4 4h4.5v4.5H4z M9.75 4h4.5v4.5h-4.5z M15.5 4H20v4.5h-4.5z M4 9.75h4.5v4.5H4z "
                     "M15.5 9.75H20v4.5h-4.5z M4 15.5h4.5V20H4z M9.75 15.5h4.5V20h-4.5z M15.5 15.5H20V20h-4.5z",
                "fill": "M9.75 9.75h4.5v4.5h-4.5z"},
    "scale":   {"d": "M12 4v16 M7.5 20h9 M5 7.5h14 M5 7.5l-2.5 6h5z M19 7.5l-2.5 6h5z", "dots": [(12, 4, 1.3)]},
    "caption": {"d": "M3 4h13v10H3z M5 12l3.5-3.5 2.5 2.5 2-2 2 2 M3 18h18 M3 21h11", "dots": [(12.5, 7, 1.2)]},
    "weave":   {"d": "M2.5 8c3.2 0 3.2 4 6.4 4s3.2-4 6.4-4 3.2 4 6.4 4 M2.5 16c3.2 0 3.2-4 6.4-4s3.2 4 6.4 4 3.2-4 6.4-4 "
                     "M7 3.5v17 M17 3.5v17"},
    "video":   {"d": "M3 4.5h18v12H3z M8 20.5h8", "fill": "M10.5 7.5v6l5-3z"},
    "rewind":  {"d": "M4.5 12a7.5 7.5 0 1 0 2.2-5.3 M4.5 3.5v3.7h3.7", "fill": "M10.5 9v6l4.5-3z"},
    "chart":   {"d": "M4 3.5V20h16.5 M7.5 15.5l3.5-4 3 2.5 5-6", "dots": [(19, 8, 1.6)]},
}

FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif"


# ---------------------------------------------------------------------------
# GitHub data
# ---------------------------------------------------------------------------
def graphql(token, query, variables=None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=body,
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": f"{USERNAME}-readme-stats",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.load(resp)
    if payload.get("errors"):
        raise RuntimeError(json.dumps(payload["errors"], indent=2))
    return payload["data"]


PROFILE_QUERY = """
query($login: String!, $after: String) {
  user(login: $login) {
    createdAt
    pullRequests { totalCount }
    repositories(first: 100, after: $after, ownerAffiliations: OWNER, privacy: PUBLIC) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        name
        url
        isFork
        pushedAt
        stargazerCount
        languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name color } }
        }
      }
    }
  }
}
"""

YEAR_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) { totalCommitContributions }
  }
}
"""

LAST_YEAR_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions
      contributionCalendar { weeks { contributionDays { date contributionCount } } }
    }
  }
}
"""


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_stats(token):
    now = datetime.now(timezone.utc)

    repos, after, profile = [], None, None
    while True:
        data = graphql(token, PROFILE_QUERY, {"login": USERNAME, "after": after})
        profile = data["user"]
        page = profile["repositories"]
        repos.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]

    created = datetime.fromisoformat(profile["createdAt"].replace("Z", "+00:00"))

    # contributionsCollection spans at most one year, so walk calendar years.
    total_commits = 0
    for year in range(created.year, now.year + 1):
        start = max(created, datetime(year, 1, 1, tzinfo=timezone.utc))
        end = min(now, datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc))
        data = graphql(token, YEAR_QUERY, {"login": USERNAME, "from": iso(start), "to": iso(end)})
        total_commits += data["user"]["contributionsCollection"]["totalCommitContributions"]

    data = graphql(token, LAST_YEAR_QUERY,
                   {"login": USERNAME, "from": iso(now - timedelta(days=365)), "to": iso(now)})
    last_year = data["user"]["contributionsCollection"]

    languages = {}
    for repo in repos:
        if repo["isFork"]:
            continue
        for edge in repo["languages"]["edges"]:
            name = edge["node"]["name"]
            if name in IGNORED_LANGUAGES:
                continue
            entry = languages.setdefault(name, {"size": 0, "color": edge["node"]["color"]})
            entry["size"] += edge["size"]

    days = [d for w in last_year["contributionCalendar"]["weeks"] for d in w["contributionDays"]]
    current_streak, longest_streak, run = 0, 0, 0
    for day in days:
        run = run + 1 if day["contributionCount"] > 0 else 0
        longest_streak = max(longest_streak, run)
    # Today not having a contribution yet shouldn't break the current streak.
    tail = days[:-1] if days and days[-1]["contributionCount"] == 0 else days
    for day in reversed(tail):
        if day["contributionCount"] == 0:
            break
        current_streak += 1

    return {
        "now": now,
        "since": created.year,
        "public_repos": profile["repositories"]["totalCount"],
        "total_prs": profile["pullRequests"]["totalCount"],
        "total_commits": total_commits,
        "year_commits": last_year["totalCommitContributions"],
        "current_streak": current_streak,
        "longest_streak": longest_streak,
        "languages": languages,
        "repos": {r["name"]: r for r in repos},
    }


# ---------------------------------------------------------------------------
# SVG helpers
# ---------------------------------------------------------------------------
def text_width(s, size, bold=False):
    """Rough rendered width of `s` in a system sans. Errs a little wide."""
    w = 0.0
    for ch in s:
        if ch == " ":
            w += 0.28
        elif ch in "iljI.,:;'|!()[]/":
            w += 0.30
        elif ch in "frt-*":
            w += 0.40
        elif ch in "mwMW@":
            w += 0.88
        elif ch.isupper():
            w += 0.68
        elif ch.isdigit():
            w += 0.58
        else:
            w += 0.55
    return w * size * (1.06 if bold else 1.0)


def wrap(text, size, max_width, max_lines):
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if text_width(trial, size) <= max_width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    if len(lines) > max_lines:
        print(f"warning: tagline too long, truncating: {text!r}", file=sys.stderr)
        lines = lines[:max_lines]
        while text_width(lines[-1] + "…", size) > max_width:
            lines[-1] = lines[-1].rsplit(" ", 1)[0]
        lines[-1] = lines[-1].rstrip(".,;:") + "…"
    return lines


def svg_open(w, h, t, label):
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'font-family="{FONT}" role="img" aria-label="{escape(label)}">',
        f'<rect x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" rx="12" fill="{t["bg"]}" stroke="{t["border"]}"/>',
    ]


def lang_color(color, theme):
    color = color or "#8b949e"
    if theme == "dark":  # very dark linguist colours (e.g. C) vanish on the dark page
        r, g, b = (int(color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
        if 0.2126 * r + 0.7152 * g + 0.0722 * b < 90:
            return "#8b949e"
    return color


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------
def render_header(s, cfg, theme):
    t = THEMES[theme]
    W, H, pad = 840, 152, 28
    out = svg_open(W, H, t, "GitHub stats")

    streak_label = "Day streak"
    if s["longest_streak"] > s["current_streak"]:
        streak_label += f" · best {s['longest_streak']}"
    stats = [
        (f"{s['total_commits']:,}", "Commits"),
        (f"{s['year_commits']:,}", "In the past year"),
        (f"{s['current_streak']:,}", streak_label),
        (f"{s['total_prs']:,}", "Pull requests"),
        (f"{s['public_repos']:,}", "Public repos"),
    ]
    col = (W - 2 * pad) / len(stats)
    for i, (value, label) in enumerate(stats):
        x = pad + i * col + (0 if i == 0 else 22)
        if i:
            out.append(f'<line x1="{pad + i * col:.1f}" y1="24" x2="{pad + i * col:.1f}" y2="70" '
                       f'stroke="{t["border"]}"/>')
        out.append(f'<text x="{x:.1f}" y="50" fill="{t["ink"]}" font-size="24" font-weight="600" '
                   f'letter-spacing="-0.3">{value}</text>')
        out.append(f'<text x="{x:.1f}" y="70" fill="{t["muted"]}" font-size="12.5">{escape(label)}</text>')

    # language bar: one rounded track, segments separated by 2px of background
    langs = sorted(s["languages"].items(), key=lambda kv: kv[1]["size"], reverse=True)[:TOP_LANGUAGES]
    total = sum(v["size"] for _, v in langs) or 1
    bar_y, bar_h, x = 96, 8, float(pad)
    span = W - 2 * pad - 2 * (len(langs) - 1)
    out.append(f'<clipPath id="bar"><rect x="{pad}" y="{bar_y}" width="{W - 2 * pad}" height="{bar_h}" rx="4"/></clipPath>')
    out.append('<g clip-path="url(#bar)">')
    for name, info in langs:
        w = max(3.0, info["size"] / total * span)
        out.append(f'<rect x="{x:.1f}" y="{bar_y}" width="{w:.1f}" height="{bar_h}" '
                   f'fill="{lang_color(info["color"], theme)}"><title>{escape(name)}</title></rect>')
        x += w + 2
    out.append("</g>")
    x = float(pad)
    for name, info in langs:
        share = info["size"] / total
        pct = f"{share * 100:.0f}%" if share >= 0.01 else "<1%"
        out.append(f'<circle cx="{x + 4:.1f}" cy="126" r="4" fill="{lang_color(info["color"], theme)}"/>')
        out.append(f'<text x="{x + 14:.1f}" y="130" font-size="12" fill="{t["ink"]}">{escape(name)}'
                   f'<tspan fill="{t["faint"]}"> {escape(pct)}</tspan></text>')
        x += 14 + text_width(f"{name} {pct}", 12) + 18
    out.append(f'<text x="{W - pad}" y="130" font-size="12" fill="{t["faint"]}" text-anchor="end">'
               f'Since {s["since"]} · updated {s["now"].strftime("%b %-d, %Y")}</text>')
    out.append("</svg>")
    return "\n".join(out)


def render_icon(name, color, x, y, scale=1.0):
    icon = ICONS.get(name, {"d": "M5 5h14v14H5z"})
    out = [f'<g transform="translate({x} {y}) scale({scale})">',
           f'<path d="{icon["d"]}" fill="none" stroke="{color}" stroke-width="1.6" '
           f'stroke-linecap="round" stroke-linejoin="round"/>']
    if icon.get("fill"):
        out.append(f'<path d="{icon["fill"]}" fill="{color}"/>')
    out += [f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{color}"/>' for cx, cy, r in icon.get("dots", [])]
    out.append("</g>")
    return "".join(out)


def render_project(item, group, theme):
    """An app-icon style tile: tinted icon square with the short name underneath. No frame."""
    t = THEMES[theme]
    accent = ACCENTS[group.get("color", "blue")][theme]
    W, H, tile = 132, 96, 54
    x0 = (W - tile) / 2
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
           f'font-family="{FONT}" role="img" aria-label="{escape(item['name'] + ': ' + item['summary'])}">',
           f'<rect x="{x0}" y="4" width="{tile}" height="{tile}" rx="15" fill="{accent}" '
           f'fill-opacity="{0.10 if theme == "light" else 0.16}"/>',
           render_icon(item.get("icon", ""), accent, x0 + 10, 14, 34 / 24),
           f'<text x="{W / 2}" y="84" fill="{t["ink"]}" font-size="13" font-weight="600" '
           f'text-anchor="middle">{escape(item["name"])}</text>',
           "</svg>"]
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------
def picture(base, alt):
    return (f'<picture><source media="(prefers-color-scheme: dark)" srcset="{base}-dark.svg">'
            f'<img src="{base}-light.svg" width="100%" alt="{escape(alt)}"></picture>')


def render_projects(cfg, s):
    """Rows of six clickable icon tiles; the one-line summary shows on hover."""
    cards = []
    for group in cfg["projects"]:
        for item in group["items"]:
            repo = s["repos"].get(item["repo"]) or {}
            url = repo.get("url") or f"https://github.com/{USERNAME}/{item['repo']}"
            base = f"assets/projects/{item['repo']}"
            cards.append(f'<a href="{url}"><picture><source media="(prefers-color-scheme: dark)" '
                         f'srcset="{base}-dark.svg"><img src="{base}-light.svg" width="15.6%" '
                         f'alt="{escape(item["name"])}: {escape(item["summary"])}" '
                         f'title="{escape(item["summary"])}"></picture></a>')
    # six tiles per markdown line, so each row stays together however line breaks are rendered
    return "\n".join(" ".join(cards[i:i + 6]) for i in range(0, len(cards), 6))


def header_alt(cfg, s):
    return (f"{s['total_commits']:,} commits, {s['year_commits']:,} in the "
            f"past year, {s['current_streak']}-day streak, {s['total_prs']:,} pull requests, "
            f"{s['public_repos']:,} public repos.")


def main():
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("Set GH_TOKEN (e.g. `export GH_TOKEN=$(gh auth token)`).")
    cfg = json.loads(PROFILE.read_text())
    s = fetch_stats(token)

    ASSETS.mkdir(exist_ok=True)
    for theme in THEMES:
        (ASSETS / f"header-{theme}.svg").write_text(render_header(s, cfg, theme))
    shutil.rmtree(ASSETS / "projects", ignore_errors=True)  # rebuilt from scratch every run
    (ASSETS / "projects").mkdir()
    for theme in THEMES:
        for group in cfg["projects"]:
            for item in group["items"]:
                (ASSETS / "projects" / f"{item['repo']}-{theme}.svg").write_text(render_project(item, group, theme))

    values = {
        "HEADER": picture("assets/header", header_alt(cfg, s)),
        "PROJECTS": render_projects(cfg, s),
        "UPDATED": s["now"].strftime("%d %b %Y"),
    }
    out = TEMPLATE.read_text()
    for key, value in values.items():
        out = out.replace("{{" + key + "}}", value)
    README.write_text(out)
    print(f"README updated: {s['total_commits']:,} total commits")


if __name__ == "__main__":
    main()
