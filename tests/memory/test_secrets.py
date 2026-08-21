from memorylib import secrets


def test_scan_flags_a_known_credential():
    hits = secrets.scan_secret("my key is " + "AKIA" + "ABCDEFGHIJKLMNOP here")
    assert "aws_access_key_id" in hits


def test_scan_clean_text_returns_empty():
    assert secrets.scan_secret("the quarterly budget is finalized") == []


def test_load_patterns_returns_nonempty_list_of_dicts():
    pats = secrets.load_patterns()
    assert isinstance(pats, list) and pats and all("regex" in p and "name" in p for p in pats)
