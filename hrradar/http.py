from __future__ import annotations

import threading
import time
import urllib.robotparser
from urllib.parse import urlsplit

import requests

USER_AGENT = "hr-contact-radar/0.1 (personal job-search tool; one visit per site per day)"
_local = threading.local()
_robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
_robots_lock = threading.Lock()


def session() -> requests.Session:
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
        _local.s.headers["User-Agent"] = USER_AGENT
        _local.s.headers["Accept"] = "application/json, text/html;q=0.9, application/rss+xml;q=0.8, */*;q=0.5"
        _local.s.headers["Accept-Language"] = "en, fi;q=0.9, sv;q=0.8"
    return _local.s


class FetchError(Exception):
    pass


def get(url: str, *, as_json: bool = True, tries: int = 3, timeout=(6, 25), headers: dict | None = None,
        params: dict | None = None):
    """GET with polite retries. Returns None for 404/410; raises FetchError otherwise."""
    last = "no attempt"
    for attempt in range(tries):
        try:
            r = session().get(url, timeout=timeout, headers=headers, params=params)
        except requests.RequestException as e:
            last = type(e).__name__
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code in (404, 410):
            return None
        if r.status_code == 429 or r.status_code >= 500:
            last = f"HTTP {r.status_code}"
            time.sleep(4 * (attempt + 1))
            continue
        if not r.ok:
            raise FetchError(f"HTTP {r.status_code} for {url}")
        if not as_json:
            return r.text
        try:
            return r.json()
        except ValueError as e:
            raise FetchError(f"bad JSON from {url}") from e
    raise FetchError(f"{last} for {url}")


def allowed(url: str) -> bool:
    """robots.txt check for company websites. A site whose robots.txt cannot be read is treated as allowing."""
    parts = urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}"
    with _robots_lock:
        if base not in _robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = session().get(base + "/robots.txt", timeout=(5, 10))
                rp.parse(r.text.splitlines() if r.ok else [])
            except requests.RequestException:
                rp.parse([])
            _robots[base] = rp
        rp = _robots[base]
    return rp is None or rp.can_fetch(USER_AGENT, url)


def page(url: str, timeout=(6, 15), max_bytes: int = 1_500_000) -> tuple[str, str] | None:
    """Fetch an HTML page if robots.txt allows. Returns (final_url, html) or None."""
    if not allowed(url):
        return None
    try:
        r = session().get(url, timeout=timeout, allow_redirects=True, stream=True)
        if not r.ok or "html" not in r.headers.get("Content-Type", "html"):
            return None
        body = r.raw.read(max_bytes, decode_content=True)
        enc = r.encoding or "utf-8"
        return r.url, body.decode(enc, errors="replace")
    except (requests.RequestException, LookupError, OSError):
        return None
