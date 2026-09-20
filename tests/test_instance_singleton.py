from active_boxes.activitypub import get_backend

from micronote import api, auth_views, handlers, instance, repository, tasks
from micronote.config import ID


def test_backend_and_person_are_singletons():
    """Verify that back and MY_PERSON are identical singleton objects across all modules (BUG-024)."""
    # Verify backend identity across modules
    assert repository.back is instance.back
    assert handlers.back is instance.back
    assert tasks.back is instance.back

    # Verify active_boxes global backend registration matches the singleton
    assert get_backend() is instance.back

    # Verify MY_PERSON identity across modules
    assert api.MY_PERSON is instance.MY_PERSON
    assert auth_views.MY_PERSON is instance.MY_PERSON
    assert handlers.MY_PERSON is instance.MY_PERSON
    assert tasks.MY_PERSON is instance.MY_PERSON

    # Verify actor ID
    assert instance.MY_PERSON.id == ID
