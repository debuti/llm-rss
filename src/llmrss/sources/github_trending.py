"""GitHub trending (HTML scraping: there is no official API)."""

from __future__ import annotations

import re

from ..normalize import clean_text
from . import Item

BASE = "https://github.com/trending"
_ROW = re.compile(r'<article class="Box-row">(.*?)</article>', re.S)
_REPO = re.compile(r'<h2[^>]*>\s*<a[^>]*href="/([^"/]+/[^"/]+)"', re.S)
_DESC = re.compile(r"<p[^>]*>(.*?)</p>", re.S)
_STARS = re.compile(r'href="/[^"]+/stargazers"[^>]*>(.*?)</a>', re.S)
_TODAY = re.compile(r"([\d,]+)\s+stars?\s+(today|this week|this month)")
_LANG = re.compile(r'itemprop="programmingLanguage">([^<]+)<')


def _int(s: str | None) -> int | None:
    if not s:
        return None
    digits = re.sub(r"[^\d]", "", clean_text(s))
    return int(digits) if digits else None


def parse(src_name: str, html: str) -> list[Item]:
    items = []
    for row in _ROW.findall(html):
        m = _REPO.search(row)
        if not m:
            continue
        repo = m.group(1)
        d = _DESC.search(row)
        desc = clean_text(d.group(1)) if d else ""
        s = _STARS.search(row)
        t = _TODAY.search(row)
        lang = _LANG.search(row)
        items.append(
            Item(
                source=src_name,
                url=f"https://github.com/{repo}",
                title=f"{repo}: {desc}" if desc else repo,
                summary=desc,
                extra={"repo": repo, "stars": _int(s.group(1)) if s else None,
                       "stars_period": _int(t.group(1)) if t else None,
                       "language": lang.group(1).strip() if lang else None},
            )
        )
    return items


def fetch(src, client) -> list[Item]:
    url = f"{BASE}/{src.language}" if src.language else BASE
    r = client.get(url, params={"since": src.since})
    r.raise_for_status()
    return parse(src.name, r.text)
