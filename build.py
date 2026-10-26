#!/usr/bin/env python3
"""
birb-report — a tiny static generator for birb.report. No dependencies.
Reads markdown from posts/ and pages/ and writes a static site to docs/.
Run it with:  python3 build.py

How posting works:
  - Make a file in posts/ named by date, e.g.  posts/2026-10-26.md
  - (Optional) open with a frontmatter block:

        ---
        title: the post title
        dek: a one-line subtitle shown under the title
        ---

    A lone "# Title" first line works as the title too. No title at all is
    fine — the entry number and date stand in for it.
  - Run this script. The docs/ folder is the finished website.

Multiple posts in one day? Add a suffix:  posts/2026-10-26-two.md

Pages (about, elsewhere) live in pages/<name>.md and become /<name>.html.

Preview drafts locally without publishing:
    python3 build.py --drafts     # writes preview/ (gitignored), includes drafts/
"""

import html
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")

ROOT = Path(__file__).parent
POSTS_DIR = ROOT / "posts"
DRAFTS_DIR = ROOT / "drafts"
PAGES_DIR = ROOT / "pages"
STYLE_SRC = ROOT / "style.css"

SITE_TITLE = "BIRB REPORT"
TAGLINE = ""
DOMAIN = "birb.report"

# Buttondown username (buttondown.com/<this>). Empty = the subscribe box
# shows "subscriptions opening soon" and points at the RSS feed instead.
BUTTONDOWN = ""

NAV = [("index.html", "entries"), ("about.html", "about"), ("elsewhere.html", "elsewhere")]


# ----------------------------- markdown ------------------------------------ #
# Same deliberately small subset as foid: headings, paragraphs, bold/italic,
# links, inline code, blockquotes, lists, rules.

_INLINE_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD = re.compile(r"\*\*([^*]+)\*\*|__([^_]+)__")
_ITALIC = re.compile(r"(?<![*_])\*(?!\s)([^*]+?)\*(?![*])|(?<![*_])_(?!\s)([^_]+?)_(?![_])")


