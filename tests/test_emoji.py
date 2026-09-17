"""Unit tests for Mastodon-style custom emojis (no server needed)."""

from micronote.utils.emoji import (
    extract_custom_emojis,
    render_custom_emojis,
    render_custom_emojis_in_html,
    unicode_emojize,
)

CHICK_TAG = {
    "type": "Emoji",
    "name": ":chick_0154:",
    "icon": {"type": "Image", "url": "https://miraiverse.xyz/emoji/chick/chick_0154.png"},
}


def test_extract_mastodon_emoji_tag():
    assert extract_custom_emojis([CHICK_TAG]) == {"chick_0154": "https://miraiverse.xyz/emoji/chick/chick_0154.png"}


def test_extract_ignores_non_emoji_tags():
    tags = [CHICK_TAG, {"type": "Hashtag", "name": "#art"}, "junk", None, {}]
    assert extract_custom_emojis(tags) == {"chick_0154": "https://miraiverse.xyz/emoji/chick/chick_0154.png"}


def test_extract_rejects_unsafe_urls():
    tags = [
        {"type": "Emoji", "name": ":evil:", "icon": {"url": "javascript:alert(1)"}},
        {"type": "Emoji", "name": ":noscheme:", "icon": {"url": "//example.com/x.png"}},
        {"type": "Emoji", "name": ":bad name:", "icon": {"url": "https://example.com/x.png"}},
    ]
    assert extract_custom_emojis(tags) == {}


def test_extract_accepts_icon_list():
    tags = [{"type": "Emoji", "name": ":list:", "icon": [CHICK_TAG["icon"]]}]
    assert extract_custom_emojis(tags) == {"list": "https://miraiverse.xyz/emoji/chick/chick_0154.png"}


def test_extract_handles_non_list():
    assert extract_custom_emojis(None) == {}
    assert extract_custom_emojis({}) == {}


def test_render_substitutes_known_shortcodes():
    out = render_custom_emojis(
        "SukinoVERSE :chick_0154:", {"chick_0154": "https://miraiverse.xyz/emoji/chick/chick_0154.png"}
    )
    assert out == (
        "SukinoVERSE "
        '<img class="custom-emoji"'
        ' src="https://miraiverse.xyz/emoji/chick/chick_0154.png"'
        ' alt=":chick_0154:" title=":chick_0154:" loading="lazy">'
    )


def test_render_escapes_html_and_keeps_unknown():
    out = render_custom_emojis("<b>hi</b> :unknown:", {"chick_0154": "https://example.com/x.png"})
    assert out == "&lt;b&gt;hi&lt;/b&gt; :unknown:"


def test_render_handles_empty():
    assert render_custom_emojis("", {"a": "https://example.com/x.png"}) == ""
    assert render_custom_emojis(None, None) == ""
    assert render_custom_emojis("plain :x:", None) == "plain :x:"
    assert render_custom_emojis("plain :x:", {}) == "plain :x:"


def test_unicode_emojize_converts_aliases():
    assert unicode_emojize("Just a Pythonista :snake:") == "Just a Pythonista 🐍"


def test_unicode_emojize_leaves_the_rest_alone():
    assert unicode_emojize(":blobsmile:") == ":blobsmile:"
    assert unicode_emojize(":notarealalias:") == ":notarealalias:"
    assert unicode_emojize("time 10:30:45 ok") == "time 10:30:45 ok"
    assert unicode_emojize("already 🐍 here") == "already 🐍 here"
    assert unicode_emojize(None) is None
    assert unicode_emojize(42) == 42


def test_render_custom_emojis_url_resolver():
    out = render_custom_emojis(
        "Hello :chick_0154:",
        {"chick_0154": "https://miraiverse.xyz/emoji.png"},
        url_resolver=lambda _: "/media/12345",
    )
    assert '<img class="custom-emoji" src="/media/12345"' in out


def test_render_custom_emojis_in_html_preserves_markup_and_replaces_shortcodes():
    html_input = '<p>Hello :chick_0154: world! :snake: <a href="https://example.com/:not_an_emoji:">link</a></p>'
    emojis = {"chick_0154": "https://miraiverse.xyz/emoji/chick/chick_0154.png"}
    out = render_custom_emojis_in_html(html_input, emojis)
    assert '<a href="https://example.com/:not_an_emoji:">link</a>' in out
    assert (
        '<img class="custom-emoji" src="https://miraiverse.xyz/emoji/chick/chick_0154.png" alt=":chick_0154:" title=":chick_0154:" loading="lazy">'
        in out
    )
    assert "🐍" in out


def test_render_custom_emojis_in_html_ignores_code_blocks():
    html_input = '<pre><code class="language-python">x = ":chick_0154:"</code></pre><p>outside: :chick_0154:</p>'
    emojis = {"chick_0154": "https://miraiverse.xyz/emoji/chick/chick_0154.png"}
    out = render_custom_emojis_in_html(html_input, emojis)
    assert '<code class="language-python">x = ":chick_0154:"</code>' in out
    assert '<p>outside: <img class="custom-emoji"' in out


def test_render_custom_emojis_in_html_handles_empty():
    assert render_custom_emojis_in_html("") == ""
    assert render_custom_emojis_in_html(None) == ""


def test_render_custom_emojis_in_html_supports_url_resolver():
    html_input = "<p>Note with :drgn_hyper:</p>"
    emojis = {"drgn_hyper": "https://remote/drgn.png"}
    out = render_custom_emojis_in_html(html_input, emojis, url_resolver=lambda _: "/media/abc")
    assert '<img class="custom-emoji" src="/media/abc"' in out
