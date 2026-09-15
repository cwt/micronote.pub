from bs4 import BeautifulSoup
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

_FORMATTER = HtmlFormatter(cssclass="codehilite")


def highlight_code_blocks(html: str) -> str:
    """Highlights fenced code blocks for display.

    Runs at render time only: the federated ``content`` keeps the plain
    ``<pre><code class="language-*">`` markup, so no presentation leaks
    into ActivityPub payloads.
    """
    if "<pre" not in html or "language-" not in html:
        return html
    soup = BeautifulSoup(html, "html.parser")
    for pre in soup.find_all("pre"):
        code = pre.find("code")
        if code is None:
            continue
        class_attr: str | list[str] | None = code.get("class") or []
        classes: list[str] = [class_attr] if isinstance(class_attr, str) else list(class_attr or [])
        lang = next(
            (cls.removeprefix("language-") for cls in classes if cls.startswith("language-")),
            None,
        )
        if not lang:
            continue
        try:
            lexer = get_lexer_by_name(lang)
        except ClassNotFound:
            continue
        highlighted = BeautifulSoup(highlight(code.get_text(), lexer, _FORMATTER), "html.parser")
        replacement = highlighted.div
        if replacement is None:
            continue
        pre.replace_with(replacement)
    return str(soup)
