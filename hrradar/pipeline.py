"""collect -> keep Nordic / remote-Europe -> match role -> dedupe -> find contacts -> enrich -> score -> write."""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import json
from pathlib import Path

from . import dashboard, enrich, extract, match
from .models import Contact, Lead, contact_rank
from .sources import SourceResult, ats, hackernews, nordic

SOURCE_RANK = {"jobtech": 0, "nav": 0, "hrmanager": 0, "teamtailor": 0, "laura": 0, "varbi": 0, "recruitee": 1,
               "greenhouse": 1, "lever": 1, "ashby": 1, "smartrecruiters": 1, "hackernews": 2}
HYDRATE = {"smartrecruiters": ats.hydrate_smartrecruiters}
MAX_RECRUITER_PAGES = 200   # Teamtailor job pages read per run to get the recruiter's name


def collect(cfg: dict, companies: dict, only: set[str] | None = None, limit: int | None = None) -> list[SourceResult]:
    src = cfg.get("sources", {})

    def on(name: str) -> bool:
        return src.get(name, {}).get("enabled", True) and (only is None or name in only)

    jobs = []
    for name in ("greenhouse", "lever", "ashby", "smartrecruiters"):
        if on(name):
            jobs.append((name, ats.fetch, (name, companies.get(name, []), limit)))
    for name, fn in nordic.FETCHERS.items():
        if on(name):
            jobs.append((name, fn, (src.get(name, {}), cfg, companies.get(name, []), limit)))
    if on("hackernews"):
        jobs.append(("hackernews", hackernews.fetch, ()))
    # Sources are independent; run them side by side (each limits its own concurrency).
    with cf.ThreadPoolExecutor(4) as ex:
        futs = [(name, ex.submit(fn, *args)) for name, fn, args in jobs]
        results = []
        for name, f in futs:
            try:
                results.append(f.result())
            except Exception as e:   # a broken source must not sink the run
                results.append(SourceResult(name=name, ok=False, error=f"{type(e).__name__}: {e}"))
    return results


def select(leads: list[Lead], cfg: dict, funnel: dict) -> list[Lead]:
    # YAML turns an unquoted `no` (Norway) into False; read it back as "no".
    keep_places = {"no" if p is False else str(p) for p in cfg.get("keep_places", ["fi", "se", "no", "dk", "is", "nordic", "remote"])}
    block = [b.lower() for b in cfg.get("agency_blocklist", [])]
    best: dict[str, Lead] = {}
    for lead in leads:
        if not lead.title or not lead.url:
            continue
        if any(b in (lead.company or "").lower() for b in block):
            funnel["agency"] += 1
            continue
        lead.role = match.match_role(lead, cfg, full_text=lead.source == "hackernews")
        if not lead.role:
            funnel["role"] += 1
            continue
        lead.country = match.country_of(lead)
        if lead.country not in keep_places:
            funnel["place"] += 1
            continue
        k = match.dedupe_key(lead)
        cur = best.get(k)
        if cur is None:
            best[k] = lead
        else:
            funnel["duplicate"] += 1
            if SOURCE_RANK.get(lead.source, 5) < SOURCE_RANK.get(cur.source, 5):
                lead.contacts = merge_contacts(lead.contacts + cur.contacts)
                best[k] = lead
            else:
                cur.contacts = merge_contacts(cur.contacts + lead.contacts)
    return list(best.values())


def merge_contacts(contacts: list[Contact]) -> list[Contact]:
    out: dict[str, Contact] = {}
    for c in sorted(contacts, key=contact_rank):
        k = c.key
        if not k:
            continue
        if k in out:
            cur = out[k]
            cur.phone = cur.phone or c.phone
            cur.role = cur.role or c.role
            cur.name = cur.name or c.name
            continue
        # A guessed address for someone whose real address we have: drop the guess.
        if c.email_guessed and any(o.name and o.name.lower() == c.name.lower() and not o.email_guessed for o in out.values()):
            continue
        out[k] = c
    return sorted(out.values(), key=contact_rank)


def find_contacts(lead: Lead) -> None:
    for c in lead.contacts:   # structured contacts from the source's data
        c.name, extra = extract.clean_name(c.name)
        c.role = c.role or extra
    found = extract.extract(lead.description, lead.company)
    if lead.apply_email and extract.mailbox_kind(lead.apply_email) != "ignore":
        found.append(Contact(email=lead.apply_email.lower(), role="application address", kind="apply_email",
                             evidence="application email in the posting's data"))
    lead.contacts = [c for c in merge_contacts(lead.contacts + found) if not extract.is_union_rep(c)]


def apply_site(lead: Lead, entry: dict, cfg: dict) -> None:
    """Add what the company's website gave, and build guessed addresses for named people who have none."""
    site = enrich.contacts_from_cache(entry)
    fmt = entry.get("format")
    ecfg = cfg.get("enrichment", {})
    have_named = any(c.name and c.email and c.kind in ("posting", "ats_field") for c in lead.contacts)
    # The site's own format if it showed one; otherwise firstname.lastname, unless turned off in config.
    sep = fmt if fmt else (ecfg.get("default_format", ".") if ecfg.get("guess_without_format", True) else None)
    for c in lead.contacts:
        if c.name and not c.email and lead.domain and sep is not None:
            g = extract.guess_email(c.name, sep, lead.domain)
            if g:
                c.email, c.email_guessed = g, True
                c.evidence = (c.evidence + " · " if c.evidence else "") + (
                    f"address format learned from {lead.domain}" if fmt else
                    "address uses the firstname.lastname format most Nordic companies use; not verified")
    if not have_named:
        lead.contacts = merge_contacts(lead.contacts + site[: cfg.get("enrichment", {}).get("max_site_contacts", 3)])


