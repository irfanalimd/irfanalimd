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
    }


def badge(label, message, color, label_color="555", style="flat-square"):
    q = urllib.parse.quote
    return (
        f"https://img.shields.io/static/v1?style={style}"
        f"&label={q(label)}&message={q(message)}&color={q(color.lstrip('#'))}"
        f"&labelColor={q(label_color.lstrip('#'))}"
    )


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
        "TOTAL_COMMITS_BADGE": badge("total commits", f"{s['total_commits']:,}", "1f6feb", "0d1117", "for-the-badge"),
        "LANGUAGES": render_languages(s["languages"]),
        "ACTIVE_PROJECTS": render_active(s["active"]),
        "UPDATED": s["now"].strftime("%d %b %Y"),
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
    print(f"README updated: {stats['total_commits']:,} total commits")


if __name__ == "__main__":
    main()
