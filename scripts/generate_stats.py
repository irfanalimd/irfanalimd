#!/usr/bin/env python3
"""Render README.md and its SVG cards from TEMPLATE.md + profile.json using live GitHub stats.

Uses only the standard library. Requires a token in GH_TOKEN (the Actions
GITHUB_TOKEN works for public data; a classic PAT with `repo` + `read:user`
also counts private contributions).

Outputs:
  README.md                      from TEMPLATE.md
  assets/header-{light,dark}.svg name, stats and language bar
  assets/stack-{light,dark}.svg  tech stack
"""

import json
import os
import re
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
    W, H, pad = 840, 252, 28
    out = svg_open(W, H, t, f"{cfg['name']}: GitHub stats")
    out.append(f'<text x="{pad}" y="52" fill="{t["ink"]}" font-size="26" font-weight="600" '
               f'letter-spacing="-0.4">{escape(cfg["name"])}</text>')
    out.append(f'<text x="{pad}" y="78" fill="{t["muted"]}" font-size="14">{escape(cfg["tagline"])}</text>')
    out.append(f'<line x1="{pad}" y1="102" x2="{W - pad}" y2="102" stroke="{t["border"]}"/>')

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
            out.append(f'<line x1="{pad + i * col:.1f}" y1="124" x2="{pad + i * col:.1f}" y2="170" '
                       f'stroke="{t["border"]}"/>')
        out.append(f'<text x="{x:.1f}" y="150" fill="{t["ink"]}" font-size="24" font-weight="600" '
                   f'letter-spacing="-0.3">{value}</text>')
        out.append(f'<text x="{x:.1f}" y="170" fill="{t["muted"]}" font-size="12.5">{escape(label)}</text>')

    # language bar: one rounded track, segments separated by 2px of background
    langs = sorted(s["languages"].items(), key=lambda kv: kv[1]["size"], reverse=True)[:TOP_LANGUAGES]
    total = sum(v["size"] for _, v in langs) or 1
    bar_y, bar_h, x = 196, 8, float(pad)
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
        out.append(f'<circle cx="{x + 4:.1f}" cy="226" r="4" fill="{lang_color(info["color"], theme)}"/>')
        out.append(f'<text x="{x + 14:.1f}" y="230" font-size="12" fill="{t["ink"]}">{escape(name)}'
                   f'<tspan fill="{t["faint"]}"> {escape(pct)}</tspan></text>')
        x += 14 + text_width(f"{name} {pct}", 12) + 18
    out.append(f'<text x="{W - pad}" y="230" font-size="12" fill="{t["faint"]}" text-anchor="end">'
               f'Since {s["since"]} · updated {s["now"].strftime("%b %-d, %Y")}</text>')
    out.append("</svg>")
    return "\n".join(out)


def render_stack(cfg, theme):
    t = THEMES[theme]
    W, pad, label_w, chip_h, gap, size = 840, 28, 150, 28, 8, 12.5
    rows, y = [], 28
    for group in cfg["stack"]:
        x, row, first = pad + label_w, [], True
        for item in group["items"]:
            w = text_width(item, size) + 26
            if x + w > W - pad and row:  # wrap onto another line within the same group
                rows.append((group["label"] if first else "", y, row))
                first = False
                y += chip_h + gap
                x, row = pad + label_w, []
            row.append((item, x, w))
            x += w + gap
        rows.append((group["label"] if first else "", y, row))
        y += chip_h + 14
    H = y + 14
    out = svg_open(W, H, t, "Tech stack: " + "; ".join(
        f"{g['label']}: {', '.join(g['items'])}" for g in cfg["stack"]))
    for label, ry, row in rows:
        if label:
            out.append(f'<text x="{pad}" y="{ry + 18.5}" fill="{t["muted"]}" font-size="13">{escape(label)}</text>')
        for item, cx, w in row:
            out.append(f'<rect x="{cx:.1f}" y="{ry}" width="{w:.1f}" height="{chip_h}" rx="{chip_h / 2}" '
                       f'fill="{t["chip"]}" stroke="{t["border"]}"/>')
            out.append(f'<text x="{cx + w / 2:.1f}" y="{ry + 18.5}" fill="{t["ink"]}" font-size="{size}" '
                       f'text-anchor="middle">{escape(item)}</text>')
    out.append("</svg>")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------
def picture(base, alt):
    return (f'<picture><source media="(prefers-color-scheme: dark)" srcset="{base}-dark.svg">'
            f'<img src="{base}-light.svg" width="100%" alt="{escape(alt)}"></picture>')


def md_text(text):
    return re.sub(r"([*_\[\]`])", r"\\\1", text)


def render_projects(cfg, s):
    """One short paragraph per group: the group name, then its projects as plain links.
    Each project's one-liner is the link's hover title."""
    blocks = []
    for group in cfg["projects"]:
        links = []
        for item in group["items"]:
            repo = s["repos"].get(item["repo"]) or {}
            url = repo.get("url") or f"https://github.com/{USERNAME}/{item['repo']}"
            title = item["tagline"].replace('"', "'")
            links.append(f'[{md_text(item["name"])}]({url} "{title}")')
        blocks.append(f"**{group['group']}**  \n" + " · ".join(links))
    return "\n\n".join(blocks)


def header_alt(cfg, s):
    return (f"{cfg['name']}. {cfg['tagline']}. {s['total_commits']:,} commits, {s['year_commits']:,} in the "
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
        (ASSETS / f"stack-{theme}.svg").write_text(render_stack(cfg, theme))
    shutil.rmtree(ASSETS / "projects", ignore_errors=True)  # previous design's project cards

    values = {
        "HEADER": picture("assets/header", header_alt(cfg, s)),
        "PROJECTS": render_projects(cfg, s),
        "STACK": picture("assets/stack", "Tech stack"),
        "UPDATED": s["now"].strftime("%d %b %Y"),
    }
    out = TEMPLATE.read_text()
    for key, value in values.items():
        out = out.replace("{{" + key + "}}", value)
    README.write_text(out)
    print(f"README updated: {s['total_commits']:,} total commits")


if __name__ == "__main__":
    main()
