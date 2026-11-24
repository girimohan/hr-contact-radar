import pytest

from hrradar import secure


def test_roundtrip_and_wrong_passphrase():
    blob = secure.encrypt("Neea Turunen <neea@x.fi>".encode(), "correct horse battery")
    assert b"Neea" not in blob
    assert secure.decrypt(blob, "correct horse battery").decode() == "Neea Turunen <neea@x.fi>"
    with pytest.raises(secure.BadPassphrase):
        secure.decrypt(blob, "wrong passphrase!!")


def test_state_only_restores_known_files(tmp_path):
    (tmp_path / "private").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "private" / "site_cache.json").write_text('{"a": 1}')
    (tmp_path / "data" / "seen.json").write_text("{}")
    blob = secure.pack_state(tmp_path, "pass phrase 123")
    dest = tmp_path / "restore"
    got = secure.unpack_state(blob, dest, "pass phrase 123")
    assert sorted(got) == ["data/seen.json", "private/site_cache.json"]
    assert (dest / "private" / "site_cache.json").read_text() == '{"a": 1}'


def test_sealed_page_hides_contents():
    page = secure.seal_page("<html>secret.person@company.fi</html>", "pass phrase 123")
    assert "secret.person" not in page and "__PAYLOAD__" not in page
