"""RSS 2.0 и Atom: заголовки и ссылки. Всё, что пришло из сети, чистится."""
import html
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from xml.etree.ElementTree import Element
from xml.etree.ElementTree import ParseError as XmlParseError

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from ..errors import ParseError

MAX_TITLE = 200
_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class NewsItem:
    title: str
    link: str


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _clean_title(text: str | None) -> str:
    plain = html.unescape(_TAGS.sub("", text or ""))
    return _SPACES.sub(" ", plain).strip()[:MAX_TITLE]


def _clean_link(url: str | None) -> str | None:
    """Только http(s); трекинговые utm_* вырезаются."""
    parts = urlsplit((url or "").strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_")]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def _child_text(node: Element, name: str) -> str | None:
    for child in node:
        if _local(child.tag) == name:
            return child.text
    return None


def _atom_link(node: Element) -> str | None:
    fallback = None
    for child in node:
        if _local(child.tag) != "link" or not child.get("href"):
            continue
        if child.get("rel") in (None, "alternate"):
            return child.get("href")
        fallback = fallback or child.get("href")
    return fallback


def parse_feed(raw: bytes, limit: int) -> list[NewsItem]:
    try:
        root = ElementTree.fromstring(raw)
    except (XmlParseError, DefusedXmlException, ValueError) as exc:
        raise ParseError(f"лента: не XML ({type(exc).__name__})") from exc

    kind = _local(root.tag)
    if kind == "rss":
        nodes = [n for n in root.iter() if _local(n.tag) == "item"]
        link_of = lambda n: _child_text(n, "link")   # noqa: E731
    elif kind == "feed":
        nodes = [n for n in root if _local(n.tag) == "entry"]
        link_of = _atom_link
    else:
        raise ParseError(f"лента: неизвестный формат <{kind}>")

    items: list[NewsItem] = []
    for node in nodes:
        title = _clean_title(_child_text(node, "title"))
        link = _clean_link(link_of(node))
        if title and link:
            items.append(NewsItem(title, link))
        if len(items) >= limit:
            break
    return items


async def fetch_feed(fetcher, url: str, limit: int) -> list[NewsItem]:
    return parse_feed(await fetcher.get(url), limit)
