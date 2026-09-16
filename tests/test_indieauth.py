"""Unit tests for IndieAuth helper functions and scope normalization."""

from micronote.indieauth import _normalize_scope


def test_normalize_scope_string_preserved():
    assert _normalize_scope("create update") == "create update"
    assert _normalize_scope("create") == "create"
    assert _normalize_scope("") == ""


def test_normalize_scope_list_joined():
    assert _normalize_scope(["create", "update"]) == "create update"
    assert _normalize_scope(["read"]) == "read"
    assert _normalize_scope([]) == ""


def test_normalize_scope_invalid():
    assert _normalize_scope(None) == ""
    assert _normalize_scope(123) == ""
