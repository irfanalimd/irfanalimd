#!/usr/bin/env python3
"""Render README.md from TEMPLATE.md using live GitHub stats.

Uses only the standard library. Requires a token in GH_TOKEN (the Actions
GITHUB_TOKEN works for public data; a classic PAT with `repo` + `read:user`
also counts private contributions).
"""

import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

USERNAME = os.environ.get("GH_USERNAME", "irfanalimd")
ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "TEMPLATE.md"
README = ROOT / "README.md"
ASSETS = ROOT / "assets"

# Markup and notebook output inflate byte counts without saying much about
# what someone actually writes, so leave them out of the language breakdown.
IGNORED_LANGUAGES = {
    "Jupyter Notebook", "HTML", "CSS", "SCSS", "Sass", "Less", "Stylus", "CMake", "Makefile", "Dockerfile",
}
TOP_LANGUAGES = 6
ACTIVE_PROJECTS = 5


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
    followers { totalCount }
    pullRequests { totalCount }
    issues { totalCount }
    repositories(first: 100, after: $after, ownerAffiliations: OWNER, privacy: PUBLIC) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        isFork
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
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions
    }
  }
}
"""

LAST_YEAR_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!, $max: Int!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions
      totalPullRequestContributions
      totalIssueContributions
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
      commitContributionsByRepository(maxRepositories: $max) {
        repository { name url description isPrivate isFork }
        contributions { totalCount }
      }
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

    data = graphql(
        token,
        LAST_YEAR_QUERY,
        {
            "login": USERNAME,
            "from": iso(now - timedelta(days=365)),
            "to": iso(now),
            "max": ACTIVE_PROJECTS * 3,
        },
    )
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

    active = [
        {
            "name": c["repository"]["name"],
            "url": c["repository"]["url"],
            "description": c["repository"]["description"] or "",
            "commits": c["contributions"]["totalCount"],
        }
        for c in last_year["commitContributionsByRepository"]
        if not c["repository"]["isPrivate"] and not c["repository"]["isFork"]
    ][:ACTIVE_PROJECTS]

    return {
        "now": now,
        "years_on_github": now.year - created.year - ((now.month, now.day) < (created.month, created.day)),
        "public_repos": profile["repositories"]["totalCount"],
        "stars": sum(r["stargazerCount"] for r in repos if not r["isFork"]),
        "followers": profile["followers"]["totalCount"],
        "total_prs": profile["pullRequests"]["totalCount"],
        "total_issues": profile["issues"]["totalCount"],
        "total_commits": total_commits,
        "year_commits": last_year["totalCommitContributions"],
        "year_prs": last_year["totalPullRequestContributions"],
        "year_contributions": last_year["contributionCalendar"]["totalContributions"],
        "current_streak": current_streak,
        "longest_streak": longest_streak,
        "languages": languages,
        "active": active,
        "since": created.year,
    }


def badge(label, message, color, label_color="555", style="flat-square"):
    q = urllib.parse.quote
    return (
        f"https://img.shields.io/static/v1?style={style}"
        f"&label={q(label)}&message={q(message)}&color={q(color.lstrip('#'))}"
        f"&labelColor={q(label_color.lstrip('#'))}"
    )


CARD_THEMES = {
    # GitHub's own light/dark palettes, so the card sits naturally on the page
    "light": {"bg": "#ffffff", "border": "#d0d7de", "ink": "#1f2328", "muted": "#59636e"},
    "dark": {"bg": "#0d1117", "border": "#30363d", "ink": "#e6edf3", "muted": "#9198a1"},
}
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif"


def card_stats(s):
    streak_label = "Current streak"
    if s["longest_streak"] > s["current_streak"]:
        streak_label += f" · best {s['longest_streak']}"
    return [
        (f"{s['total_commits']:,}", "", "Commits"),
        (f"{s['year_commits']:,}", "", "In the past year"),
        (f"{s['current_streak']:,}", " days", streak_label),
        (f"{s['total_prs']:,}", "", "Pull requests"),
        (f"{s['public_repos']:,}", "", "Public repos"),
    ]


def render_card(s, theme):
    t = CARD_THEMES[theme]
    W, H, pad = 840, 164, 28
    stats = card_stats(s)
    col = (W - 2 * pad) / len(stats)
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
        f'font-family="{FONT}" role="img" aria-label="GitHub stats">',
        f'<rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="12" fill="{t["bg"]}" stroke="{t["border"]}"/>',
    ]
    for i, (value, suffix, label) in enumerate(stats):
        x = pad + i * col + (0 if i == 0 else 24)
        if i:
            sx = pad + i * col
            out.append(f'<line x1="{sx:.1f}" y1="32" x2="{sx:.1f}" y2="94" stroke="{t["border"]}"/>')
        out.append(
            f'<text x="{x:.1f}" y="68" fill="{t["ink"]}" font-size="30" font-weight="600" letter-spacing="-0.5">'
            f'{value}<tspan fill="{t["muted"]}" font-size="15" font-weight="500" letter-spacing="0">{suffix}</tspan></text>'
        )
        out.append(f'<text x="{x:.1f}" y="90" fill="{t["muted"]}" font-size="13">{label}</text>')

    out.append(f'<line x1="{pad}" y1="118" x2="{W - pad}" y2="118" stroke="{t["border"]}"/>')
    out.append(f'<text x="{pad}" y="142" fill="{t["muted"]}" font-size="12">On GitHub since {s["since"]}</text>')
    out.append(
        f'<text x="{W - pad}" y="142" fill="{t["muted"]}" font-size="12" text-anchor="end">'
        f'Updated {s["now"].strftime("%b %-d, %Y")}</text>'
    )
    out.append("</svg>")
    return "\n".join(out)


def stats_alt(s):
    return (f"{s['total_commits']:,} commits, {s['year_commits']:,} in the past year, "
            f"{s['current_streak']}-day streak, {s['total_prs']:,} pull requests, {s['public_repos']:,} public repos")


def render_languages(languages):
    total = sum(v["size"] for v in languages.values())
    if not total:
        return "_No language data yet._"
    top = sorted(languages.items(), key=lambda kv: kv[1]["size"], reverse=True)[:TOP_LANGUAGES]
    rows = ["| Language | Share |", "|:--|:--|"]
    for name, info in top:
        pct = info["size"] / total * 100
        filled = max(1, round(pct / 5))
        bar = "█" * filled + "░" * (20 - filled)
        color = info["color"] or "#888888"
        rows.append(f"| ![{name}]({badge('⠀', name, '555', color)}) | `{bar}` {pct:.1f}% |")
    return "\n".join(rows)


def render_active(active):
    if not active:
        return "_Nothing public yet this year._"
    lines = []
    for repo in active:
        desc = repo["description"]
        if len(desc) > 90:
            desc = desc[:87].rsplit(" ", 1)[0].rstrip(".,;:") + "…"
        desc = f" — {desc}" if desc else ""
        lines.append(f"- [**{repo['name']}**]({repo['url']}) · `{repo['commits']:,} commits`{desc}")
    return "\n".join(lines)


def render(template, s):
    values = {
        "TOTAL_COMMITS": f"{s['total_commits']:,}",
        "YEAR_COMMITS": f"{s['year_commits']:,}",
        "YEAR_CONTRIBUTIONS": f"{s['year_contributions']:,}",
        "YEAR_PRS": f"{s['year_prs']:,}",
        "TOTAL_PRS": f"{s['total_prs']:,}",
        "TOTAL_ISSUES": f"{s['total_issues']:,}",
        "PUBLIC_REPOS": f"{s['public_repos']:,}",
        "STARS": f"{s['stars']:,}",
        "FOLLOWERS": f"{s['followers']:,}",
        "CURRENT_STREAK": f"{s['current_streak']:,}",
        "LONGEST_STREAK": f"{s['longest_streak']:,}",
        "YEARS_ON_GITHUB": str(s["years_on_github"]),
        "LANGUAGES": render_languages(s["languages"]),
        "ACTIVE_PROJECTS": render_active(s["active"]),
        "UPDATED": s["now"].strftime("%d %b %Y"),
        "STATS_ALT": stats_alt(s),
    }
    out = template
    for key, value in values.items():
        out = out.replace("{{" + key + "}}", value)
    return out


def main():
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("Set GH_TOKEN (e.g. `export GH_TOKEN=$(gh auth token)`).")
    stats = fetch_stats(token)
    README.write_text(render(TEMPLATE.read_text(), stats))
    ASSETS.mkdir(exist_ok=True)
    for theme in CARD_THEMES:
        (ASSETS / f"stats-{theme}.svg").write_text(render_card(stats, theme))
    print(f"README updated: {stats['total_commits']:,} total commits")


if __name__ == "__main__":
    main()
