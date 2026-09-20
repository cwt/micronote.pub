import re
from unittest.mock import MagicMock, patch

from micronote.app import app
from micronote.config import BASE_URL


def test_escaped_base_url_regex_pattern():
    escaped_base = re.escape(BASE_URL)
    pattern = re.compile(f"^{escaped_base}")

    # Should match valid paths on the same host
    assert pattern.match(f"{BASE_URL}/note/123") is not None

    # Should not match wildcard variations that unescaped dots would match
    if "." in BASE_URL:
        tampered_url = BASE_URL.replace(".", "x", 1)
        assert pattern.match(f"{tampered_url}/note/123") is None


def test_admin_notifications_query_uses_escaped_base():
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["logged_in"] = True

        mock_paginated = MagicMock(return_value=([], None, None))
        with (
            patch("micronote.admin.paginated_query", mock_paginated),
            patch("micronote.admin.render_template", return_value="OK"),
        ):
            resp = client.get("/admin/notifications")
            assert resp.status_code == 200

            call_args = mock_paginated.call_args
            assert call_args is not None
            query = call_args[0][1]
            escaped_base = re.escape(BASE_URL)
            or_conditions = query.get("$or", [])

            # Check that replies_query, announced_query, and likes_query used escaped_base
            found_regexes = [
                cond[field]["$regex"]
                for cond in or_conditions
                for field in ["activity.object.inReplyTo", "activity.object"]
                if field in cond and "$regex" in cond[field]
            ]
            assert len(found_regexes) == 3
            for regex_val in found_regexes:
                assert regex_val == f"^{escaped_base}"


def test_admin_dashboard_does_not_query_or_pass_instances():
    app.config["TESTING"] = True

    mock_db = MagicMock()
    mock_db.activities.count_documents.return_value = 10

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["logged_in"] = True

        with (
            patch("micronote.admin.DB", mock_db),
            patch("micronote.admin.render_template", return_value="OK") as mock_render,
        ):
            resp = client.get("/admin")
            assert resp.status_code == 200
            assert mock_render.call_count == 1
            template_name, kwargs = mock_render.call_args[0][0], mock_render.call_args[1]
            assert template_name == "admin.html"
            assert "instances" not in kwargs
            # Ensure DB.instances was never accessed
            assert mock_db.instances.find.call_count == 0
