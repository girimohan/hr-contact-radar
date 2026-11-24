"""Nordic sources whose data names the contact person.

Public employment services (official, documented APIs):
  jobtech     Sweden, Arbetsförmedlingen JobSearch API. No key. `application_contacts` per ad.
  nav         Norway, NAV pam-stilling-feed. Public token. `contactList` per ad. NAV's terms say contact
              data must be deleted when an ad goes inactive; the local store does exactly that.
Nordic ATS platforms (public feeds, one per company; list the companies in data/companies.json):
  hrmanager   Talentech HR-manager. Project leader name, email and phone in the feed.
  teamtailor  Job page names the recruiter ("Kontakt" block).
  recruitee   Per-job application inbox (`mailbox_email`).
  laura       Finnish ATS (rekrytointi.com). Full ad text, contacts parsed from it.
  varbi       Swedish public-sector ATS. Full ad text, contacts parsed from it.
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict
from email.utils import parsedate_to_datetime
from pathlib import Path

from .. import match
from ..http import FetchError, get, page
from ..models import Contact, Lead
from ..text import strip_html
from . import SourceResult

WORKERS = 6
DEFAULT_QUERIES = ["python", "machine learning", "AI engineer", "LLM", "data engineer", "backend", "fullstack",
                   "software engineer", "utvecklare", "utvikler", "udvikler"]


def _none(v) -> str:
    return "" if v in (None, "None", "null") else str(v).strip()


def _title_matches(title: str, cfg: dict) -> bool:
    return bool(match.match_role(Lead(source="", source_id="", company="", title=title, url=""), cfg))


# ---------------------------------------------------------------------------- Sweden: JobTech
def parse_jobtech(hit: dict) -> Lead:
    emp = hit.get("employer") or {}
    addr = hit.get("workplace_address") or {}
    details = hit.get("application_details") or {}
    desc = hit.get("description") or {}
    model = _none((hit.get("workplace_model") or {}).get("label"))
    place = ", ".join(filter(None, [_none(addr.get("city")) or _none(addr.get("municipality")), _none(addr.get("country")) or "Sverige"]))
    contacts = []
    for c in hit.get("application_contacts") or []:
        name, email, phone = _none(c.get("name")), _none(c.get("email")).lower(), _none(c.get("telephone"))
        if name or email:
            contacts.append(Contact(name=name, email=email, phone=phone,
                                    role=_none(c.get("contact_type")) or _none(c.get("description")),
                                    kind="ats_field", evidence="contact person listed in the Arbetsförmedlingen ad"))
    ref = _none(details.get("reference"))
    country = "se" if (_none(addr.get("country")) or "Sverige").lower() in ("sverige", "sweden") else ""
    return Lead(
        source="jobtech", source_id=_none(hit.get("id")), company=_none(emp.get("name")) or _none(emp.get("workplace")),
        title=_none(hit.get("headline")), url=_none(hit.get("webpage_url")),
        location=place + (f"; {model}" if model else ""), country=country,
        posted=_none(hit.get("publication_date"))[:10], description=_none(desc.get("text"))[:20000],
        employment_type=_none((hit.get("employment_type") or {}).get("label")), company_url=_none(emp.get("url")),
        apply_email=_none(details.get("email")) or _none(emp.get("email")), apply_url=_none(details.get("url")),
        ats_hint="teamtailor" if ref.startswith("teamtailor") else "", contacts=contacts,
    )


def fetch_jobtech(src: dict, cfg: dict, _slugs, limit: int | None = None) -> SourceResult:
    res = SourceResult(name="jobtech")
    since = (dt.datetime.now() - dt.timedelta(days=src.get("days", 30))).strftime("%Y-%m-%dT%H:%M:%S")
    queries = src.get("queries") or DEFAULT_QUERIES
    pages = 1 if limit else src.get("pages_per_query", 2)
    seen, errors = set(), []
    for q in queries:
        for p in range(pages):
            try:
                data = get("https://jobsearch.api.jobtechdev.se/search",
                           params={"q": q, "limit": 100, "offset": p * 100, "published-after": since}) or {}
            except FetchError as e:
                errors.append(str(e))
                break
            hits = data.get("hits") or []
            for h in hits:
                if _none(h.get("id")) not in seen:
                    seen.add(_none(h.get("id")))
                    res.leads.append(parse_jobtech(h))
            if len(hits) < 100:
                break
    if errors and not res.leads:
        res.ok, res.error = False, errors[0]
    res.detail = f"{len(queries)} searches, ads from the last {src.get('days', 30)} days"
    return res


# ---------------------------------------------------------------------------- Norway: NAV
NAV = "https://pam-stilling-feed.nav.no"
_JWT = re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+")


def nav_token(src: dict) -> str:
    tok = os.environ.get("NAV_FEED_TOKEN") or src.get("token") or ""
    if tok:
        return tok
    text = get(f"{NAV}/api/publicToken", as_json=False) or ""
    m = _JWT.search(text)
    if not m:
        raise FetchError("could not read NAV's public token")
    return m.group(0)


def parse_nav(entry: dict) -> Lead | None:
    ad = entry.get("ad_content") or {}
    if not ad:
        return None
    emp = ad.get("employer") or {}
    locs = ad.get("workLocations") or []
    place = "; ".join(filter(None, (", ".join(filter(None, [_none(l.get("city")) or _none(l.get("municipal")),
                                                            _none(l.get("country")) or "Norge"])) for l in locs))) or "Norge"
    contacts = [Contact(name=_none(c.get("name")), email=_none(c.get("email")).lower(), phone=_none(c.get("phone")),
                        role=_none(c.get("title")) or _none(c.get("role")), kind="ats_field",
                        evidence="contact person listed in the NAV ad")
                for c in ad.get("contactList") or [] if _none(c.get("name")) or _none(c.get("email"))]
    remote = re.search(r"hjemmekontor|remote|fjernarbeid", json.dumps(ad.get("remote") or ""), re.I)
    return Lead(
        source="nav", source_id=_none(ad.get("uuid") or entry.get("uuid")), company=_none(emp.get("name")),
        title=_none(ad.get("jobtitle")) or _none(ad.get("title")), url=_none(ad.get("link")) or _none(ad.get("sourceurl")),
        location=place + ("; Remote" if remote else ""), country="no", posted=_none(ad.get("published"))[:10],
        description=strip_html(ad.get("description"))[:20000], employment_type=_none(ad.get("engagementtype")),
        company_url=_none(emp.get("homepage")), apply_email=_none(ad.get("applicationEmail")),
        apply_url=_none(ad.get("applicationUrl")), contacts=contacts,
    )


def fetch_nav(src: dict, cfg: dict, _slugs, limit: int | None = None) -> SourceResult:
    """Page forward through NAV's feed from where the last run stopped; keep a local store of matching ads."""
    res = SourceResult(name="nav")
    root = Path(cfg.get("_root", "."))
    cursor_path, store_path = root / "data" / "nav_cursor.json", root / "private" / "nav_store.json"
    store = _read(store_path)
    cursor = _read(cursor_path).get("next") or ""
    try:
        headers = {"Authorization": f"Bearer {nav_token(src)}"}
    except FetchError as e:
        res.ok, res.error = False, str(e)
        return res
    first = {}
    if not cursor:
        # The feed starts in 2023; on the first run start a few weeks back instead.
        from email.utils import format_datetime
        start = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=src.get("start_days", 21))
        cursor, first = "/api/v1/feed", {"If-Modified-Since": format_datetime(start, usegmt=True)}
    wanted, pages, seen_items = [], 0, 0
    max_pages = 2 if limit else src.get("max_pages", 60)
    try:
        while pages < max_pages:
            data = get(NAV + cursor, headers={**headers, **(first if pages == 0 else {})}) or {}
            items = data.get("items") or []
            pages += 1
            seen_items += len(items)
            for it in items:
                fe = it.get("_feed_entry") or {}
                uid = fe.get("uuid") or it.get("id")
                if fe.get("status") != "ACTIVE":
                    store.pop(uid, None)        # NAV: delete contact data when an ad goes inactive
                elif _title_matches(fe.get("title") or it.get("title") or "", cfg):
                    wanted.append(uid)
            nxt = data.get("next_url")
            if not items or not nxt or nxt == cursor:
                break
            cursor = nxt
    except FetchError as e:
        if not pages:
            res.ok, res.error = False, str(e)
            return res
        res.detail = f"stopped early: {e}; "

    def one(uid: str):
        try:
            return uid, parse_nav(get(f"{NAV}/api/v1/feedentry/{uid}", headers=headers) or {})
        except FetchError:
            return uid, None

    with cf.ThreadPoolExecutor(WORKERS) as ex:
        for uid, lead in ex.map(one, list(dict.fromkeys(wanted))[: src.get("max_details", 400)]):
            if lead and lead.title:
                store[uid] = asdict(lead)
    fields = Lead.__dataclass_fields__
    for d in store.values():
        lead = Lead(**{k: v for k, v in d.items() if k in fields and k != "contacts"})
        lead.contacts = [Contact(**c) for c in d.get("contacts", [])]
        res.leads.append(lead)
    res.detail += f"{seen_items:,} feed entries in {pages} pages, {len(wanted)} matching titles, {len(store)} ads stored"
    _write(cursor_path, {"next": cursor, "date": dt.date.today().isoformat()})
    _write(store_path, store)
    return res


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")


