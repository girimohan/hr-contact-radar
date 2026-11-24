"""Which postings to keep (role and place), and how worth writing to each one is (0-100)."""
from __future__ import annotations

import datetime as dt
import re

from . import extract
from .models import Lead
from .text import ascii_fold, first_keyword

PLACES = {
    "fi": r"finland|suomi|helsinki|helsingfors|espoo|vantaa|tampere|turku|åbo|oulu|jyväskylä|lahti|kuopio|pori|"
          r"joensuu|lappeenranta|vaasa|hämeenlinna|seinäjoki|rovaniemi|kotka|kouvola|mikkeli|porvoo|salo|hyvinkää|"
          r"järvenpää|kerava|nurmijärvi|kirkkonummi|tuusula|uusimaa|pirkanmaa|varsinais-suomi|pohjois-pohjanmaa",
    "se": r"sweden|sverige|stockholm|göteborg|gothenburg|malmö|malmo|uppsala|linköping|västerås|örebro|lund|umeå|"
          r"helsingborg|norrköping|jönköping|luleå|sundsvall|gävle|karlstad|växjö|halmstad|skåne",
    "no": r"norway|norge|oslo|bergen|trondheim|stavanger|drammen|kristiansand|tromsø|fredrikstad|sandnes|bodø|ålesund",
    "dk": r"denmark|danmark|copenhagen|københavn|kobenhavn|aarhus|århus|odense|aalborg|esbjerg|kolding|vejle|"
          r"roskilde|horsens|lyngby|ballerup",
    "is": r"iceland|ísland|reykjavik|reykjavík",
}
PLACE_RE = {k: re.compile(rf"(?<![\wåäö])(?:{v})(?![\wåäö])", re.I) for k, v in PLACES.items()}
NORDIC = re.compile(r"\bnordics?\b|\bscandinavia\b|\bpohjoismaat\b|\bnorden\b", re.I)
REMOTE = re.compile(r"\bremote\b|\betätyö\w*|\bdistans\w*|\bfjernarbeid|\bhjemmekontor|\banywhere\b|\bwork from home\b", re.I)
OPEN_TO_US = re.compile(r"\b(?:europe|european|eu|eea|emea|cet|cest|eet|utc ?[+-] ?[0-3]|gmt ?[+-] ?[0-3]|"
                        r"worldwide|anywhere|global(?:ly)?|international)\b", re.I)
ELSEWHERE = re.compile(r"\b(?:us|usa|u\.s\.|united states|canada|latam|latin america|apac|india|brazil|mexico|"
                       r"australia|americas|north america|philippines|singapore|japan|uk only|us only)\b", re.I)


def country_of(lead: Lead) -> str:
    """fi / se / no / dk / is for Nordic jobs, "remote" for remote jobs open to Europe, "" for anything else."""
    if lead.country in ("fi", "se", "no", "dk", "is"):
        return lead.country
    loc = lead.location or ""
    for code, rx in PLACE_RE.items():
        if rx.search(loc):
            return code
    if NORDIC.search(loc):
        return "nordic"
    text = f"{loc} {lead.title} {(lead.description or '')[:3000]}"
    if REMOTE.search(loc) or REMOTE.search(lead.title) or lead.country == "remote":
        head = f"{loc} {lead.title}"
        if OPEN_TO_US.search(head) or (not ELSEWHERE.search(head) and OPEN_TO_US.search(text)):
            return "remote"
        if PLACE_RE["fi"].search(text):
            return "remote"
    return ""


def match_role(lead: Lead, cfg: dict, full_text: bool = False) -> str:
    title = (lead.title or "").lower()
    hay = f"{title} {(lead.description or '').lower()[:4000]}" if full_text else title
    if not full_text and first_keyword(title, cfg.get("exclude_title", [])):
        return ""
    for role in cfg["roles"]:
        if first_keyword(hay, role["title_any"]):
            return role["name"]
    return ""


