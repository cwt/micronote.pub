"""Render literal markdown code syntax found in federated note content.

Remote servers often deliver plain-text posts whose authors typed markdown
code fences or inline code; the backticks arrive as literal text. This pass
turns them into real ``<pre><code>`` / ``<code>`` markup at render time. It
runs before sanitization, so bleach validates the generated HTML.
"""

import html
import re

# A fence opens with ```lang, then a line break (federated content uses
# <br /> for line breaks), the code, another line break, and a closing ```.
FENCED_CODE_RE = re.compile(
    r"```([A-Za-z0-9_+.#-]*)[ \t]*(?:<br\s*/?>|\n)(.*?)(?:<br\s*/?>|\n)```",
    re.DOTALL | re.IGNORECASE,
)
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
TAG_RE = re.compile(r"(<[^>]+>)")
BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
# A paragraph holding only a generated code block would sanitize into an
# empty <p> next to the <pre>; unwrap it so no stray paragraph remains.
PRE_ONLY_PARAGRAPH_RE = re.compile(r"<p>\s*(<pre>.*?</pre>)\s*</p>", re.DOTALL | re.IGNORECASE)


def _code_text(raw: str) -> str:
    """Plain text for a code block: <br> becomes a newline, tags stay text."""
    return html.escape(html.unescape(BR_RE.sub("\n", raw)))


def _fence_repl(match: re.Match) -> str:
    lang = match.group(1)
    class_attr = f' class="language-{lang}"' if lang else ""
    return f"<pre><code{class_attr}>{_code_text(match.group(2))}</code></pre>"


def _inline_repl(match: re.Match) -> str:
    return f"<code>{html.escape(html.unescape(match.group(1)))}</code>"


def _render_inline_code(content: str) -> str:
    parts = []
    in_code = 0
    for token in TAG_RE.split(content):
        if not token:
            continue
        if token.startswith("<") and token.endswith(">"):
            tag = token.lower()
            if tag.startswith("<pre") or tag.startswith("<code"):
                in_code += 1
            elif tag.startswith("</pre") or tag.startswith("</code>"):
                in_code = max(0, in_code - 1)
            parts.append(token)
        elif in_code:
            parts.append(token)
        else:
            parts.append(INLINE_CODE_RE.sub(_inline_repl, token))
    return "".join(parts)


def render_literal_markdown_code(content: str) -> str:
    """Converts literal ``` fences and `spans` in HTML content to code markup."""
    if "`" not in content:
        return content
    content = FENCED_CODE_RE.sub(_fence_repl, content)
    content = PRE_ONLY_PARAGRAPH_RE.sub(r"\1", content)
    return _render_inline_code(content)