# ---------------------------------------------------------------------------- per-company ATS feeds
def _per_company(name: str, slugs: list[str], one, limit: int | None) -> SourceResult:
    slugs = list(dict.fromkeys(slugs))[:limit] if limit else list(dict.fromkeys(slugs))
    res = SourceResult(name=name)
    if not slugs:
        res.detail = "no companies listed in data/companies.json"
        return res
    failed = []
    with cf.ThreadPoolExecutor(WORKERS) as ex:
        for slug, leads, err in ex.map(lambda s: _safe(one, s), slugs):
            if err:
                failed.append(f"{slug} ({err})")
            else:
                res.leads.extend(leads)
    res.detail = f"{len(slugs) - len(failed)} of {len(slugs)} companies answered" + (f"; failed: {', '.join(failed[:4])}" if failed else "")
    if len(failed) > len(slugs) / 2:
        res.ok, res.error = False, "most feeds failed, e.g. " + "; ".join(failed[:3])
    return res


def _safe(one, slug):
    try:
        return slug, one(slug), ""
    except (FetchError, ET.ParseError, ValueError, KeyError, TypeError) as e:
        return slug, [], str(e)[:80] or type(e).__name__


def _ms_date(v) -> str:
    m = re.search(r"/Date\((\d+)", str(v or ""))
    return dt.datetime.fromtimestamp(int(m.group(1)) / 1000, dt.timezone.utc).date().isoformat() if m else ""


