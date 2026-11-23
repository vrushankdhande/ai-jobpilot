"""Small dependency-free HTML -> readable text converter (keeps bullets, so the JD parser can use them)."""
import html
import re
from html.parser import HTMLParser

_BLOCK = {"p", "div", "br", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "section", "article", "table"}
_SKIP = {"script", "style", "noscript", "template"}


class _Extractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip += 1
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP and self._skip:
            self._skip -= 1
        elif tag in _BLOCK or tag == "li":
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(raw: str | None) -> str:
    if not raw:
        return ""
    text = raw
    # Greenhouse returns HTML escaped (&lt;p&gt;...): unescape once when it looks escaped.
    if "&lt;" in text and "<" not in text:
        text = html.unescape(text)
    parser = _Extractor()
    parser.feed(text)
    parser.close()
    out = "".join(parser.parts).replace("\xa0", " ")
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"[ \t]*\n[ \t]*", "\n", out)
    return re.sub(r"\n{3,}", "\n\n", out).strip()
