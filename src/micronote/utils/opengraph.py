import logging

import opengraph
import requests
from active_boxes import activitypub as ap
from active_boxes.urlutils import check_url, is_url_valid
from bs4 import BeautifulSoup

from .lookup import lookup

logger = logging.getLogger(__name__)


def links_from_note(note: dict) -> set[str]:
    tags_href = {t["href"] for t in note.get("tag", []) if t.get("href")}

    links: set[str] = set()
    soup = BeautifulSoup(note["content"], "html5lib")
    for link in soup.find_all("a"):
        h = link.get("href")
        if isinstance(h, str) and h.startswith(("http://", "https://")) and h not in tags_href and is_url_valid(h):
            links.add(h)

    return links


def fetch_og_metadata(user_agent: str, links: set[str] | list[str]) -> list[dict]:
    res = []
    for link in links:
        try:
            check_url(link)

            # Remove any AP actor from the list
            try:
                p = lookup(link)
                if p.has_type(ap.ACTOR_TYPES):
                    continue
            except Exception:
                pass

            r = requests.get(link, headers={"User-Agent": user_agent}, timeout=15)
            r.raise_for_status()
            if not (r.headers.get("content-type") or "").startswith("text/html"):
                logger.debug(f"skipping {link}")
                continue

            r.encoding = "UTF-8"
            html = r.text
            try:
                data = dict(opengraph.OpenGraph(html=BeautifulSoup(html, "html5lib")))
            except Exception:
                logger.exception(f"failed to parse {link}")
                continue
            if data.get("url"):
                res.append(data)
        except Exception as exc:
            logger.warning(f"failed to fetch OG metadata for {link}: {exc}")
            continue

    return res