def run(cfg: dict, companies: dict, root: Path, today: dt.date | None = None, only: set[str] | None = None,
        limit: int | None = None, enrich_sites: bool = True) -> dict:
    today = today or dt.date.today()
    private = root / "private"
    data_dir = root / "data"
    cfg = dict(cfg, _root=str(root))
    results = collect(cfg, companies, only, limit)
    if results and not any(r.ok for r in results):
        raise RuntimeError("every source failed: " + "; ".join(f"{r.name}: {r.error}" for r in results))
    all_leads = [lead for r in results for lead in r.leads]
    funnel = {"collected": len(all_leads), "agency": 0, "role": 0, "place": 0, "duplicate": 0}
    leads = select(all_leads, cfg, funnel)

    # Sources whose list API has no text: read the posting itself, for the leads we kept only.
    need = [lead for lead in leads if not lead.description and lead.source in HYDRATE]
    # Ads hosted on Teamtailor (directly, or reposted to Arbetsförmedlingen) name the recruiter on the job page.
    tt = [lead for lead in leads if lead.ats_hint == "teamtailor" and not any(c.name for c in lead.contacts)]
    with cf.ThreadPoolExecutor(6) as ex:
        list(ex.map(lambda lead: HYDRATE[lead.source](lead), need))
        list(ex.map(nordic.hydrate_teamtailor, tt[:MAX_RECRUITER_PAGES]))

    for lead in leads:
        lead.language = match.language_of(f"{lead.title}\n{lead.description}")
        find_contacts(lead)

    ecfg = cfg.get("enrichment", {})
    en = enrich.Enricher(data_dir, private, today, max_sites=ecfg.get("max_sites_per_run", 120),
                         max_pages=ecfg.get("max_pages_per_site", 6), refresh_days=ecfg.get("refresh_days", 14),
                         domain_overrides=cfg.get("domains") or {})
    for lead in leads:
        lead.domain = en.resolve_domain(lead)
    if enrich_sites and ecfg.get("enabled", True):
        # Best leads first, so the per-run site budget goes where it matters.
        for lead in leads:
            match.score(lead, cfg, today)
        leads.sort(key=lambda lead: -lead.score)
        lacking = [lead for lead in leads if not any(c.name and c.email for c in lead.contacts)]
        en.guess_domains([lead.company for lead in lacking if not lead.domain])
        for lead in lacking:
            lead.domain = lead.domain or en.domain_for(lead.company)
        en.crawl_many([lead.domain for lead in lacking if lead.domain])
        for lead in lacking:
            if lead.domain and lead.domain in en.sites:
                apply_site(lead, en.sites[lead.domain], cfg)
    for lead in leads:
        for c in lead.contacts:
            if c.email:
                c.mx_ok = en.accepts_mail(c.email.split("@")[1])
        lead.contacts = [c for c in lead.contacts if c.mx_ok is not False or not c.email_guessed]
        match.score(lead, cfg, today)
    en.save()

    min_score = cfg.get("min_score", 0)
    kept = [lead for lead in leads if lead.score >= min_score and (lead.contacts or cfg.get("keep_without_contact", True))]
    funnel["low_score"] = len(leads) - len(kept)
    kept.sort(key=lambda lead: (-lead.score, lead.company.lower()))

    # First-seen ledger: job keys and dates only, no people.
    ledger_path = data_dir / "seen.json"
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ledger = {}
    first_run = not ledger
    new_today = 0
    for lead in kept:
        k = match.dedupe_key(lead)
        if k not in ledger:
            ledger[k] = today.isoformat()
            new_today += 1
        lead.first_seen = ledger[k]
    cutoff = (today - dt.timedelta(days=cfg.get("retention_days", 120))).isoformat()
    live = {match.dedupe_key(lead) for lead in kept}
    ledger = {k: v for k, v in ledger.items() if v >= cutoff or k in live}

    kept_by = {}
    for lead in kept:
        kept_by[lead.source] = kept_by.get(lead.source, 0) + 1
    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "date": today.isoformat(),
        "first_run": first_run,
        "new_today": 0 if first_run else new_today,
        "funnel": funnel,
        "enrichment": dict(en.stats),
        "sources": [{"name": r.name, "ok": r.ok, "fetched": len(r.leads), "kept": kept_by.get(r.name, 0),
                     "detail": r.detail, "error": r.error} for r in results],
        "profile": cfg.get("profile", {}),
        "outreach": cfg.get("outreach", {}),
        "leads": [lead.to_public() for lead in kept],
    }
    private.mkdir(parents=True, exist_ok=True)
    (private / "leads.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    (private / "dashboard.html").write_text(dashboard.build(payload), encoding="utf-8")
    data_dir.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
    return payload
