from hrradar.sources import hackernews, nordic


def test_jobtech_contacts_and_teamtailor_hint():
    hit = {
        "id": "31764852", "headline": "Senior Utvecklare", "webpage_url": "https://arbetsformedlingen.se/platsbanken/annonser/31764852",
        "employer": {"name": "Deploja AB", "url": "https://career.deploja.se", "email": None},
        "workplace_address": {"city": "Solna", "municipality": "Stockholm", "country": "Sverige"},
        "application_details": {"email": None, "url": "https://career.deploja.se/jobs/8779350-senior", "reference": "teamtailor-8779350-2307817"},
        "application_contacts": [{"name": "Jonas Berg", "description": None, "email": "Jonas.Berg@deploja.se",
                                  "telephone": "08-123 456", "contact_type": "Rekryterare"}],
        "description": {"text": "Vi söker en utvecklare."}, "publication_date": "2026-11-23T09:52:00",
        "workplace_model": {"label": None},
    }
    lead = nordic.parse_jobtech(hit)
    assert lead.country == "se" and lead.ats_hint == "teamtailor" and lead.apply_url.endswith("senior")
    c = lead.contacts[0]
    assert (c.name, c.email, c.role, c.kind) == ("Jonas Berg", "jonas.berg@deploja.se", "Rekryterare", "ats_field")


def test_nav_entry():
    entry = {"status": "ACTIVE", "uuid": "u1", "ad_content": {
        "uuid": "u1", "jobtitle": "Backendutvikler", "title": "Bli med oss", "link": "https://arbeidsplassen.nav.no/stillinger/stilling/u1",
        "employer": {"name": "Ansettr AS", "homepage": "https://ansettr.no"}, "published": "2026-11-23T00:00:00+01:00",
        "workLocations": [{"city": "OSLO", "country": "NORGE"}], "description": "<p>Vi søker</p>",
        "contactList": [{"name": "Kari Nordmann", "email": "Kari@ansettr.no", "phone": "900 00 000", "role": "", "title": "HR-rådgiver"}]}}
    lead = nordic.parse_nav(entry)
    assert lead.title == "Backendutvikler" and lead.country == "no" and lead.company_url == "https://ansettr.no"
    assert lead.contacts[0].email == "kari@ansettr.no" and lead.contacts[0].role == "HR-rådgiver"


def test_teamtailor_recruiter_block(monkeypatch):
    html = ('<section data-section-name="recruiter" data-section-label="Rekryterare"><section><div>'
            '<span> Kontakt </span> <a href="/people/1777545-simon-kanaan">Simon Kanaan</a> <span> VD </span>'
            '</div></section></section>')
    monkeypatch.setattr(nordic, "page", lambda url: ("https://career.acme.se/jobs/1-dev", html))
    from hrradar.models import Lead
    lead = Lead(source="teamtailor", source_id="1", company="Acme", title="Dev", url="https://career.acme.se/jobs/1-dev")
    nordic.hydrate_teamtailor(lead)
    assert lead.contacts[0].name == "Simon Kanaan" and lead.contacts[0].role == "VD"
    assert lead.company_url.startswith("https://career.acme.se")


def test_hrmanager_project_leader(monkeypatch):
    data = {"Items": [{"Id": 7, "Name": "Python Developer", "CustomerName": "Acme AS", "AdvertisementUrlSecure": "https://x/7",
                       "Published": "/Date(1763900000000+0100)/", "WorkPlace": "Oslo", "ShortDescription": "<p>Hi</p>",
                       "Users": {"ProjectLeader": {"FirstName": "kari", "LastName": "Nordmann", "Email": "Kari.Nordmann@acme.no",
                                                   "Phone": "", "PhoneMobile": "900 00 000", "Title": "HR Partner"},
                                 "ProjectParticipants": []}}]}
    monkeypatch.setattr(nordic, "get", lambda url, **kw: data)
    lead = nordic.hrmanager_one("acme")[0]
    assert lead.contacts[0].name == "Kari Nordmann" and lead.contacts[0].email == "kari.nordmann@acme.no"
    assert lead.contacts[0].role == "HR Partner" and lead.posted.startswith("2025")


def test_hn_keeps_mailto():
    thread = {"children": [{"id": 1, "author": "a", "created_at": "2026-11-02T00:00:00Z",
                            "text": 'Acme | Python Engineer | REMOTE (EU)<p>We build things. '
                                    'Email <a href="mailto:jobs@acme.io">me</a></p>'}]}
    leads = hackernews.parse_thread(thread)
    assert leads and "jobs@acme.io" in leads[0].description
