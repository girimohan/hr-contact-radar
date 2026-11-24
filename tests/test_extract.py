from hrradar.extract import deobfuscate, email_format, extract, guess_email, mailbox_kind, name_from_email


def by_name(contacts):
    return {c.name: c for c in contacts if c.name}


def test_finnish_contact_block_with_stated_format():
    text = ("Lisätietoja tehtävästä antaa ohjelmistokehityspäällikkö Maija Meikäläinen, puh. 040 123 4567. "
            "Rekrytointiin liittyvissä kysymyksissä vastaa HR-asiantuntija Pekka Virtanen, p. +358 50 765 4321. "
            "Sähköpostit ovat muotoa etunimi.sukunimi@espoo.fi.")
    got = by_name(extract(text, "Espoon kaupunki"))
    m, p = got["Maija Meikäläinen"], got["Pekka Virtanen"]
    assert m.email == "maija.meikalainen@espoo.fi" and m.email_guessed
    assert m.role == "ohjelmistokehityspäällikkö" and m.phone == "040 123 4567"
    assert p.role == "HR-asiantuntija" and p.is_hr and p.email == "pekka.virtanen@espoo.fi"
    assert not any("etunimi" in c.email for c in extract(text))


def test_swedish_obfuscated_address():
    got = extract("Mer information om tjänsten lämnas av Anna Svensson, rekryteringsansvarig, "
                  "anna.svensson(at)foretag.se, 070-123 45 67.")
    a = by_name(got)["Anna Svensson"]
    assert a.email == "anna.svensson@foretag.se" and not a.email_guessed
    assert a.role == "rekryteringsansvarig" and a.phone.startswith("070")


def test_norwegian_phone_without_leading_zero_and_mailbox():
    got = extract("Spørsmål om stillingen rettes til Ola Nordmann, daglig leder, tlf 900 00 000, eller send CV til jobb@firma.no.")
    o = by_name(got)["Ola Nordmann"]
    assert o.phone == "900 00 000" and o.role == "daglig leder"
    assert any(c.email == "jobb@firma.no" and c.kind == "apply_email" for c in got)


def test_english_recruiter_and_ignored_mailboxes():
    got = extract("We are Acme Oy. Questions? Reach out to our senior recruiter Jane Doe (jane.doe@acme.fi). "
                  "Send your CV to careers@acme.fi. Built with Python and React. Privacy: privacy@acme.fi", "Acme Oy")
    j = by_name(got)["Jane Doe"]
    assert j.email == "jane.doe@acme.fi" and j.role == "senior recruiter"
    emails = {c.email for c in got}
    assert "careers@acme.fi" in emails and "privacy@acme.fi" not in emails
    assert "Acme Oy" not in by_name(got) and "Python React" not in by_name(got)


def test_company_names_are_not_people():
    got = extract("Lisätietoja antaa Meikäläinen Oy:n toimitusjohtaja.")
    assert not by_name(got)


def test_deobfuscate_variants():
    assert "mike@acme.io" in deobfuscate("mike [at] acme [dot] io")
    assert "ola.n@firma.no" in deobfuscate("ola.n at firma.no")
    assert "look at github.com" in deobfuscate("look at github.com")   # plain words stay plain


def test_mailbox_kinds():
    assert mailbox_kind("rekry@firma.fi") == "recruiting"
    assert mailbox_kind("careers@firma.fi") == "recruiting"
    assert mailbox_kind("info@firma.fi") == "general"
    assert mailbox_kind("maija.meikalainen@firma.fi") == "person"
    assert mailbox_kind("noreply@firma.fi") == "ignore"
    assert mailbox_kind("logo@2x.png") == "ignore"
    assert mailbox_kind("etunimi.sukunimi@firma.fi") == "ignore"


def test_format_and_guess():
    assert email_format("osoitteet muotoa etunimi.sukunimi(at)hel.fi") == (".", "hel.fi")
    assert guess_email("Väinö Ääkkönen", ".", "firma.fi") == "vaino.aakkonen@firma.fi"
    assert guess_email("Cher", ".", "firma.fi") == ""
    assert name_from_email("jane.doe@acme.fi") == "Jane Doe"
    assert name_from_email("careers@acme.fi") == ""
