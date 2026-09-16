import hashlib

from micronote.filters import permalink_id


def test_permalink_id_deterministic():
    uri = "https://example.com/activities/12345"
    expected = hashlib.sha256(uri.encode("utf-8")).hexdigest()[:12]
    assert permalink_id(uri) == expected
    assert permalink_id(uri) == permalink_id(uri)


def test_permalink_id_empty_and_none():
    assert permalink_id(None) == ""
    assert permalink_id("") == ""


def test_permalink_id_non_string():
    expected = hashlib.sha256(b"98765").hexdigest()[:12]
    assert permalink_id(98765) == expected


def test_permalink_id_format():
    result = permalink_id("sample-item-id")
    assert len(result) == 12
    # Verify it is hex characters only (safe for DOM element IDs)
    int(result, 16)