_FI = re.compile(r"\b(?:ja|tai|on|ovat|että|sekä|meillä|sinulla|haemme|tehtävä\w*|työ\w*|kokemus\w*|osaamis\w*)\b", re.I)
_SV = re.compile(r"\b(?:och|att|är|för|vi|söker|du|har|tjänsten|erfarenhet|arbete)\b", re.I)
_NO = re.compile(r"\b(?:og|er|vi|søker|du|har|stillingen|erfaring|arbeid|ikke|eller)\b", re.I)
_DA = re.compile(r"\b(?:og|er|vi|søger|du|har|stillingen|erfaring|arbejde|ikke|eller)\b", re.I)
_EN = re.compile(r"\b(?:and|the|you|we|are|with|for|experience|our|will|work)\b", re.I)


def language_of(text: str) -> str:
    sample = (text or "")[:3000]
    counts = {k: len(rx.findall(sample)) for k, rx in (("fi", _FI), ("sv", _SV), ("no", _NO), ("da", _DA), ("en", _EN))}
    best = max(counts, key=counts.get)
    return best if counts[best] >= 3 else "en"


CONTACT_POINTS = {
    "named_email": 40,      # a named person with an address written in the posting or the source's data
    "named_guess": 30,      # a named person, address built from the company's stated format
    "named_phone": 24,      # a named person with only a phone number
    "mailbox": 22,          # an application / recruiting mailbox
    "site_person": 26,      # a recruiter or hiring leader on the company's own website
    "named_only": 14,
    "general": 8,
    "none": 0,
}


def contact_quality(lead: Lead) -> str:
    best = "none"
    order = list(CONTACT_POINTS)
    for c in lead.contacts:
        if c.mx_ok is False:
            continue
        if c.kind in ("posting", "ats_field"):
            q = ("named_guess" if c.email_guessed else "named_email") if (c.name and c.email) else \
                "named_phone" if (c.name and c.phone) else "named_only" if c.name else \
                "mailbox" if c.email else "none"
        elif c.kind == "apply_email":
            q = "general" if c.role == "general mailbox" else "mailbox"
        elif c.kind == "company_page":
            q = "site_person"
        elif c.kind == "pattern_guess":
            q = "named_guess"
        elif c.kind == "generic":
            q = "general" if c.role == "general mailbox" else "mailbox"
        else:
            q = "none"
        if order.index(q) < order.index(best):
            best = q
    return best


def score(lead: Lead, cfg: dict, today: dt.date) -> None:
    weights = {r["name"]: float(r.get("weight", 1)) for r in cfg["roles"]}
    reasons: list[str] = []
    total = 30 * weights.get(lead.role, 0.5)

    q = contact_quality(lead)
    total += CONTACT_POINTS[q]
    best = lead.best_contact()
    if best and q != "none":
        who = best.name or best.email
        what = f"{best.role}, " if best.role and best.name else ""
        reasons.append(f"contact: {who} ({what}{'address guessed' if best.email_guessed else best.kind.replace('_', ' ')})")
    else:
        reasons.append("no contact found yet")
    if best and extract.HR_ROLE.search(best.role or ""):
        total += 4
        reasons.append("contact works in recruiting")

    total += {"fi": 15, "nordic": 10, "se": 10, "no": 10, "dk": 10, "is": 8, "remote": 8}.get(lead.country, 0)

    skills = [s for s in cfg.get("profile", {}).get("skills", []) if first_keyword(f"{lead.title} {lead.description}".lower(), [s])]
    total += min(len(skills), 6) / 6 * 10
    if skills:
        reasons.append("skills: " + ", ".join(skills[:5]))

    if lead.posted:
        try:
            age = (today - dt.date.fromisoformat(lead.posted[:10])).days
            total += 6 if age <= 7 else 3 if age <= 21 else 0
            if age <= 7:
                reasons.append("posted this week")
        except ValueError:
            pass

    if lead.language not in ("en", ""):
        total -= cfg.get("language_penalty", 8)
        reasons.append({"fi": "posting in Finnish", "sv": "posting in Swedish", "no": "posting in Norwegian",
                        "da": "posting in Danish"}.get(lead.language, "not in English"))
    lead.score = max(0, min(100, round(total)))
    lead.reasons = reasons


def dedupe_key(lead: Lead) -> str:
    return re.sub(r"[^a-z0-9]+", " ", ascii_fold(f"{lead.company} {lead.title}")).strip()
