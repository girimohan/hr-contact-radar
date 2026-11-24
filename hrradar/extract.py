"""Pull contact people out of job-posting text.

Nordic postings usually end with a contact block:
    "Lisätietoja tehtävästä antaa rekrytointipäällikkö Maija Meikäläinen, puh. 040 123 4567,
     sähköpostit muotoa etunimi.sukunimi@yritys.fi"
    "Mer information om tjänsten lämnas av Anna Svensson, anna.svensson@foretag.se"
    "Spørsmål om stillingen rettes til Ola Nordmann, tlf 900 00 000"
This module finds those people, their role, phone and email, and the company's email format
when the posting states it, so an address can be built for a named person.
"""
from __future__ import annotations

import re

from .models import Contact
from .text import ascii_fold

# Phrases that introduce a contact person, in Finnish, Swedish, Norwegian, Danish and English.
TRIGGERS = [
    # Finnish
    r"lisätietoja\w*", r"lisätiedot\w*", r"yhteyshenkilö\w*", r"yhteystiedot", r"tiedustelut", r"kysyttävää",
    r"vastaa\w* mielellään", r"ota yhteyttä", r"tavoitat", r"rekrytoija\w*", r"tietoa tehtävästä",
    r"voit olla yhteydessä", r"kysy lisää",
    # Swedish
    r"mer information", r"ytterligare information", r"upplysningar", r"kontaktperson\w*", r"frågor om tjänsten",
    r"har du frågor", r"kontakta", r"ansvarig rekryterare", r"rekryterare",
    # Norwegian / Danish
    r"spørsmål om stillingen", r"spørgsmål til stillingen", r"ytterligere opplysninger", r"yderligere oplysninger",
    r"mer informasjon", r"mere information", r"har du spørsmål", r"har du spørgsmål", r"kontakt",
    # English
    r"for (?:more|further) (?:information|details)", r"further information", r"contact person\w*",
    r"questions about the (?:role|position|job)", r"reach out to", r"feel free to (?:contact|reach)",
    r"get in touch", r"hiring manager", r"recruiter", r"talent acquisition", r"contact us", r"contact:",
    r"send (?:your|us your|an?) (?:cv|resume|résumé|application)", r"email (?:us|me|them) at", r"apply by email",
    r"questions\?", r"recruiting contact",
]
TRIGGER_RE = re.compile(r"(?<![\wåäö])(?:" + "|".join(TRIGGERS) + r")", re.I)

# Phrases that put a person's name right after them ("... antaa Maija", "kontakta Anna", "reach out to Jane").
# A name found in free text counts only if one of these comes shortly before it, in the same sentence,
# or if the name has its own phone number or a matching address. This keeps out Title Case phrases
# such as "Privacy Policy" or "Final Interview" that sit near the word "recruiter" in English ads.
STRONG = re.compile(
    r"(?<![\wåäö])(?:antaa|antavat|vastaa\w*|yhteyshenkilö\w*|tavoitat|ota yhteyttä|yhteydessä|kysy\w*|lisätietoja\w*|"
    r"lisätiedot\w*|tiedustelut|lämnas av|ger|kontakta|kontaktperson\w*|upplysningar\w*|besvaras av|"
    r"välkommen att kontakta|hos|rekryterare|rettes til|ta (?:gjerne )?kontakt med|kontakt(?:e|informasjon)?|"
    r"henvendelse til|spørsmål\w*|spørgsmål\w*|oplysninger|reach out to|contact(?: person)?|questions?|"
    r"feel free to (?:contact|reach out to|email)|e-?mail|recruiter\s*:|hiring manager\s*:|talent partner\s*:|"
    r"get in touch with|write to|speak (?:to|with)|apply to|send [^.\n]{0,30}? to)(?![\wåäö])", re.I)
UNION = re.compile(r"fack|union|förbund|forbund|\bsaco\b|\btco\b|tillitsvalgt|luottamusmies|luottamushenkilö|"
                   r"\bvision\b|\bkommunal\b|ingenjörer|\bst\b|\bseko\b|\bofr\b|\bjusek\b|tjänstemannaförbund", re.I)


def is_union_rep(c: "Contact") -> bool:
    """Swedish and Norwegian ads list union representatives next to the hiring contact; they do not hire."""
    return bool(UNION.search(f"{c.name} {c.role}"))


def _introduced(text: str, start: int) -> bool:
    gap_start = max(0, start - 70)
    for m in STRONG.finditer(text, gap_start, start):
        gap = text[m.end():start]
        if len(gap) <= 50 and not re.search(r"[.!?\n]", gap):
            return True
    return False

