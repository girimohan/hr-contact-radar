import datetime as dt

import yaml

from hrradar import enrich, match, pipeline
from hrradar.models import Contact, Lead

CFG = yaml.safe_load(open("config.yaml", encoding="utf-8"))
TODAY = dt.date(2026, 11, 24)


def lead(**kw):
    base = dict(source="greenhouse", source_id="x:1", company="Acme", title="Python Developer", url="https://x")
    base.update(kw)
    return Lead(**base)


def test_country_detection():
    assert match.country_of(lead(location="Helsinki, Finland")) == "fi"
    assert match.country_of(lead(location="Stockholm")) == "se"
    assert match.country_of(lead(location="Remote - EMEA")) == "remote"
    assert match.country_of(lead(location="Remote - US")) == ""
    assert match.country_of(lead(location="New York")) == ""
    assert match.country_of(lead(location="Remote", description="You can work from anywhere in Europe")) == "remote"


def test_role_and_exclusions():
    assert match.match_role(lead(title="Ohjelmistokehittäjä"), CFG) == "Backend Engineer"
    assert match.match_role(lead(title="Senior LLM Engineer"), CFG) == "AI / LLM Engineer"
    assert match.match_role(lead(title="Engineering Manager, AI"), CFG) == ""


def test_select_dedupes_and_merges_contacts():
    a = lead(location="Helsinki", contacts=[Contact(email="rekry@acme.fi", kind="apply_email")])
    b = lead(source="teamtailor", source_id="y", location="Helsinki",
             contacts=[Contact(name="Jane Doe", email="jane.doe@acme.fi", kind="ats_field", role="Recruiter")])
    funnel = {"agency": 0, "role": 0, "place": 0, "duplicate": 0}
    kept = pipeline.select([a, b], CFG, funnel)
    assert len(kept) == 1 and funnel["duplicate"] == 1
    assert kept[0].source == "teamtailor"
    assert {c.email for c in kept[0].contacts} == {"rekry@acme.fi", "jane.doe@acme.fi"}


def test_score_prefers_named_person():
    named = lead(location="Helsinki", country="fi", role="Backend Engineer", language="en",
                 contacts=[Contact(name="Jane Doe", email="jane.doe@acme.fi", kind="posting")])
    box = lead(location="Helsinki", country="fi", role="Backend Engineer", language="en",
               contacts=[Contact(email="jobs@acme.fi", kind="apply_email")])
    none = lead(location="Helsinki", country="fi", role="Backend Engineer", language="en")
    for x in (named, box, none):
        match.score(x, CFG, TODAY)
    assert named.score > box.score > none.score


def test_guess_for_named_person_without_address():
    x = lead(domain="acme.fi", contacts=[Contact(name="Maija Meikäläinen", kind="posting")])
    pipeline.apply_site(x, {"contacts": [], "format": "_"}, CFG)
    assert x.contacts[0].email == "maija_meikalainen@acme.fi" and x.contacts[0].email_guessed


def test_domain_helpers():
    assert enrich.registrable("careers.acme.fi") == "acme.fi"
    assert enrich.employer_domain("https://jobs.lever.co/acme/123") == ""
    assert enrich.employer_domain("https://careers.acme.fi/jobs/1") == "acme.fi"
    assert enrich.employer_domain("rekry@acme.fi") == "acme.fi"
    assert enrich.company_slug("Acme Finland Oy") == "acme"


def test_site_people_and_format():
    html = """<html><body><h2>Team</h2>
      <p>Jane Doe<br>Talent Acquisition Lead<br><a href="mailto:jane.doe@acme.fi">jane.doe@acme.fi</a></p>
      <p>John Smith<br>Sales Director<br>john.smith@acme.fi</p>
      <p>Jobs: <a href="mailto:careers@acme.fi">careers@acme.fi</a> · privacy@acme.fi</p></body></html>"""
    res = enrich.people_on_pages([("https://acme.fi/team", html)], "acme.fi")
    emails = [c["email"] for c in res["contacts"]]
    assert emails[0] == "jane.doe@acme.fi"
    assert "careers@acme.fi" in emails
    assert "john.smith@acme.fi" not in emails and "privacy@acme.fi" not in emails
    assert res["format"] == "."
