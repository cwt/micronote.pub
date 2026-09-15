import logging

import opengraph
import requests
from active_boxes import activitypub as ap
from active_boxes.errors import NotAnActivityError
from active_boxes.urlutils import check_url, is_url_valid
from bs4 import BeautifulSoup

from .lookup import lookup

logger = logging.getLogger(__name__)


def links_from_note(note):
    tags_href = set()
    for t in note.get("tag", []):
        h = t.get("href")
        if h:
            tags_href.add(h)

    links = set()
    soup = BeautifulSoup(note["content"], 'html5lib')
    for link in soup.find_all("a"):
        h = link.get("href")
        if h.startswith("http") and h not in tags_href and is_url_valid(h):
            links.add(h)

    return links


def fetch_og_metadata(user_agent, links):
    res = []
    for link in links:
        check_url(link)

        # Remove any AP actor from the list
        try:
            p = lookup(link)
            if p.has_type(ap.ACTOR_TYPES):
                continue
        except NotAnActivityError:
            pass

        r = requests.get(link, headers={"User-Agent": user_agent}, timeout=15)
        r.raise_for_status()
        if not (r.headers.get("content-type") or "").startswith("text/html"):
            logger.debug(f"skipping {link}")
            continue

        r.encoding = 'UTF-8'
        html = r.text
        try:
            data = dict(opengraph.OpenGraph(
                html=BeautifulSoup(html, 'html5lib')
            ))
        except Exception:
            logger.exception(f"failed to parse {link}")
            continue
        if data.get("url"):
            res.append(data)

    return res