# Words that make a phrase a job title. HR_ROLE is the subset that means "recruiting / people team".
HR_ROLE = re.compile(
    r"recruit\w*|talent|\bhr\b|hr[- ]|human resources|people (?:partner|operations|team|lead|manager)|sourcer|"
    r"henkilöstö\w*|rekry\w*|rekryter\w*|personal(?:chef|ansvarig|avdeling|leder)|hr-\w+|"
    r"acquisition|employer brand", re.I)
TITLE_WORDS = re.compile(
    r"manager|lead|head|director|chief|officer|\bcto\b|\bceo\b|\bcoo\b|\bcpo\b|founder|partner|specialist|"
    r"consultant|coordinator|engineer|architect|recruit\w*|talent|\bhr\b|people|owner|"
    r"päällikkö|johtaja|esihenkilö|esimies|rekrytoi\w*|henkilöstö\w*|asiantuntija|konsultti|koordinaattori|"
    r"vastaava|tiiminvetäjä|vetäjä|toimitusjohtaja|yrittäjä|perustaja|"
    r"chef|ansvarig|rekryter\w*|vd\b|grundare|leder|sjef|ansvarlig|rådgiver|konsulent|direktør|direktör|daglig leder",
    re.I)

_QUALIFIER = re.compile(r"senior|junior|lead|principal|hr|it|technical|tekninen|vanhempi|global|nordic|interim|"
                        r"acting|associate|assistant", re.I)

EMAIL_RE = re.compile(r"(?<![\w.+-])([a-z0-9][a-z0-9._%+-]{0,63}@[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,24})(?![\w-])", re.I)
PHONE_RE = re.compile(r"(?<![\d+])(?:(?:\+|00)\d{2,3}[\s-]?\(?0?\)?[\s-]?\d{1,4}|0\d{1,3})(?:[\s-]?\d{2,4}){2,4}(?!\d)")

_UP = "A-ZÅÄÖØÆÜÉ"
_LO = "a-zåäöøæüéèáíóúšž'’"
_TOKEN = rf"[{_UP}][{_LO}]+(?:-[{_UP}][{_LO}]+)?"
NAME_RE = re.compile(rf"(?<![\w{_LO}])({_TOKEN}(?:[ \t]+(?:(?:van|von|de|af|la|di)[ \t]+)?{_TOKEN}){{1,2}})(?![\w{_LO}])")
_HONORIFIC = re.compile(r"\b(?:mr|mrs|ms|dr|prof)\.?\s+", re.I)

# Capitalised words that start sentences or name things, never people.
NOT_NAME = {w.lower() for w in """
Senior Junior Lead Head Talent Acquisition Recruiter Recruitment Recruiting Hiring Manager Team Director Chief
Our The We You Your Please Apply Send Contact Questions Feel Further More Information For If Any Read Learn
Hr People Partner Specialist Consultant Coordinator Engineer Engineering Product Design Sales Marketing Customer
Oy Oyj Ab Abp Ltd Inc Gmbh As Asa Aps Group Company Corporation Finland Suomi Sweden Sverige Norway Norge Denmark
Danmark Nordic Nordics Europe Helsinki Espoo Vantaa Tampere Turku Oulu Jyväskylä Lahti Kuopio Stockholm Göteborg
Gothenburg Malmö Uppsala Oslo Bergen Trondheim Copenhagen København Aarhus Reykjavik Remote Hybrid Office
January February March April May June July August September October November December Monday Tuesday Wednesday
Thursday Friday Saturday Sunday Tammikuu Helmikuu Maaliskuu Huhtikuu Toukokuu Kesäkuu Heinäkuu Elokuu Syyskuu
Lokakuu Marraskuu Joulukuu Lisätietoja Lisätiedot Yhteyshenkilö Hae Haku Hakemus Tehtävä Tehtävään Työ Puh Tel
Mer Kontakt Kontaktperson Frågor Spørsmål Har Du Vi Med Och Och Ring Sök Søk Velkommen Välkommen Tervetuloa
Microsoft Google Amazon Aws Azure Python Java Javascript Typescript React Node Linux Docker Kubernetes Cloud
Data Ai Ml Llm Teams Slack Linkedin Github Gitlab Jira Confluence Salesforce Sap Oracle Apple Meta Openai
Anthropic Claude Chatgpt Copilot Power Bi Excel Office Business Development Operations Support Service Services
Technical Technology Software Platform Solutions Digital Innovation Analytics Security Network Systems Project
Program Programme Manager Development Research University Yliopisto Ammattikorkeakoulu Kaupunki City Municipality
Hyvinvointialue Region Ministry State Valtio Kunta Insinööri Teknologia Palvelut Ratkaisut Tervetuloa Kiitos
Thanks Thank Best Regards Kind Cheers Hello Hi Hei Moi Hej Kontaktinformasjon Kontaktinformation Contact Kontakt
Privacy Policy Interview Final Offer Decision Live Coding Culture Values Global Job Description Benefits Perks
""".split()}