def hrmanager_one(alias: str) -> list[Lead]:
    data = get(f"https://recruiter-api.hr-manager.net/jobportal.svc/{alias}/positionlist/json/") or {}
    out = []
    for it in data.get("Items") or []:
        users = it.get("Users") or {}
        people = [users.get("ProjectLeader") or {}] + [p for p in users.get("ProjectParticipants") or [] if p.get("IsContactPerson")]
        contacts = []
        for p in people:
            name = " ".join(filter(None, [_none(p.get("FirstName")).title(), _none(p.get("LastName"))]))
            email = _none(p.get("Email")).lower()
            if name or email:
                contacts.append(Contact(name=name, email=email, phone=_none(p.get("PhoneMobile")) or _none(p.get("Phone")),
                                        role=_none(p.get("Title")) or "project leader for this hire", kind="ats_field",
                                        evidence="hiring project leader in the company's HR-manager feed"))
        tree = it.get("DepartmentTree") or {}
        place = _none(it.get("WorkPlace")) or ", ".join(filter(None, [_none(tree.get("City")), _none(tree.get("Country"))]))
        out.append(Lead(
            source="hrmanager", source_id=f"{alias}:{it.get('Id')}", company=_none(it.get("CustomerName")) or alias,
            title=_none(it.get("Name")), url=_none(it.get("AdvertisementUrlSecure")) or _none(it.get("AdvertisementUrl")),
            location=place, posted=_ms_date(it.get("Published") or it.get("Created")),
            description=strip_html(it.get("ShortDescription"))[:20000], contacts=contacts,
        ))
    return out


def _rss_items(xml_text: str):
    root = ET.fromstring(xml_text)
    for item in root.iter("item"):
        yield {c.tag.split("}")[-1]: (c.text or "") for c in item}, item


def _rss_date(v: str) -> str:
    try:
        return parsedate_to_datetime(v).date().isoformat()
    except (TypeError, ValueError):
        return ""


def teamtailor_one(slug: str) -> list[Lead]:
    base = f"https://{slug}" if "." in slug else f"https://{slug}.teamtailor.com"
    text = get(f"{base}/jobs.rss", as_json=False)
    out = []
    for f, item in _rss_items(text or "<rss/>"):
        locs = [(el.text or "") for el in item.iter() if el.tag.split("}")[-1] in ("location", "city", "country", "name")]
        remote = (f.get("remoteStatus") or "").lower()
        company = (f.get("companyName") or slug.split(".")[0]).replace("-", " ").title()
        out.append(Lead(
            source="teamtailor", source_id=f"{slug}:{f.get('guid') or f.get('link')}", company=company,
            title=f.get("title", "").strip(), url=f.get("link", ""),
            location="; ".join(dict.fromkeys(l.strip() for l in locs if l.strip())) + ("; Remote" if remote in ("fully", "remote") else ""),
            posted=_rss_date(f.get("pubDate", "")), description=strip_html(f.get("description"))[:20000],
            company_url=base, ats_hint="teamtailor",
        ))
    return out


