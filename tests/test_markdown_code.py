"""Literal markdown code in federated content is rendered as code markup."""

from micronote.filters import clean, markdown_code
from micronote.utils.highlight import highlight_code_blocks


def test_fenced_block_with_br_separators():
    content = "<p>dumb identifiers such as</p><p>```<br />(person-name some-person)<br />```</p>"
    out = markdown_code(content)
    assert "<pre><code>(person-name some-person)</code></pre>" in out
    assert "```" not in out


def test_fenced_block_with_language():
    content = "```python<br />print('hi')<br />```"
    out = markdown_code(content)
    assert '<pre><code class="language-python">print(&#x27;hi&#x27;)</code></pre>' in out


def test_fenced_block_with_newline_separators():
    content = "```\nsome-person:name\n```"
    out = markdown_code(content)
    assert "<pre><code>some-person:name</code></pre>" in out


def test_inline_code():
    assert markdown_code("<p>use `defstruct` here</p>") == "<p>use <code>defstruct</code> here</p>"


def test_existing_code_is_untouched():
    content = '<pre><code class="language-python">`not` markdown</code></pre>'
    assert markdown_code(content) == content


def test_entities_are_not_double_escaped():
    content = "```<br />&lt;tag&gt; &amp; stuff<br />```"
    out = markdown_code(content)
    assert "&lt;tag&gt; &amp; stuff" in out


def test_raw_tags_in_fence_are_shown_as_text():
    content = "```<br /><script>alert(1)</script><br />```"
    out = markdown_code(content)
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_no_backticks_returns_unchanged():
    content = "<p>hello <strong>world</strong></p>"
    assert markdown_code(content) == content


def test_pipeline_sanitizes_and_highlights():
    content = "<p>```python<br />print(1)<br />```</p>"
    rendered = highlight_code_blocks(clean(markdown_code(content)))
    assert "codehilite" in rendered
    assert "```" not in rendered


def test_production_note_shape_end_to_end():
    content = (
        "<p>```<br />(person-name some-person)<br />```</p><p>invoke methods, as in `(object:method args ...)`.</p>"
    )
    out = clean(markdown_code(content))
    assert "```" not in out
    assert "<pre><code>(person-name some-person)</code></pre>" in out
    assert "<code>(object:method args ...)</code>" in out
    assert "<p></p>" not in out