# Mailboxes: who reads them.
_RECRUITING = re.compile(r"^(?:rekry\w*|recruit\w*|careers?|jobs?|work|hr|talent\w*|people|hiring|ty[oö]paikat|"
                         r"rekrytering|jobb\w*|karri[aä]r\w*|karriere|apply|applications?|hakemukset|cv|"
                         r"join(?:us)?|jobs?-?\w*|hr-\w+|\w+-?rekry|\w+\.rekry|\w+-?careers?|\w+-?jobs)$", re.I)
_GENERAL = re.compile(r"^(?:info|hello|hi|hey|contact|office|toimisto|kontakt|post|posti|mail|kirjaamo|"
                      r"asiakaspalvelu|kundservice|team|tiimi|general)$", re.I)
_IGNORE = re.compile(r"^(?:no-?reply|do-?not-?reply|donotreply|privacy|gdpr|dpo|tietosuoja|dataprotection|"
                     r"data-?protection|abuse|postmaster|webmaster|hostmaster|support|help|helpdesk|billing|"
                     r"invoic\w*|lasku\w*|faktura|sales|myynti|salg|press|media|news|newsletter|ir|investor\w*|"
                     r"security|legal|compliance|whistleblow\w*|feedback|palaute|marketing|markkinointi|"
                     r"accessibility|saavutettavuus|cookies?|unsubscribe)$", re.I)
_JUNK_DOMAIN = re.compile(r"(?:^|\.)(?:example\.(?:com|org|fi)|domain\.com|email\.com|yritys\.fi|company\.com|"
                          r"sentry\.io|wixpress\.com|sentry-next\.wixpress\.com|godaddy\.com|"
                          r"teamtailor\.com|smartrecruiters\.com|greenhouse\.io|lever\.co|ashbyhq\.com|"
                          r"recruitee\.com|workable\.com|personio\.\w+)$", re.I)
_FILE = re.compile(r"\.(?:png|jpe?g|gif|svg|webp|css|js)$", re.I)

_TEMPLATE_LOCAL = re.compile(r"etunimi|sukunimi|firstname|lastname|first\.name|last\.name|förnamn|efternamn|fornavn|"
                             r"etternavn|efternavn|^name\.surname$|^first\.last$", re.I)
FORMAT_RE = re.compile(
    r"(etunimi|firstname|first[\s_]?name|förnamn|fornavn|fornamn|name)\s*([._-]?)\s*"
    r"(sukunimi|lastname|last[\s_]?name|surname|efternamn|etternavn|efternavn)\s*@\s*([a-z0-9-]+(?:\.[a-z0-9-]+)+)",
    re.I)

_AT = re.compile(r"\s*[\(\[\{<]\s*(?:at|ät|a|@|miuku|miukumauku|kissanhäntä|snabel-?a)\s*[\)\]\}>]\s*", re.I)
_DOT = re.compile(r"\s*[\(\[\{<]\s*(?:dot|piste|punkt|prikk|punktum)\s*[\)\]\}>]\s*", re.I)
_SPACED_AT = re.compile(r"(\b[a-z0-9]+[._][a-z0-9._-]+)\s+(?:at|ät|@)\s+([a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,})\b", re.I)
_SPACED_SIGN = re.compile(r"([a-z0-9._-]+)\s+@\s+([a-z0-9-]+(?:\s*\.\s*[a-z0-9-]+)+)", re.I)


def deobfuscate(text: str) -> str:
    """matti.meikalainen(at)firma.fi, anna [at] firma [dot] se, ola at firma.no -> real addresses."""
    t = _AT.sub("@", text)
    t = _DOT.sub(".", t)
    t = _SPACED_AT.sub(r"\1@\2", t)
    return _SPACED_SIGN.sub(lambda m: f"{m.group(1)}@{re.sub(r'\s+', '', m.group(2))}", t)


