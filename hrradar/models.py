from __future__ import annotations

from dataclasses import asdict, dataclass, field

# How a contact was found, best first. The dashboard shows this so you know how much to trust an address.
KINDS = {
    "posting": "named in the job posting",
    "ats_field": "listed as the contact in the job board's data",
    "apply_email": "the posting's application address",
    "company_page": "found on the company's website",
    "generic": "a recruiting mailbox on the company's website",
    "pattern_guess": "guessed from the company's email format (not verified)",
}
KIND_RANK = {k: i for i, k in enumerate(KINDS)}


@dataclass
class Contact:
    name: str = ""
    email: str = ""
    phone: str = ""
    role: str = ""          # "Recruiter", "HR Manager", "Hiring manager"... as written, may be empty
    kind: str = "posting"   # one of KINDS
    evidence: str = ""      # the sentence or page URL it came from
    email_guessed: bool = False  # built from a name and the company's address format, not seen written down
    mx_ok: bool | None = None   # domain accepts mail; None = not checked

    @property
    def key(self) -> str:
        return (self.email or self.name).lower()

    @property
    def is_hr(self) -> bool:
        from .extract import HR_ROLE
        return bool(HR_ROLE.search(self.role or ""))


@dataclass
class Lead:
    """One job posting and the people you could write to about it."""

    source: str
    source_id: str
    company: str
    title: str
    url: str
    location: str = ""
    country: str = ""          # fi, se, no, dk, is, remote
    posted: str = ""
    description: str = ""
    employment_type: str = ""
    company_url: str = ""      # company website when the source gives one
    apply_email: str = ""      # application address when the source gives one
    apply_url: str = ""        # where the application form lives (often the employer's own ATS)
    ats_hint: str = ""         # "teamtailor" when the posting is hosted there: its page names the recruiter
    contacts: list[Contact] = field(default_factory=list)  # straight from the source's data (ATS / API fields)

    # filled in by the pipeline
    role: str = ""
    language: str = ""
    domain: str = ""
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    first_seen: str = ""

    @property
    def uid(self) -> str:
        return f"{self.source}:{self.source_id}"

    def best_contact(self) -> Contact | None:
        return min(self.contacts, key=contact_rank, default=None)

    def to_public(self) -> dict:
        d = asdict(self)
        d["id"] = self.uid
        d["snippet"] = self.description[:500]
        d["contacts"] = [dict(asdict(c), label=KINDS.get(c.kind, c.kind)) for c in sorted(self.contacts, key=contact_rank)]
        d.pop("description")
        return d


def contact_rank(c: Contact) -> tuple:
    """Lower is better: a named HR person with an email beats a mailbox, which beats a guess."""
    return (KIND_RANK.get(c.kind, 9), 0 if c.email else 1, 0 if c.name else 1, 0 if c.is_hr else 1)
