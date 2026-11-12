"""URL canonicalization and title hashing for dedup."""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PREFIXES = ("utm_", "mc_", "pk_")
_TRACKING_KEYS = {"fbclid", "gclid", "ref", "ref_src", "source", "share", "igshid", "si"}
_ARXIV_RE = re.compile(r"^/(?:abs|pdf|html)/([^/?#]+?)(?:v\d+)?(?:\.pdf)?/?$")


def canonical_url(url: str) -> str:
    url = url.strip()
    parts = urlsplit(url)
    scheme = "https" if parts.scheme in ("http", "https", "") else parts.scheme
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"
    path = parts.path or "/"

    if host in ("arxiv.org", "export.arxiv.org"):
        host = "arxiv.org"
        m = _ARXIV_RE.match(path)
        if m:
            path = f"/abs/{m.group(1)}"

    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith(_TRACKING_PREFIXES) and k.lower() not in _TRACKING_KEYS
    ]
    query.sort()
    return urlunsplit((scheme, host, path, urlencode(query), ""))


def url_id(url: str) -> str:
    return hashlib.sha256(canonical_url(url).encode()).hexdigest()[:16]


def normalize_title(title: str) -> str:
    t = html.unescape(title)
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"^(show|ask|launch|tell) hn:\s*", "", t)
    t = re.sub(r"\s*[\[(](pdf|video|\d{4})[\])]\s*$", "", t)
    t = re.sub(r"[^\w\s]", " ", t)
    return " ".join(t.split())


def title_hash(title: str) -> str:
    return hashlib.sha256(normalize_title(title).encode()).hexdigest()[:16]


def clean_text(s: str | None, limit: int = 2000) -> str:
    """Strip HTML tags and collapse whitespace."""
    if not s:
        return ""
    s = re.sub(r"<(script|style)\b.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = " ".join(html.unescape(s).split())
    return s[:limit]