def mailbox_kind(email: str) -> str:
    """person, recruiting, general, unknown or ignore."""
    local, _, domain = email.lower().partition("@")
    if (not domain or _JUNK_DOMAIN.search(domain) or _FILE.search(email) or _IGNORE.match(local)
            or _TEMPLATE_LOCAL.search(local)):
        return "ignore"
    if _RECRUITING.match(local):
        return "recruiting"
    if _GENERAL.match(local):
        return "general"
    if re.fullmatch(r"[a-z]+(?:-[a-z]+)?[._][a-z]+(?:-[a-z]+)?", local) or re.fullmatch(r"[a-z]\.[a-z]+", local):
        return "person"
    return "unknown"


def find_emails(text: str) -> list[tuple[str, int]]:
    out, seen = [], set()
    for m in EMAIL_RE.finditer(text):
        e = m.group(1).rstrip(".").lower()
        if e not in seen and mailbox_kind(e) != "ignore":
            seen.add(e)
            out.append((e, m.start()))
    return out


_PHONE_KW = re.compile(r"\b(?:puh|puhelin|tel|tlf|tfn|telefon|mob|mobil|gsm|phone|p)\.?:?\s*(\+?\d[\d\s-]{6,18}\d)", re.I)


def find_phone(text: str) -> str:
    m = _PHONE_KW.search(text)
    if m and 8 <= sum(ch.isdigit() for ch in m.group(1)) <= 15:
        return re.sub(r"\s+", " ", m.group(1)).strip(" -")
    for m in PHONE_RE.finditer(text):
        if 8 <= sum(ch.isdigit() for ch in m.group(0)) <= 15:
            return re.sub(r"\s+", " ", m.group(0)).strip(" -")
    return ""


def email_format(text: str) -> tuple[str, str] | None:
    """"sähköpostit muotoa etunimi.sukunimi@firma.fi" -> (".", "firma.fi")."""
    m = FORMAT_RE.search(deobfuscate(text))
    return (m.group(2), m.group(4).lower()) if m else None


def guess_email(name: str, sep: str, domain: str) -> str:
    parts = [p for p in re.split(r"\s+", ascii_fold(name).strip()) if p]
    if len(parts) < 2 or not domain:
        return ""
    clean = [re.sub(r"[^a-z-]", "", p) for p in (parts[0], parts[-1])]
    return f"{clean[0]}{sep}{clean[1]}@{domain}" if all(clean) else ""


def name_from_email(email: str) -> str:
    local = email.split("@")[0]
    if mailbox_kind(email) != "person" or re.fullmatch(r"[a-z]\.[a-z]+", local):
        return ""
    return " ".join(p.capitalize() for p in re.split(r"[._]", local))


def email_matches_name(email: str, name: str) -> bool:
    local = re.sub(r"[^a-z]", "", email.split("@")[0].lower())
    parts = [re.sub(r"[^a-z]", "", p) for p in ascii_fold(name).split()]
    parts = [p for p in parts if len(p) >= 2]
    if not parts or not local:
        return False
    first, last = parts[0], parts[-1]
    return (len(parts) > 1 and last in local and (first in local or local.startswith(first[0]))) or local == first


def _is_name(cand: str, company_words: set[str]) -> bool:
    toks = cand.split()
    if len(toks) < 2 or len(toks) > 4:
        return False
    for t in toks:
        low = t.lower()
        if low in NOT_NAME or low in company_words or low in ("van", "von", "de", "af", "la", "di"):
            if low not in ("van", "von", "de", "af", "la", "di"):
                return False
        if TITLE_WORDS.fullmatch(low):
            return False
    return True


_FI_CASE = re.compile(r"(\w{2,})s(?:een|elle|elta|eltä|esta|estä|eksi)$")


def base_surname(name: str) -> str:
    """Finnish ads inflect surnames: "ota yhteyttä Neea Turuseen", "lähetä Matti Virtaselle". The address
    uses the base form (Turunen, Virtanen), so turn -seen / -selle / -selta / -sesta back into -nen."""
    parts = name.split()
    if len(parts) >= 2:
        parts[-1] = _FI_CASE.sub(r"\1nen", parts[-1])
    return " ".join(parts)


def clean_name(name: str) -> tuple[str, str]:
    """ "Panos Plikas, Enhetschef" -> ("Panos Plikas", "Enhetschef"): job boards sometimes put the title in the name."""
    name, _, rest = (name or "").partition(",")
    return re.sub(r"\s+", " ", name).strip(), rest.strip()


