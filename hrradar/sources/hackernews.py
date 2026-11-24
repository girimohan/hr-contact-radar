"""The monthly "Ask HN: Who is hiring?" thread (public Algolia HN API).

Posts are written by the people hiring and usually end with their own email address, which makes
this the best online source of direct contacts. Kept: remote posts and posts naming a Nordic place.
"""
from __future__ import annotations

import html
import re

from ..http import FetchError, get
from ..models import Lead
from ..text import strip_html
from . import SourceResult

SEARCH = "https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=12"
ITEM = "https://hn.algolia.com/api/v1/items/{id}"
_PARA = re.compile(r"<p>|<br\s*/?>|\n", re.I)
_KEEP = re.compile(r"\bremote\b|finland|helsinki|espoo|tampere|stockholm|sweden|oslo|norway|copenhagen|denmark|nordic", re.I)
_NO_REMOTE = re.compile(r"\b(no remote|not remote|on[- ]?site only)\b", re.I)


def parse_thread(thread: dict) -> list[Lead]:
    out = []
    for c in thread.get("children") or []:
        raw = c.get("text") or ""
        if not raw or not c.get("author"):
            continue
        parts = [p for p in _PARA.split(html.unescape(raw)) if p.strip()] or [""]
        header = strip_html(parts[0], keep_lines=False)
        body = strip_html("\n".join(parts[1:]))
        # Links in HN comments are <a href="mailto:..."> or plain text; keep the hrefs so addresses survive.
        hrefs = " ".join(re.findall(r'href="mailto:([^"]+)"', raw))
        whole = f"{header}\n{body}"
        if not _KEEP.search(whole) or _NO_REMOTE.search(whole) and not re.search(r"finland|helsinki|nordic", whole, re.I):
            continue
        company = re.split(r"\s*[|•·]\s*|\s+[-–—]\s+", header, maxsplit=1)[0].strip()[:60]
        out.append(Lead(
            source="hackernews", source_id=str(c.get("id")), company=company or "HN post", title=header[:160],
            url=f"https://news.ycombinator.com/item?id={c.get('id')}", location=header,
            posted=(c.get("created_at") or "")[:10], description=f"{whole}\n{hrefs}"[:20000],
        ))
    return out


def fetch() -> SourceResult:
    res = SourceResult(name="hackernews")
    try:
        hits = (get(SEARCH) or {}).get("hits") or []
        story = next((h for h in hits if (h.get("title") or "").lower().startswith("ask hn: who is hiring")), None)
        if not story:
            res.ok, res.error = False, "no 'Who is hiring?' thread found"
            return res
        res.leads = parse_thread(get(ITEM.format(id=story["objectID"]), timeout=(6, 60)) or {})
        res.detail = story.get("title", "")
    except FetchError as e:
        res.ok, res.error = False, str(e)
    return res
