import json

import active_boxes.activitypub as ap
import mf2py
import requests
from active_boxes.errors import ActivityNotFoundError
from active_boxes.webfinger import get_actor_url_sync


def lookup(url: str) -> ap.BaseActivity:
    """Try to find an AP object related to the given URL."""
    if url.startswith("@"):
        # A handle is not fetchable as-is; it must go through WebFinger.
        actor_url = get_actor_url_sync(url)
        if not actor_url:
            raise ActivityNotFoundError(f"cannot resolve {url}")
        return ap.fetch_remote_activity_sync(actor_url)

    backend = ap.get_backend()
    try:
        resp = requests.get(
            url,
            timeout=15,
            allow_redirects=False,
            headers={"User-Agent": backend.user_agent()},
        )
        if resp.ok:
            # If the page is HTML, maybe it contains an alternate link pointing to an AP object
            for alternate in mf2py.parse(resp.text).get("alternates", []):
                if alternate.get("type") == "application/activity+json":
                    return ap.fetch_remote_activity_sync(alternate["url"])

            try:
                # Maybe the page was JSON-LD?
                data = resp.json()
                return ap.parse_activity(data)
            except json.JSONDecodeError:
                pass
    except requests.RequestException:
        pass

    # Try content negotiation (retry with the AP Accept header / signed fetch)
    return ap.fetch_remote_activity_sync(url)
