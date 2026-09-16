from unittest.mock import MagicMock

import pytest
from neosqlite.objectid import ObjectId
from werkzeug.exceptions import BadRequest

from micronote.activitypub import build_ordered_collection


def test_build_ordered_collection_query_immutability():
    mock_col = MagicMock()
    mock_col.name = "outbox"
    mock_col.count_documents.return_value = 10
    oid1 = ObjectId()
    oid2 = ObjectId()
    mock_col.find.return_value.sort.return_value = [
        {"_id": oid1, "type": "Create"},
        {"_id": oid2, "type": "Create"},
    ]

    original_query = {"box": "outbox", "type": "Create"}
    query_copy = original_query.copy()

    cursor_val = str(ObjectId())
    res = build_ordered_collection(mock_col, q=original_query, cursor=cursor_val, limit=2)

    # original query must not be mutated
    assert original_query == query_copy
    assert "_id" not in original_query

    # totalItems must be computed on base query
    mock_col.count_documents.assert_called_once_with(query_copy)
    assert res["totalItems"] == 10


def test_build_ordered_collection_empty_page_with_cursor():
    mock_col = MagicMock()
    mock_col.name = "inbox"
    mock_col.count_documents.return_value = 42
    mock_col.find.return_value.sort.return_value = []

    cursor_val = str(ObjectId())
    res = build_ordered_collection(mock_col, q={"box": "inbox"}, cursor=cursor_val)

    assert res["totalItems"] == 42
    assert res["orderedItems"] == []


def test_build_ordered_collection_invalid_cursor():
    mock_col = MagicMock()
    mock_col.name = "outbox"
    mock_col.count_documents.return_value = 5

    with pytest.raises(BadRequest):
        build_ordered_collection(mock_col, cursor="invalid-not-an-objectid")