def _inline(text: str) -> str:
    text = html.escape(text, quote=False)
    codes: list[str] = []

    def stash(m: "re.Match[str]") -> str:
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"

    text = _INLINE_CODE.sub(stash, text)
    text = _LINK.sub(r'<a href="\2">\1</a>', text)
    text = _BOLD.sub(lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", text)
    text = _ITALIC.sub(lambda m: f"<em>{m.group(1) or m.group(2)}</em>", text)
    text = re.sub(r"\x00(\d+)\x00", lambda m: f"<code>{codes[int(m.group(1))]}</code>", text)
    return text


def md_to_html(src: str) -> str:
    lines = src.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)

    def flush_para(buf: list[str]) -> None:
        if buf:
            # A single newline is a real line break, as on foid.
            out.append(f"<p>{'<br>'.join(_inline(l.strip()) for l in buf)}</p>")
            buf.clear()

    para: list[str] = []
    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            flush_para(para)
            i += 1
            continue

        m = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if m:
            flush_para(para)
            level = min(len(m.group(1)) + 1, 5)  # h1 in a post renders as h2
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            i += 1
            continue

        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", stripped):
            flush_para(para)
            out.append("<hr>")
            i += 1
            continue

        if stripped.startswith(">"):
            flush_para(para)
            quote: list[str] = []
            while i < n and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append(f"<blockquote><p>{_inline(' '.join(quote))}</p></blockquote>")
            continue

        if re.match(r"^[-*+]\s+", stripped):
            flush_para(para)
            items: list[str] = []
            while i < n and re.match(r"^[-*+]\s+", lines[i].strip()):
                item = re.sub(r"^[-*+]\s+", "", lines[i].strip())
                items.append(f"<li>{_inline(item)}</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue

        if re.match(r"^\d+\.\s+", stripped):
            flush_para(para)
            items = []
            while i < n and re.match(r"^\d+\.\s+", lines[i].strip()):
                item = re.sub(r"^\d+\.\s+", "", lines[i].strip())
                items.append(f"<li>{_inline(item)}</li>")
                i += 1
            out.append("<ol>" + "".join(items) + "</ol>")
            continue

        para.append(line)
        i += 1

    flush_para(para)
    return "\n".join(out)


# ------------------------------ posts -------------------------------------- #

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_DATE_IN_NAME = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


def split_frontmatter(src: str) -> tuple[dict[str, str], str]:
    meta: dict[str, str] = {}
    fm = _FRONTMATTER.match(src)
    if fm:
        for line in fm.group(1).splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                meta[key.strip().lower()] = val.strip()
        src = src[fm.end():]
    return meta, src.strip()


def first_commit_time(path: Path) -> datetime | None:
    """Author time of the first commit that touched the file, in New York.
    Requires full git history — the Actions checkout uses fetch-depth: 0."""
    try:
        out = subprocess.run(
            ["git", "log", "--follow", "--format=%aI", "--", str(path)],
            capture_output=True, text=True, cwd=ROOT,
        ).stdout.strip().splitlines()
    except OSError:
        out = []
    return datetime.fromisoformat(out[-1]).astimezone(TZ) if out else None


def parse_post(path: Path) -> dict | None:
    m = _DATE_IN_NAME.match(path.stem)
    if not m:
        return None
    post_date = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    meta, body = split_frontmatter(path.read_text(encoding="utf-8"))

    title = meta.get("title", "")
    first = body.split("\n", 1)[0].strip()
    if first.startswith("# "):
        title = title or first[2:].strip()
        body = body.split("\n", 1)[1].strip() if "\n" in body else ""

    # Publish time: `time:` frontmatter wins, else the first commit,
    # else now (the post-push rebuild then pins it to the real commit).
    if meta.get("time"):
        stamp_time = meta["time"]
        when = datetime.combine(post_date, time(12), TZ)
    else:
        when = first_commit_time(path) or datetime.now(TZ)
        stamp_time = when.strftime("%-I:%M %p")
        if when.date() != post_date:
            when = datetime.combine(post_date, time(12), TZ)

    return {
        "slug": path.stem,
        "date": post_date,
        "time": stamp_time,
        "when": when,
        "title": title,
        "dek": meta.get("dek", ""),
        "html": md_to_html(body),
        "excerpt": excerpt(body),
    }


def excerpt(body: str, limit: int = 220) -> str:
    """First paragraph as plain text, trimmed — for the entries list."""
    para = re.split(r"\n\s*\n", body.strip(), maxsplit=1)[0] if body.strip() else ""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", para)
    text = re.sub(r"[*_`>#]", "", text).replace("\n", " ").strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return text


# ------------------------------ pages -------------------------------------- #

def page(title: str, body: str, depth: int = 0, current: str = "") -> str:
    prefix = "../" * depth
    here = ' aria-current="page"'
    nav = " ".join(
        f'<a href="{prefix}{href}"{here if href == current else ""}>{label}</a>'
        for href, label in NAV
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,600;1,6..72,400&display=swap">
<link rel="stylesheet" href="{prefix}style.css">
<link rel="alternate" type="application/rss+xml" title="{html.escape(SITE_TITLE)}" href="https://{DOMAIN}/feed.xml">
</head>
<body>
<header class="masthead">
<a class="wordmark" href="{prefix}index.html">{html.escape(SITE_TITLE)}</a>
<p class="tagline">{html.escape(TAGLINE)}</p>
<nav>{nav}</nav>
</header>
<main>
{body}
</main>
<footer>
<p><a href="{prefix}feed.xml">rss</a> · <a href="https://foid.report">foid</a> · <a href="https://fringe.report">fringe</a> · <a href="https://intervals.report">intervals</a></p>
</footer>
</body>
</html>
"""


def subscribe_box(prefix: str = "") -> str:
    if BUTTONDOWN:
        form = (
            f'<form class="subscribe-form" action="https://buttondown.com/api/emails/embed-subscribe/{BUTTONDOWN}" '
            f'method="post" target="popupwindow" '
            f"onsubmit=\"window.open('https://buttondown.com/{BUTTONDOWN}', 'popupwindow')\">"
            '<label class="sr-only" for="bd-email">email address</label>'
            '<input type="email" name="email" id="bd-email" placeholder="you@somewhere" required>'
            '<button type="submit">subscribe</button>'
            "</form>"
        )
    else:
        form = f'<p class="subscribe-soon">subscriptions opening soon — <a href="{prefix}feed.xml">rss</a> works now.</p>'
    return (
        '<aside class="subscribe">\n'
        '<p class="label">sighting alerts</p>\n'
        "<p>new entries, sent to your inbox when they land. nothing else.</p>\n"
        f"{form}\n"
        "</aside>"
    )


def entry_no(n: int) -> str:
    return f"{n:03d}"


def display_title(post: dict) -> str:
    return post["title"] or f"Entry {entry_no(post['no'])}"


def post_article(post: dict, newer: dict | None, older: dict | None) -> str:
    stamp = post["date"].strftime("%B %-d, %Y")
    if post.get("time"):
        stamp += f" · {post['time']}"
    dek = f'<p class="dek">{_inline(post["dek"])}</p>\n' if post["dek"] else ""
    links = []
    if older:
        links.append(f'<a rel="prev" href="{older["slug"]}.html">← {html.escape(display_title(older))}</a>')
    if newer:
        links.append(f'<a rel="next" href="{newer["slug"]}.html">{html.escape(display_title(newer))} →</a>')
    pager = f'<nav class="pager">{"".join(links)}</nav>' if links else ""
    return (
        '<article class="post">\n'
        '<header class="specimen">\n'
        f'<p class="label">entry no. {entry_no(post["no"])}</p>\n'
        f"<h1>{html.escape(display_title(post))}</h1>\n"
        f"{dek}"
        f'<p class="label">observed {stamp}</p>\n'
        "</header>\n"
        f'<div class="prose">\n{post["html"]}\n</div>\n'
        "</article>\n"
        f"{subscribe_box('../')}\n"
        f"{pager}"
    )


def entries_list(posts: list[dict]) -> str:
    if not posts:
        return '<p class="label empty">no sightings yet.</p>'
    rows = []
    for p in posts:
        dek = f'<p class="dek">{_inline(p["dek"])}</p>' if p["dek"] else ""
        ex = f'<p class="excerpt">{html.escape(p["excerpt"])}</p>' if p["excerpt"] else ""
        rows.append(
            '<li class="entry">\n'
            f'<p class="label"><span>no. {entry_no(p["no"])}</span> <span>{p["date"].strftime("%b %-d, %Y").lower()}</span></p>\n'
            f'<h2><a href="posts/{p["slug"]}.html">{html.escape(display_title(p))}</a></h2>\n'
            f"{dek}{ex}\n"
            "</li>"
        )
    return '<ol class="entries" reversed>\n' + "\n".join(rows) + "\n</ol>"


def rss_feed(posts: list[dict]) -> str:
    """RSS 2.0 feed, newest first, full post HTML in each item.
    Buttondown's RSS-to-email reads this to send new posts."""

    def rfc822(dt: datetime) -> str:
        return dt.strftime("%a, %d %b %Y %H:%M:%S %z")

    items = []
    for p in posts:
        link = f"https://{DOMAIN}/posts/{p['slug']}.html"
        body = (f"<p><em>{_inline(p['dek'])}</em></p>\n" if p["dek"] else "") + p["html"]
        items.append(
            "<item>\n"
            f"<title>{html.escape(display_title(p))}</title>\n"
            f"<link>{link}</link>\n"
            f'<guid isPermaLink="true">{link}</guid>\n'
            f"<pubDate>{rfc822(p['when'])}</pubDate>\n"
            f"<description>{html.escape(body)}</description>\n"
            "</item>"
        )
    last_build = rfc822(posts[0]["when"]) if posts else rfc822(datetime.now(TZ))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
        "<channel>\n"
        f"<title>{html.escape(SITE_TITLE)}</title>\n"
        f"<link>https://{DOMAIN}/</link>\n"
        f"<description>{html.escape(TAGLINE)}</description>\n"
        f"<lastBuildDate>{last_build}</lastBuildDate>\n"
        f'<atom:link href="https://{DOMAIN}/feed.xml" rel="self" type="application/rss+xml"/>\n'
        + "\n".join(items)
        + "\n</channel>\n</rss>\n"
    )


def build(include_drafts: bool = False) -> None:
    site_dir = ROOT / ("preview" if include_drafts else "docs")  # Pages serves /docs on main
    sources = list(POSTS_DIR.glob("*.md"))
    if include_drafts:
        sources += DRAFTS_DIR.glob("*.md")
    posts = sorted(filter(None, map(parse_post, sources)), key=lambda p: (p["date"], p["slug"]))
    for i, post in enumerate(posts, start=1):  # oldest is entry 001
        post["no"] = i
    posts.reverse()  # newest first for display

    if site_dir.exists():
        shutil.rmtree(site_dir)
    (site_dir / "posts").mkdir(parents=True)

    shutil.copy(STYLE_SRC, site_dir / "style.css")
    (site_dir / "CNAME").write_text(DOMAIN + "\n")

    home = subscribe_box() + "\n" + entries_list(posts)
    (site_dir / "index.html").write_text(page(SITE_TITLE, home, current="index.html"), encoding="utf-8")
    (site_dir / "feed.xml").write_text(rss_feed(posts), encoding="utf-8")

    for i, p in enumerate(posts):
        newer = posts[i - 1] if i > 0 else None
        older = posts[i + 1] if i + 1 < len(posts) else None
        (site_dir / "posts" / f"{p['slug']}.html").write_text(
            page(f"{display_title(p)} — {SITE_TITLE}", post_article(p, newer, older), depth=1),
            encoding="utf-8",
        )

    for src in sorted(PAGES_DIR.glob("*.md")):
        meta, body = split_frontmatter(src.read_text(encoding="utf-8"))
        title = meta.get("title", src.stem)
        content = f'<article class="post page-{src.stem}">\n<h1>{html.escape(title)}</h1>\n<div class="prose">\n{md_to_html(body)}\n</div>\n</article>'
        (site_dir / f"{src.stem}.html").write_text(
            page(f"{title} — {SITE_TITLE}", content, current=f"{src.stem}.html"), encoding="utf-8"
        )

    print(f"built {len(posts)} post(s) → {site_dir}")


if __name__ == "__main__":
    build(include_drafts="--drafts" in sys.argv)
