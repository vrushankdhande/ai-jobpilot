"""An e-mail's HTML as a flat reading-order stream: text lines and link markers. Unlike a per-link text reader, link text is kept as
ordinary lines (also when links are nested, which some job-alert e-mails do: a whole job card is a link that contains an "Apply" link).
Nothing is fetched; this only reads the HTML it is given."""
import re
from html.parser import HTMLParser

_BLOCK = {"p", "div", "br", "tr", "td", "th", "li", "table", "h1", "h2", "h3", "h4", "h5", "h6", "section", "span"}
_SKIP = {"script", "style", "head", "title"}


class FlatStream(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items: list[tuple[str, str]] = []      # ("link", href) | ("text", line)
        self._line: list[str] = []
        self._skip = 0

    def _flush(self):
        t = re.sub(r"\s+", " ", "".join(self._line)).strip()
        if t:
            self.items.append(("text", t))
        self._line = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip += 1
        elif tag == "a":
            self._flush()
            self.items.append(("link", dict(attrs).get("href") or ""))
        elif tag in _BLOCK:
            self._flush()

    def handle_endtag(self, tag):
        if tag in _SKIP and self._skip:
            self._skip -= 1
        elif tag == "a" or tag in _BLOCK:
            self._flush()

    def handle_data(self, data):
        if not self._skip:
            self._line.append(data)

    def close(self):
        super().close()
        self._flush()


def flat_items(html: str) -> list[tuple[str, str]]:
    p = FlatStream()
    p.feed(html or "")
    p.close()
    return p.items
