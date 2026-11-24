"""When a posting names nobody: find the company's website, read its careers / contact / team pages,
and pick out recruiting people and mailboxes. Also learns the company's email format from the
addresses it publishes, so a named person without an address can get a (clearly marked) guess.

Polite by design: robots.txt is respected, at most `max_pages` pages per site, results are cached
for `refresh_days`, and only work contact details the company itself published are kept.
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from . import extract
from .http import FetchError, get, page
from .models import Contact, Lead
from .text import ascii_fold, strip_html

# Hosts that belong to job platforms, not to the employer.
JOB_HOSTS = re.compile(
    r"(?:^|\.)(?:greenhouse\.io|lever\.co|ashbyhq\.com|smartrecruiters\.com|teamtailor\.com|recruitee\.com|"
    r"workable\.com|personio\.(?:de|com)|jobylon\.com|varbi\.com|reachmee\.com|hr-manager\.net|talentadore\.com|"
    r"laura\.fi|rekrytointi\.com|kuntarekry\.fi|valtiolle\.fi|tyomarkkinatori\.fi|duunitori\.fi|oikotie\.fi|"
    r"linkedin\.com|arbetsformedlingen\.se|jobtechdev\.se|nav\.no|jobnet\.dk|ycombinator\.com|remoteok\.com|"
    r"remotive\.com|weworkremotely\.com|himalayas\.app|jobicy\.com|arbeitnow\.com|workingnomads\.com|"
    r"google\.com|forms\.gle|typeform\.com|notion\.site|notion\.so|bamboohr\.com|workday\.com|myworkdayjobs\.com|"
    r"successfactors\.\w+|icims\.com|taleo\.net|jobvite\.com|breezy\.hr|homerun\.co|join\.com|gmail\.com|"
    r"outlook\.com|hotmail\.com|yahoo\.com|protonmail\.com|proton\.me|icloud\.com|github\.com|wellfound\.com|"
    r"emply\.com|hrmanager\.\w+|webcruiter\.\w+|jobteaser\.com|mynewterm\.com|careers-page\.com|zohorecruit\.\w+)$",
    re.I)

CAREER_LINK = re.compile(
    r"career|jobs?\b|join|work-with-us|open-positions|rekry|rekrytointi|ura\b|ty[oö]paik|avoimet|jobba|karri[aä]r|"
    r"karriere|ledige|stillinger|lediga", re.I)
CONTACT_LINK = re.compile(r"contact|yhteystied|ota-yhteytt|kontakt|om-oss|about|meist|yritys|company|team|tiimi|"
                          r"people|henkil[oö]st|staff|medarbetare|ansatte|organisation|management|johto", re.I)
TLDS = ["fi", "com", "io", "se", "no", "dk", "ai", "eu", "tech", "app", "co"]
_SLUG = re.compile(r"[^a-z0-9]+")
_SUFFIX = re.compile(r"\b(?:oyj?|ab|abp|as|asa|a/s|aps|ltd|limited|inc|llc|gmbh|group|finland|suomi|sweden|"
                     r"norway|denmark|technologies|technology|software|solutions)\b\.?", re.I)
HIRING_LEADER = re.compile(r"founder|perustaja|grundare|\bceo\b|\bcto\b|toimitusjohtaja|\bvd\b|daglig leder|"
                           r"head of (?:engineering|technology|data|ai|product)|engineering manager|vp engineering|"
                           r"teknologiajohtaja|kehitysjohtaja|chief technology", re.I)


def registrable(host: str) -> str:
    """careers.acme.fi -> acme.fi; www.acme.co.uk -> acme.co.uk."""
    host = (host or "").lower().split(":")[0].strip(".")
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "ac", "gov", "net"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def employer_domain(url_or_email: str) -> str:
    if not url_or_email:
        return ""
    host = url_or_email.split("@", 1)[1] if "@" in url_or_email and "//" not in url_or_email else urlsplit(
        url_or_email if "//" in url_or_email else "https://" + url_or_email).hostname or ""
    dom = registrable(host)
    return "" if not dom or JOB_HOSTS.search(dom) else dom


def company_slug(name: str) -> str:
    return _SLUG.sub("", ascii_fold(_SUFFIX.sub(" ", name or "")))


def _brand_words(name: str) -> list[str]:
    return [w for w in _SLUG.split(ascii_fold(_SUFFIX.sub(" ", name or ""))) if len(w) >= 3]


class Enricher:
    def __init__(self, cache_dir: Path, private_dir: Path, today: dt.date, max_sites: int = 120,
                 max_pages: int = 6, refresh_days: int = 14, workers: int = 6, domain_overrides: dict | None = None):
        self.today = today
        self.max_sites, self.max_pages, self.refresh_days, self.workers = max_sites, max_pages, refresh_days, workers
        self.domains_path = cache_dir / "domains.json"      # company -> domain (no personal data)
        self.mx_path = cache_dir / "mx.json"                # domain -> accepts mail (no personal data)
        self.sites_path = private_dir / "site_cache.json"   # domain -> people found (personal data: stays private)
        self.domains = _load(self.domains_path)
        self.mx = _load(self.mx_path)
        self.sites = _load(self.sites_path)
        self.overrides = {company_slug(k): v for k, v in (domain_overrides or {}).items()}
        self.stats = Counter()

    # ------------------------------------------------------------------ domains
    def resolve_domain(self, lead: Lead) -> str:
        slug = company_slug(lead.company)
        if slug in self.overrides:
            return self.overrides[slug]
        for cand in (lead.company_url, lead.apply_email, *[c.email for c in lead.contacts], lead.url):
            d = employer_domain(cand)
            if d:
                if slug:
                    self.domains[slug] = d
                return d
        if not slug or len(slug) < 3:
            return ""
        if slug in self.domains:
            return self.domains[slug]
        return ""   # guessed later, in bulk, by guess_domains()

    def guess_domains(self, companies: list[str]) -> None:
        """Try acme.fi, acme.com, ... and keep the first whose homepage mentions the company's name."""
        todo = [c for c in dict.fromkeys(companies) if company_slug(c) and company_slug(c) not in self.domains
                and company_slug(c) not in self.overrides and len(company_slug(c)) >= 3]
        todo = todo[: self.max_sites]

        def one(company: str) -> tuple[str, str]:
            slug, words = company_slug(company), _brand_words(company)
            for tld in TLDS:
                dom = f"{slug}.{tld}"
                if not self.accepts_mail(dom):
                    continue
                got = page(f"https://{dom}/", timeout=(4, 8), max_bytes=300_000)
                if not got:
                    continue
                final, html = got
                head = ascii_fold(strip_html(html[:60000], keep_lines=False))[:6000]
                if words and all(w in head for w in words[:2]):
                    return slug, registrable(urlsplit(final).hostname or dom)
            return slug, ""

        with cf.ThreadPoolExecutor(self.workers) as ex:
            for slug, dom in ex.map(one, todo):
                self.domains[slug] = dom
                self.stats["domains_guessed" if dom else "domains_not_found"] += 1

    def domain_for(self, company: str) -> str:
        slug = company_slug(company)
        return self.overrides.get(slug) or self.domains.get(slug, "")

    # ------------------------------------------------------------------ mail check
    def accepts_mail(self, domain: str) -> bool:
        """MX lookup over DNS-over-HTTPS. Only the domain name is sent, never a person's address."""
        if not domain:
            return False
        if domain in self.mx:
            return bool(self.mx[domain])
        ok = False
        try:
            data = get("https://cloudflare-dns.com/dns-query", params={"name": domain, "type": "MX"},
                       headers={"Accept": "application/dns-json"}, tries=2, timeout=(4, 8)) or {}
            ok = data.get("Status") == 0 and any(a.get("type") == 15 for a in data.get("Answer") or [])
        except FetchError:
            return True   # lookup failed: do not punish the address for our network trouble
        self.mx[domain] = ok
        return ok

    # ------------------------------------------------------------------ site crawl
    def site_contacts(self, domain: str) -> dict:
        """{"contacts": [...], "format": "." | "" , "pages": [...]} for a domain, cached."""
        hit = self.sites.get(domain)
        if hit and hit.get("date", "") >= (self.today - dt.timedelta(days=self.refresh_days)).isoformat():
            self.stats["sites_cached"] += 1
            return hit
        self.stats["sites_read"] += 1
        res = crawl(domain, self.max_pages)
        res["date"] = self.today.isoformat()
        self.sites[domain] = res
        return res

    def crawl_many(self, domains: list[str]) -> None:
        todo = [d for d in dict.fromkeys(domains) if d][: self.max_sites]
        with cf.ThreadPoolExecutor(self.workers) as ex:
            list(ex.map(self.site_contacts, todo))

    def save(self) -> None:
        for path, data in ((self.domains_path, self.domains), (self.mx_path, self.mx), (self.sites_path, self.sites)):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------------- crawling one site
