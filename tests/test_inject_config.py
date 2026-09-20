from unittest.mock import MagicMock, patch

from micronote import stats
from micronote.app import app, inject_config


def test_inject_config_caches_counts():
    stats.clear_counts()

    mock_db = MagicMock()
    mock_db.activities.count_documents.return_value = 42

    with app.test_request_context("/"):
        with patch("micronote.stats.DB", mock_db):
            # First render: populates cache with 5 queries
            ctx1 = inject_config()
            assert ctx1["notes_count"] == 42
            assert mock_db.activities.count_documents.call_count == 5

            # Second render immediately after: hits the counts memo, no new DB queries
            ctx2 = inject_config()
            assert ctx2["notes_count"] == 42
            assert mock_db.activities.count_documents.call_count == 5