def recruitee_one(slug: str) -> list[Lead]:
    data = get(f"https://{slug}.recruitee.com/api/offers/") or {}
    out = []
    for o in data.get("offers") or []:
        box = _none(o.get("mailbox_email")).lower()
        out.append(Lead(
            source="recruitee", source_id=f"{slug}:{o.get('id')}", company=_none(o.get("company_name")) or slug,
            title=_none(o.get("title")), url=_none(o.get("careers_url")),
            location=", ".join(filter(None, [_none(o.get("city")), _none(o.get("country"))])) + ("; Remote" if o.get("remote") else ""),
            posted=_none(o.get("published_at"))[:10],
            description=strip_html(f"{o.get('description') or ''}\n{o.get('requirements') or ''}")[:20000],
            contacts=[Contact(email=box, role="this job's application inbox", kind="apply_email",
                              evidence="the job's own inbox in the company's Recruitee board")] if box else [],
        ))
    return out


def laura_one(slug: str) -> list[Lead]:
    host = slug if "." in slug else f"{slug}.rekrytointi.com"
    out = []
    for jgid in (1, 0):
        text = get(f"https://{host}/paikat/?o=A_RSS&jgid={jgid}", as_json=False)
        for f, _ in _rss_items(text or "<rss/>"):
            out.append(Lead(
                source="laura", source_id=f"{host}:{f.get('guid') or f.get('link')}",
                company=slug.split(".")[0].replace("-", " ").title(), title=f.get("title", "").strip(),
                url=f.get("link", ""), location="Finland", country="fi", posted=_rss_date(f.get("pubDate", "")),
                # <laura:description> (the full ad) comes after the plain <description> (just dates), so it wins here.
                description=strip_html(f.get("description"))[:20000],
            ))
        if out:
            break
    return out


def varbi_one(slug: str) -> list[Lead]:
    host = slug if "." in slug else f"{slug}.varbi.com"
    text = get(f"https://{host}/what:rssfeed/", as_json=False)
    return [Lead(source="varbi", source_id=f"{host}:{f.get('guid') or f.get('link')}",
                 company=slug.split(".")[0].upper() if len(slug.split(".")[0]) <= 4 else slug.split(".")[0].title(),
                 title=f.get("title", "").strip(), url=f.get("link", ""), location="Sverige", country="se",
                 posted=_rss_date(f.get("pubDate", "")), description=strip_html(f.get("description"))[:20000])
            for f, _ in _rss_items(text or "<rss/>")]


# ---------------------------------------------------------------------------- Teamtailor recruiter block
_TT_BLOCK = re.compile(r'data-section-name="recruiter"(.*?)</section>\s*</section>', re.S)
_TT_PERSON = re.compile(r'<a[^>]+href="(/people/[^"]+)"[^>]*>\s*([^<]+?)\s*</a>\s*(?:<span[^>]*>\s*([^<]*?)\s*</span>)?', re.S)
_TT_BODY = re.compile(r'data-section-name="job-body"(.*?)</section>', re.S)


def hydrate_teamtailor(lead: Lead) -> None:
    """A Teamtailor job page names the recruiter or contact ("Kontakt: Simon Kanaan, VD")."""
    url = lead.apply_url if lead.source == "jobtech" and lead.apply_url else lead.url
    got = page(url)
    if not got:
        return
    final, html = got
    m = _TT_BLOCK.search(html)
    if m:
        for href, name, title in _TT_PERSON.findall(m.group(1)):
            name = strip_html(name, keep_lines=False)
            if name and not any(c.name.lower() == name.lower() for c in lead.contacts):
                lead.contacts.append(Contact(name=name, role=strip_html(title or "", keep_lines=False), kind="ats_field",
                                             evidence=f"named as the contact on the job page {final}"))
    if len(lead.description) < 400:
        b = _TT_BODY.search(html)
        if b:
            lead.description = strip_html(b.group(1))[:20000]
    from ..enrich import employer_domain
    lead.company_url = lead.company_url or (final if employer_domain(final) else "")


FETCHERS = {
    "jobtech": fetch_jobtech,
    "nav": fetch_nav,
    "hrmanager": lambda src, cfg, slugs, limit: _per_company("hrmanager", slugs, hrmanager_one, limit),
    "teamtailor": lambda src, cfg, slugs, limit: _per_company("teamtailor", slugs, teamtailor_one, limit),
    "recruitee": lambda src, cfg, slugs, limit: _per_company("recruitee", slugs, recruitee_one, limit),
    "laura": lambda src, cfg, slugs, limit: _per_company("laura", slugs, laura_one, limit),
    "varbi": lambda src, cfg, slugs, limit: _per_company("varbi", slugs, varbi_one, limit),
}
