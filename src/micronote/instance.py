"""Singleton instance holding the ActivityPub backend and Person instance."""

from active_boxes import activitypub as ap

from micronote import activitypub
from micronote.config import me

back = activitypub.MicroblogPubBackend()
ap.use_backend(back)
MY_PERSON = ap.Person(**me())

__all__ = ["MY_PERSON", "back"]