def _role_near(text: str, start: int, end: int) -> str:
    """The job title written right after the name ("Anna Svensson, HR Manager") or before it
    ("henkilöstöpäällikkö Maija Meikäläinen")."""
    after = text[end:end + 90]
    after = re.split(r"\n|@|\b(?:puh|tel|tfn|tlf|p\.|mob|gsm|phone|email|e-mail|sähköposti|e-post)\b|\+?\d{3}", after, maxsplit=1, flags=re.I)[0]
    after = after.strip(" ,:;()–—-/|")
    after = re.split(r"\s*[,(;|]\s*|\s+(?:and|ja|och|og)\s+", after)
    for chunk in after[:2]:
        if chunk and TITLE_WORDS.search(chunk) and len(chunk) <= 70:
            return chunk.strip(" .,")
    # Title written before the name: keep only the trailing title words ("HR-asiantuntija", "senior recruiter").
    words = re.split(r"[\s,(]+", re.split(r"[\n.:;]", text[max(0, start - 70):start])[-1].strip())
    picked: list[str] = []
    for w in reversed([w for w in words if w]):
        if TITLE_WORDS.search(w) or (picked and _QUALIFIER.fullmatch(w)):
            picked.insert(0, w)
        else:
            break
    return " ".join(picked)


def extract(text: str, company: str = "", known_domain: str = "") -> list[Contact]:
    """Every contact person and recruiting mailbox in a posting, best evidence attached."""
    if not text:
        return []
    text = deobfuscate(_HONORIFIC.sub("", text))
    company_words = {w.lower() for w in re.findall(rf"[{_UP}{_LO}]+", company or "") if len(w) > 2}
    emails = find_emails(text)
    fmt = email_format(text)

    # Where to look for names: after each trigger phrase, and just before each email address.
    windows = [(m.start(), min(len(text), m.end() + 320)) for m in TRIGGER_RE.finditer(text)]
    windows += [(max(0, pos - 140), pos) for _, pos in emails]

    names: list[tuple[str, int, int]] = []
    seen = set()
    for a, b in sorted(windows):
        for m in NAME_RE.finditer(text, a, b):
            cand = re.sub(r"[ \t]+", " ", m.group(1)).strip()
            start = m.start()
            # "Kontaktinformasjon Kaja Kværn": a capitalised word before the name; try again without it.
            if not _is_name(cand, company_words) and len(cand.split()) == 3:
                first = cand.split()[0]
                cand, start = cand[len(first):].strip(), text.index(cand.split()[1], m.start())
            if cand.lower() in seen or not _is_name(cand, company_words):
                continue
            if re.match(r"\s*(?:oyj?|ab|abp|as|asa|a/s|aps|ltd|gmbh|inc|llc|group)\b", text[m.end():m.end() + 8], re.I):
                continue   # "Meikäläinen Oy" is a company
            cand = base_surname(cand)
            seen.add(cand.lower())
            names.append((cand, start, m.end()))
    # Keep only real people: introduced by a contact phrase, or with their own phone number or address.
    names = [n for n in names if _introduced(text, n[1]) or find_phone(text[n[2]:n[2] + 60])
             or any(email_matches_name(e, n[0]) for e, _ in emails)]
    names.sort(key=lambda n: n[1])

    contacts: list[Contact] = []
    used: set[str] = set()
    for i, (name, start, end) in enumerate(names):
        seg_end = names[i + 1][1] if i + 1 < len(names) else min(len(text), end + 260)
        seg = text[end:seg_end]
        email = next((e for e, _ in emails if email_matches_name(e, name)), "")
        if not email:
            email = next((e for e, p in emails if end <= p < seg_end and e not in used
                          and mailbox_kind(e) in ("person", "unknown")), "")
        guessed = False
        if not email and fmt:
            email, guessed = guess_email(name, fmt[0], fmt[1]), True
        if email:
            used.add(email)
        line = text[max(0, start - 60):min(len(text), seg_end)].replace("\n", " ")
        contacts.append(Contact(name=name, email=email, phone=find_phone(seg[:120]), role=_role_near(text, start, end),
                                kind="posting", evidence=re.sub(r"\s+", " ", line).strip()[:240],
                                email_guessed=guessed and bool(email)))

    for email, pos in emails:
        if email in used:
            continue
        kind = mailbox_kind(email)
        ctx = re.sub(r"\s+", " ", text[max(0, pos - 120):pos + 60]).strip()
        if kind == "person":
            c = Contact(name=name_from_email(email), email=email, kind="posting", evidence=ctx[:240])
        elif kind == "recruiting":
            c = Contact(email=email, role="recruiting mailbox", kind="apply_email", evidence=ctx[:240])
        elif kind == "general":
            c = Contact(email=email, role="general mailbox", kind="apply_email", evidence=ctx[:240])
        else:
            c = Contact(email=email, kind="posting", evidence=ctx[:240])
        contacts.append(c)
    return contacts
