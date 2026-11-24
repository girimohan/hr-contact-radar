"""Company career pages on Greenhouse, Lever, Ashby and SmartRecruiters (public job-board APIs).

The company list is shared with remote-job-radar (data/companies.json there) plus any slugs in this
project's data/companies.json. Only postings in the Nordics or remote-in-Europe survive the pipeline.
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import re

from ..http import FetchError, get
from ..models import Lead
from ..text import strip_html
from . import SourceResult

WORKERS = 8


def parse_greenhouse(slug: str, data: dict) -> list[Lead]:
    return [Lead(
        source="greenhouse", source_id=f"{slug}:{j.get('id')}", company=j.get("company_name") or slug,
        title=(j.get("title") or "").strip(), url=j.get("absolute_url") or "",
        location=((j.get("location") or {}).get("name") or ""),
        posted=(j.get("first_published") or j.get("updated_at") or "")[:10],
        description=strip_html(j.get("content"))[:20000],
    ) for j in data.get("jobs") or []]


def parse_lever(slug: str, data) -> list[Lead]:
    out = []
    for j in data if isinstance(data, list) else []:
        c = j.get("categories") or {}
        locs = c.get("allLocations") or ([c["location"]] if c.get("location") else [])
        if j.get("workplaceType") == "remote":
            locs = locs + ["Remote"]
        desc = "\n".join(filter(None, [
            j.get("descriptionPlain"),
            *[f"{x.get('text', '')}\n{strip_html(x.get('content'))}" for x in j.get("lists") or []],
            j.get("additionalPlain"),
        ]))
        created = j.get("createdAt")
        out.append(Lead(
            source="lever", source_id=f"{slug}:{j.get('id')}", company=slug, title=(j.get("text") or "").strip(),
            url=j.get("hostedUrl") or "", location="; ".join(locs),
            posted=dt.datetime.fromtimestamp(created / 1000, dt.timezone.utc).date().isoformat() if created else "",
            description=desc[:20000], employment_type=c.get("commitment") or "",
        ))
    return out


def parse_ashby(slug: str, data: dict) -> list[Lead]:
    out = []
    for j in data.get("jobs") or []:
        if j.get("isListed") is False:
            continue
        addr = ((j.get("address") or {}).get("postalAddress") or {})
        parts = [j.get("location"), addr.get("addressCountry"), *[s.get("location") for s in j.get("secondaryLocations") or []]]
        if j.get("isRemote"):
            parts.append("Remote")
        locs = list(dict.fromkeys(p for p in parts if p))
        out.append(Lead(
            source="ashby", source_id=f"{slug}:{j.get('id')}", company=slug, title=(j.get("title") or "").strip(),
            url=j.get("jobUrl") or j.get("applyUrl") or "", location="; ".join(locs),
            posted=(j.get("publishedAt") or "")[:10],
            description=(strip_html(j.get("descriptionHtml")) or j.get("descriptionPlain") or "")[:20000],
            employment_type=j.get("employmentType") or "",
        ))
    return out


def parse_smartrecruiters(slug: str, data: dict) -> list[Lead]:
    out = []
    for j in data.get("content") or []:
        loc = j.get("location") or {}
        company = j.get("company") or {}
        ident = company.get("identifier") or slug
        place = loc.get("fullLocation") or ", ".join(filter(None, [loc.get("city"), loc.get("country")]))
        out.append(Lead(
            source="smartrecruiters", source_id=f"{ident}:{j.get('id')}", company=company.get("name") or slug,
            title=(j.get("name") or "").strip(), url=f"https://jobs.smartrecruiters.com/{ident}/{j.get('id')}",
            location=re.sub(r"(,\s*)+", ", ", place) + ("; Remote" if loc.get("remote") else ""),
            posted=(j.get("releasedDate") or "")[:10],
            employment_type=(j.get("typeOfEmployment") or {}).get("label") or "",
        ))
    return out


def hydrate_smartrecruiters(lead: Lead) -> None:
    """The list API has no description; the posting API has the ad text and sometimes the recruiter."""
    ident, _, pid = lead.source_id.partition(":")
    try:
        d = get(f"https://api.smartrecruiters.com/v1/companies/{ident}/postings/{pid}") or {}
    except FetchError:
        return
    sections = ((d.get("jobAd") or {}).get("sections") or {})
    lead.description = "\n".join(strip_html((sections.get(k) or {}).get("text")) for k in
                                 ("companyDescription", "jobDescription", "qualifications", "additionalInformation"))[:20000]
    comp = d.get("company") or {}
    lead.company_url = lead.company_url or (comp.get("websiteUrl") or "")


ATS = {
    "greenhouse": ("https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true", parse_greenhouse),
    "lever": ("https://api.lever.co/v0/postings/{slug}?mode=json", parse_lever),
    "ashby": ("https://api.ashbyhq.com/posting-api/job-board/{slug}", parse_ashby),
    "smartrecruiters": ("https://api.smartrecruiters.com/v1/companies/{slug}/postings?country=fi&limit=100", parse_smartrecruiters),
}


def _one(ats: str, slug: str):
    url, parse = ATS[ats]
    try:
        data = get(url.format(slug=slug))
    except FetchError as e:
        return slug, None, str(e)
    if data is None:
        return slug, [], "gone"
    try:
        leads = parse(slug, data)
    except Exception as e:   # one odd board must not sink the run
        return slug, None, f"parse error: {type(e).__name__}"
    for lead in leads:
        if lead.company == slug:
            lead.company = slug.replace("-", " ").replace(".", " ").title()
    return slug, leads, ""


def fetch(ats: str, slugs: list[str], limit: int | None = None) -> SourceResult:
    slugs = list(dict.fromkeys(slugs))[:limit] if limit else list(dict.fromkeys(slugs))
    res = SourceResult(name=ats)
    failed, gone = [], 0
    with cf.ThreadPoolExecutor(WORKERS) as ex:
        for slug, leads, note in ex.map(lambda s: _one(ats, s), slugs):
            if leads is None:
                failed.append(f"{slug} ({note})")
            else:
                gone += note == "gone"
                res.leads.extend(leads)
    res.detail = f"{len(slugs) - len(failed)} of {len(slugs)} boards answered" + (f", {gone} gone" if gone else "")
    if slugs and len(failed) > len(slugs) / 2:
        res.ok, res.error = False, "most boards failed, e.g. " + "; ".join(failed[:3])
    return res
