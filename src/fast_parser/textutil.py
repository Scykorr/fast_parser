from __future__ import annotations

import hashlib
import re
from html import unescape
from html.parser import HTMLParser

# Bodies shorter than this are announcements. Their hash keeps the URL so two
# different short headlines do not collapse into one article. Longer bodies
# hash as title + text, which catches the same story under a rewritten URL.
SHORT_TEXT_CHARS = 180

_WS = re.compile(r"\s+")


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip += 1
        if tag in {"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "blockquote"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip:
            self._skip -= 1
        if tag in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "blockquote"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def normalize_ws(value: str) -> str:
    return _WS.sub(" ", value).strip()


def html_to_text(value: str | None) -> str:
    if not value:
        return ""
    raw = value.strip()
    if "<" not in raw and "&" not in raw:
        return normalize_ws(raw)
    parser = _HTMLText()
    parser.feed(raw)
    parser.close()
    return normalize_ws(unescape("".join(parser.parts)))


def content_hash(title: str, text: str, canonical: str) -> str:
    title_n = normalize_ws(title).casefold()
    text_n = normalize_ws(text).casefold()
    if len(text_n) < SHORT_TEXT_CHARS:
        material = f"{title_n}\n{text_n}\n{canonical}"
    else:
        material = f"{title_n}\n{text_n}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
