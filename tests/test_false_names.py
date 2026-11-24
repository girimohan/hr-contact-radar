from hrradar.extract import extract, is_union_rep
from hrradar.models import Contact


def names(text, company=""):
    return {c.name for c in extract(text, company) if c.name}


def test_interview_steps_and_policies_are_not_people():
    text = ("Our process: Talent Acquisition Partner screening, Hiring Manager Interview, Live Coding, Final Interview, "
            "Offer Decision. For more information on how we process personal data, read our Privacy Policy. "
            "Why You'll Thrive at Grafana Labs: 100% Remote, Global Culture. Offices in San Francisco.")
    assert names(text, "Grafana Labs") == set()


def test_introduced_people_still_found():
    assert names("Vid frågor, kontakta gärna följande personer: Christian Rashdan, rekryterande chef.") == {"Christian Rashdan"}
    assert names("If you have questions, reach out to our recruiter Jane Doe.") == {"Jane Doe"}
    assert names("Kontaktinformasjon Kaja Kværn, Teamleder") == {"Kaja Kværn"}


def test_union_reps_dropped():
    assert is_union_rep(Contact(name="Akademikerförbundet SSR, Kristina Olofsson"))
    assert is_union_rep(Contact(name="Lars Berg", role="Facklig företrädare"))
    assert not is_union_rep(Contact(name="Anna Svensson", role="HR-specialist"))


def test_finnish_inflected_surname_and_title_in_name():
    from hrradar.extract import base_surname, clean_name
    assert base_surname("Neea Turuseen") == "Neea Turunen"
    assert base_surname("Matti Virtaselle") == "Matti Virtanen"
    assert base_surname("Lars Hansen") == "Lars Hansen"
    assert clean_name("Panos Plikas, Enhetschef") == ("Panos Plikas", "Enhetschef")
    got = names("Kysyttävää? Ota yhteyttä rekrytoijaamme Neea Turuseen, puh. 040 111 2222.")
    assert got == {"Neea Turunen"}
