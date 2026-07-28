"""
Regulatory & financial-crime news monitor.

Pulls headlines from local (Mauritius) and international AML/sanctions sources
via public RSS/Atom feeds, tags them by relevance, and returns a de-duplicated,
recency-sorted feed. Uses only the stdlib for parsing so it has no hard
dependency; `requests` is used for fetching when available.

Add or remove feeds in FEEDS. Some regulator sites publish RSS; where they do
not, point at a reputable aggregator or the FATF/press feed.
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime

try:
    import requests
except Exception:
    requests = None

FEEDS = [
    # (source label, url, scope)
    ("FATF", "https://www.fatf-gafi.org/en/the-fatf/news.rss", "international"),
    ("OFAC Recent Actions", "https://ofac.treasury.gov/system/files/rss/recent_actions.xml", "international"),
    ("UN Security Council", "https://press.un.org/en/rss.xml", "international"),
    # Local coverage often via general news feeds — filtered by keyword below:
    ("Le Mauricien", "https://www.lemauricien.com/feed/", "mauritius"),
    ("Defimedia", "https://defimedia.info/rss.xml", "mauritius"),
]

# Relevance keywords — a headline must hit at least one to be kept.
KEYWORDS = [
    "money laundering", "aml", "cft", "sanction", "fatf", "terrorist financing",
    "proliferation", "fiu", "fsc", "financial crimes commission", "fcc",
    "bank of mauritius", "compliance", "beneficial owner", "kyc", "fraud",
    "corruption", "amla", "blanchiment", "sanctions", "delit financier",
    "designation", "watchlist", "freeze", "asset recovery",
]


@dataclass
class NewsItem:
    source: str
    scope: str
    title: str
    link: str
    published: datetime | None
    summary: str = ""

    @property
    def date_str(self) -> str:
        return self.published.strftime("%Y-%m-%d") if self.published else "—"


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    return html.unescape(text).strip()


def _parse_date(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw)
    except Exception:
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
            try:
                return datetime.strptime(raw, fmt)
            except Exception:
                continue
    return None


def _parse_feed(raw: bytes, source: str, scope: str) -> list[NewsItem]:
    items: list[NewsItem] = []
    try:
        root = ET.fromstring(raw)
    except Exception:
        return items
    # RSS <item> and Atom <entry>
    nodes = root.iter("item")
    for node in nodes:
        title = _clean(node.findtext("title", ""))
        link = (node.findtext("link", "") or "").strip()
        desc = _clean(node.findtext("description", ""))
        pub = _parse_date(node.findtext("pubDate", ""))
        if title:
            items.append(NewsItem(source, scope, title, link, pub, desc[:280]))
    # Atom fallback
    ns = "{http://www.w3.org/2005/Atom}"
    for node in root.iter(f"{ns}entry"):
        title = _clean(node.findtext(f"{ns}title", ""))
        link_el = node.find(f"{ns}link")
        link = link_el.get("href", "") if link_el is not None else ""
        summ = _clean(node.findtext(f"{ns}summary", ""))
        pub = _parse_date(node.findtext(f"{ns}updated", "") or node.findtext(f"{ns}published", ""))
        if title:
            items.append(NewsItem(source, scope, title, link, pub, summ[:280]))
    return items


def _relevant(item: NewsItem, require_keyword: bool) -> bool:
    if not require_keyword:
        return True
    blob = f"{item.title} {item.summary}".lower()
    return any(k in blob for k in KEYWORDS)


def fetch_news(limit: int = 40, timeout: int = 15) -> tuple[list[NewsItem], list[str]]:
    """Return (items, errors). International feeds are AML-native; local feeds
    are keyword-filtered for relevance."""
    if requests is None:
        return [], ["`requests` not installed — cannot fetch live news."]
    collected: list[NewsItem] = []
    errors: list[str] = []
    for source, url, scope in FEEDS:
        try:
            r = requests.get(url, timeout=timeout,
                            headers={"User-Agent": "MauritiusFCCSuite/0.1"})
            r.raise_for_status()
            items = _parse_feed(r.content, source, scope)
            require_kw = scope == "mauritius"
            collected.extend(i for i in items if _relevant(i, require_kw))
        except Exception as e:
            errors.append(f"{source}: {e}")
    # de-dup by title
    seen = set()
    unique = []
    for it in collected:
        key = it.title.lower()[:80]
        if key not in seen:
            seen.add(key)
            unique.append(it)
    unique.sort(key=lambda i: (i.published or datetime.min.replace(tzinfo=None)
                               if i.published is None else i.published),
                reverse=True)
    return unique[:limit], errors
