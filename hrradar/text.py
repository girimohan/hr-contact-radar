from __future__ import annotations

import html
import re
from functools import lru_cache

_TAG = re.compile(r"<[^>]+>")
_BLOCK = re.compile(r"</?(p|div|li|br|tr|h[1-6]|ul|ol|table|section|article)\b[^>]*>", re.I)
_SCRIPT = re.compile(r"<(script|style|noscript)\b.*?</\1>", re.I | re.S)
_WS = re.compile(r"[ \t\r\f\v]+")
_NL = re.compile(r"\s*\n\s*")


def strip_html(s: str | None, keep_lines: bool = True) -> str:
    """HTML (possibly double-escaped) to plain text. Block tags become line breaks, so a contact block
    such as "Name<br>Title<br>email" keeps its shape for the extractor."""
    if not s:
        return ""
    s = html.unescape(html.unescape(s))
    s = _SCRIPT.sub(" ", s)
    if keep_lines:
        s = _BLOCK.sub("\n", s)
    s = _TAG.sub(" ", s)
    s = _WS.sub(" ", s)
    return _NL.sub("\n", s).strip() if keep_lines else re.sub(r"\s+", " ", s).strip()


@lru_cache(maxsize=4096)
def keyword_re(keyword: str) -> re.Pattern:
    """Whole word or phrase only, so "rn" does not match "intern"."""
    body = re.escape(keyword.lower().strip()).replace(r"\ ", r"[\s\-/]+")
    return re.compile(rf"(?<![a-z0-9åäö]){body}(?![a-z0-9åäö])")


def has_keyword(text: str, keyword: str) -> bool:
    return bool(keyword_re(keyword).search(text))


def first_keyword(text: str, keywords) -> str | None:
    for k in keywords:
        if has_keyword(text, k):
            return k
    return None


_FOLD = str.maketrans({"ä": "a", "å": "a", "ö": "o", "ü": "u", "é": "e", "è": "e", "á": "a", "ø": "o", "æ": "ae",
                       "š": "s", "ž": "z", "ß": "ss"})


def ascii_fold(s: str) -> str:
    """Matti Mäkelä -> matti makela, the way Finnish addresses are spelled."""
    return s.lower().translate(_FOLD)