_HREF = re.compile(r"<a\b[^>]*href=[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
_MAILTO = re.compile(r"mailto:([^\"'?>\s]+)", re.I)


def crawl(domain: str, max_pages: int = 6) -> dict:
    home = None
    for base in (f"https://{domain}/", f"https://www.{domain}/"):
        home = page(base)
        if home:
            break
    if not home:
        return {"contacts": [], "format": "", "pages": []}
    home_url, home_html = home
    links: list[tuple[int, str]] = []
    for href, label in _HREF.findall(home_html):
        url = urljoin(home_url, href.strip())
        host = urlsplit(url).hostname or ""
        if registrable(host) != domain or url.rstrip("/") == home_url.rstrip("/"):
            continue
        hay = f"{url} {strip_html(label, keep_lines=False)}"
        if CAREER_LINK.search(hay):
            links.append((0, url))
        elif CONTACT_LINK.search(hay):
            links.append((1, url))
    # Common paths, in case the homepage hides its menu behind JavaScript.
    for p in ("/careers", "/jobs", "/rekry", "/contact", "/yhteystiedot", "/kontakt", "/about", "/team"):
        links.append((2, urljoin(home_url, p)))
    ordered = [u for _, u in sorted(dict.fromkeys(links), key=lambda x: x[0])]
    seen, pages = set(), [(home_url, home_html)]
    for url in ordered:
        if len(pages) > max_pages:
            break
        key = url.rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        got = page(url)
        if got:
            pages.append(got)
    return people_on_pages(pages, domain)


def people_on_pages(pages: list[tuple[str, str]], domain: str) -> dict:
    """Recruiting people, hiring leaders and recruiting mailboxes on a company's own pages."""
    found: dict[str, Contact] = {}
    person_pairs: list[tuple[str, str]] = []   # (name, email) used to learn the address format
    for url, html in pages:
        text = strip_html(html)
        mails = {m.lower().strip(".") for m in _MAILTO.findall(html)}
        emails = {e for e, _ in extract.find_emails(text)} | {m for m in mails if "@" in m}
        low = text.lower()
        # Each address's context starts after the previous address, so one person's title is not read as the next one's.
        ends = sorted(m.end() for m in extract.EMAIL_RE.finditer(low))
        for email in emails:
            if extract.mailbox_kind(email) == "ignore" or registrable(email.split("@")[1]) != domain:
                continue
            pos = low.find(email)
            prev = max((e for e in ends if e <= pos), default=0)
            ctx = text[max(prev, pos - 220):pos + 80] if pos >= 0 else ""
            name, role = _person_near(ctx, email)
            kind = extract.mailbox_kind(email)
            if name:
                person_pairs.append((name, email))
            if kind == "recruiting":
                c = Contact(email=email, role="recruiting mailbox", kind="generic", evidence=url)
            elif kind == "general":
                c = Contact(email=email, role="general mailbox", kind="generic", evidence=url)
            elif role and (extract.HR_ROLE.search(role) or HIRING_LEADER.search(role)):
                c = Contact(name=name, email=email, role=role, kind="company_page", evidence=url)
            else:
                continue   # someone in sales or support: not who reads applications
            if email not in found:
                found[email] = c
    fmt = learn_format(person_pairs)
    ranked = sorted(found.values(), key=lambda c: (0 if c.kind == "company_page" and extract.HR_ROLE.search(c.role) else
                                                   1 if c.kind == "company_page" else
                                                   2 if c.role == "recruiting mailbox" else 3))
    from dataclasses import asdict
    return {"contacts": [asdict(c) for c in ranked[:8]], "format": fmt, "pages": [u for u, _ in pages]}


def _person_near(ctx: str, email: str) -> tuple[str, str]:
    """The name (matching the address) and job title written near an address on a team page."""
    best = ("", "")
    for m in extract.NAME_RE.finditer(ctx):
        cand = re.sub(r"\s+", " ", m.group(1))
        if not extract._is_name(cand, set()):
            continue
        if extract.email_matches_name(email, cand):
            return cand, extract._role_near(ctx, m.start(), m.end()) or _role_in(ctx)
        best = best if best[0] else (cand, "")
    if best[0] and extract.mailbox_kind(email) == "person":
        return best[0], _role_in(ctx)
    return "", _role_in(ctx) if extract.mailbox_kind(email) == "person" else ""


def _role_in(ctx: str) -> str:
    m = extract.HR_ROLE.search(ctx) or HIRING_LEADER.search(ctx)
    if not m:
        return ""
    ls = ctx.rfind("\n", 0, m.start()) + 1          # the line the title is on, cut to the words around it
    le = ctx.find("\n", m.end())
    line = ctx[max(ls, m.start() - 30):min(le if le >= 0 else len(ctx), m.end() + 30)]
    return re.sub(r"\s+", " ", line).strip(" ,|-–")[:60]


def learn_format(pairs: list[tuple[str, str]]) -> str:
    """From (name, address) pairs on a site, the separator its addresses use: ".", "_", "-", "" or none known."""
    votes = Counter()
    for name, email in pairs:
        parts = [re.sub(r"[^a-z-]", "", p) for p in ascii_fold(name).split()]
        if len(parts) < 2:
            continue
        local = email.split("@")[0]
        for sep in (".", "_", "-", ""):
            if local == f"{parts[0]}{sep}{parts[-1]}":
                votes[sep] += 1
    return votes.most_common(1)[0][0] if votes else ""


def contacts_from_cache(entry: dict) -> list[Contact]:
    fields = Contact.__dataclass_fields__
    return [Contact(**{k: v for k, v in c.items() if k in fields}) for c in entry.get("contacts", [])]
