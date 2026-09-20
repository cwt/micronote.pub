from active_boxes.activitypub import get_backend

from micronote import ap_routes, api, auth_views, instance, tasks, views, worker
from micronote.config import ID


def test_backend_and_person_are_singletons():
    """Verify that back and MY_PERSON are identical singleton objects across all modules (BUG-024)."""
    # Verify backend identity across modules
    assert api.back is instance.back
    assert ap_routes.back is instance.back
    assert tasks.back is instance.back
    assert views.back is instance.back
    assert worker.back is instance.back

    # Verify active_boxes global backend registration matches the singleton
    assert get_backend() is instance.back

    # Verify MY_PERSON identity across modules
    assert api.MY_PERSON is instance.MY_PERSON
    assert ap_routes.MY_PERSON is instance.MY_PERSON
    assert auth_views.MY_PERSON is instance.MY_PERSON
    assert tasks.MY_PERSON is instance.MY_PERSON
    assert worker.MY_PERSON is instance.MY_PERSON

    # Verify actor ID
    assert instance.MY_PERSON.id == ID
