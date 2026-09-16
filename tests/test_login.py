from micronote.utils.login import safe_next_url


def test_safe_next_url_valid_relative():
    assert safe_next_url("/", "/fallback") == "/"
    assert safe_next_url("/admin", "/fallback") == "/admin"
    assert safe_next_url("/admin/settings?section=general", "/fallback") == "/admin/settings?section=general"
    assert safe_next_url("/notes/1#section", "/fallback") == "/notes/1#section"


def test_safe_next_url_empty_or_none():
    assert safe_next_url(None, "/fallback") == "/fallback"
    assert safe_next_url("", "/fallback") == "/fallback"


def test_safe_next_url_external_schemes():
    assert safe_next_url("https://evil.com", "/fallback") == "/fallback"
    assert safe_next_url("http://evil.com/login", "/fallback") == "/fallback"
    assert safe_next_url("javascript:alert(1)", "/fallback") == "/fallback"
    assert safe_next_url("data:text/html,test", "/fallback") == "/fallback"


def test_safe_next_url_protocol_relative_and_slashes():
    assert safe_next_url("//evil.com", "/fallback") == "/fallback"
    assert safe_next_url("///evil.com", "/fallback") == "/fallback"
    assert safe_next_url("////evil.com", "/fallback") == "/fallback"
    assert safe_next_url("///evil.com/path", "/fallback") == "/fallback"
    assert safe_next_url("/\\evil.com", "/fallback") == "/fallback"
    assert safe_next_url("\\\\evil.com", "/fallback") == "/fallback"


def test_safe_next_url_relative_without_leading_slash():
    assert safe_next_url("admin/dashboard", "/fallback") == "/fallback"
