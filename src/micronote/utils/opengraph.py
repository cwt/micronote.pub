import logging
import re
from urllib.parse import parse_qs, urlparse

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


_YT_HOSTS = {
    "youtu.be",
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _youtube_video_id(url: str) -> str | None:
    """Return the YouTube video ID if `url` is a YouTube video URL, else None.

    Handles youtu.be short links, watch?v=, and /shorts/, /embed/, /live/ paths.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in _YT_HOSTS:
        return None

    vid = None
    if host == "youtu.be":
        vid = parsed.path.lstrip("/").split("/")[0] or None
    else:
        v = parse_qs(parsed.query).get("v")
        if v:
            vid = v[0]
        else:
            segs = [s for s in parsed.path.split("/") if s]
            if len(segs) >= 2 and segs[0] in ("shorts", "embed", "live"):
                vid = segs[1]

    return vid if _VIDEO_ID_RE.match(vid or "") else None


def _youtube_card(video_id: str, user_agent: str) -> dict:
    """Deterministic link card for a YouTube video.

    YouTube's watch page is consent-gated when scraped server-side from a
    datacenter IP (no Open Graph tags), so build the card from the stable
    thumbnail CDN plus the lightweight oEmbed endpoint instead.
    """
    canonical = f"https://www.youtube.com/watch?v={video_id}"
    title, description = "YouTube video", ""
    try:
        oembed = requests.get(
            "https://www.youtube.com/oembed",
            params={"url": canonical, "format": "json"},
            headers={"User-Agent": user_agent},
            timeout=15,
        )
        oembed.raise_for_status()
        data = oembed.json()
        title = data.get("title") or "YouTube video"
        description = data.get("author_name") or ""
    except Exception:
        logger.debug("YouTube oEmbed unavailable for %s, using fallback title", video_id)

    return {
        "url": canonical,
        "title": title,
        "description": description,
        "site_name": "YouTube",
        "image": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
    }


def fetch_og_metadata(user_agent: str, links: set[str] | list[str]) -> list[dict]:
    res = []
    for link in links:
        try:
            check_url(link)

            # YouTube is consent-gated server-side; use its stable endpoints instead.
            video_id = _youtube_video_id(link)
            if video_id:
                res.append(_youtube_card(video_id, user_agent))
                continue

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
