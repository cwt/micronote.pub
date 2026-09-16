from unittest.mock import MagicMock, patch

from micronote.dedup import main, remove_duplicate_follows


def test_remove_duplicate_follows_with_string_and_dict():
    mock_activities = MagicMock()
    mock_docs = [
        # First occurrence (string)
        {
            "_id": "id-1",
            "activity": {"object": "https://remote.server/users/alice"},
        },
        # Duplicate occurrence (string)
        {
            "_id": "id-2",
            "activity": {"object": "https://remote.server/users/alice"},
        },
        # First occurrence (embedded dict)
        {
            "_id": "id-3",
            "activity": {"object": {"id": "https://remote.server/users/bob", "type": "Person"}},
        },
        # Duplicate occurrence (embedded dict)
        {
            "_id": "id-4",
            "activity": {"object": {"id": "https://remote.server/users/bob"}},
        },
        # Duplicate occurrence (string matching previous dict)
        {
            "_id": "id-5",
            "activity": {"object": "https://remote.server/users/bob"},
        },
        # Malformed activity (should be skipped without error)
        {
            "_id": "id-6",
            "activity": {"object": None},
        },
        {
            "_id": "id-7",
            "activity": {"object": {}},
        },
    ]
    mock_activities.find.return_value = mock_docs

    with patch("micronote.dedup.DB.activities", mock_activities):
        remove_duplicate_follows("outbox", "object", "following")

    assert mock_activities.delete_one.call_count == 3
    mock_activities.delete_one.assert_any_call({"_id": "id-2"})
    mock_activities.delete_one.assert_any_call({"_id": "id-4"})
    mock_activities.delete_one.assert_any_call({"_id": "id-5"})


def test_dedup_main():
    with patch("micronote.dedup.remove_duplicate_follows") as mock_remove:
        main()
        assert mock_remove.call_count == 2
