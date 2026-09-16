import html
import re
from urllib.parse import urlparse

import emoji
from markupsafe import Markup


def flexmoji(html_text):
    html_text = emoji.emojize(html_text, language="alias")
    html_text = emoji.emojize(html_text, language="alias", delimiters=("blob_", ":"))
    html_text = emoji.emojize(html_text, language="alias", delimiters=("blob", ":"))
    return html_text


def unicode_emojize(text):
    """Converts :alias: shortcodes to Unicode emoji for federation output.

    Unlike flexmoji (which targets local HTML rendered with the BlobMoji
    font), this only maps standard aliases, so BlobMoji codes, unknown
    shortcodes and plain text pass through untouched. Remote nodes have
    no way to resolve our shortcodes, so the actor JSON and manifest
    must carry the real characters.
    """
    if not isinstance(text, str):
        return text
    return emoji.emojize(text, language="alias")


_SHORTCODE_RE = re.compile(r":([A-Za-z0-9_]+):")
_BARE_SHORTCODE_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _icon_url(icon) -> str | None:
    """Extracts the image URL from an Emoji tag icon (dict or list)."""
    match icon:
        case {"url": str(url)}:
            return url
        case [first, *_]:
            return _icon_url(first)
        case _:
            return None


def _is_safe_url(url: str | None) -> bool:
    if not url or not isinstance(url, str):
        return False
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def extract_custom_emojis(tags) -> dict[str, str]:
    """Builds a {shortcode: image_url} map from ActivityPub tag lists.

    Accepts Mastodon-style Emoji tags:
    {"type": "Emoji", "name": ":shortcode:", "icon": {"url": "..."}}.
    Only http(s) image URLs are kept, so the result is safe to render.
    """
    emojis: dict[str, str] = {}
    if not isinstance(tags, list):
        return emojis
    for tag in tags:
        if not isinstance(tag, dict):
            continue
        if str(tag.get("type", "")).lower() != "emoji":
            continue
        name = tag.get("name", "")
        if not isinstance(name, str):
            continue
        shortcode = name.strip(":")
        if not _BARE_SHORTCODE_RE.match(shortcode):
            continue
        url = _icon_url(tag.get("icon"))
        if url is not None and _is_safe_url(url):
            emojis[shortcode] = url
    return emojis


def render_custom_emojis(text: str | None, emojis: dict[str, str] | None) -> Markup:
    """Replaces :shortcode: occurrences with <img> tags.

    Everything else is HTML-escaped, unknown shortcodes are left as-is,
    so the result is safe to render unescaped.
    """
    if not text:
        return Markup("")
    if not emojis:
        return Markup(html.escape(text))
    parts = []
    pos = 0
    for match in _SHORTCODE_RE.finditer(text):
        shortcode = match.group(1)
        if shortcode not in emojis:
            continue
        parts.append(html.escape(text[pos : match.start()]))
        url = html.escape(emojis[shortcode], quote=True)
        parts.append(f'<img class="custom-emoji" src="{url}" alt=":{shortcode}:" title=":{shortcode}:" loading="lazy">')
        pos = match.end()
    parts.append(html.escape(text[pos:]))
    return Markup("".join(parts))
