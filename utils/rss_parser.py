#!/usr/bin/env python3
"""
FORGE — RSS Parser Utility
==========================
Thin wrapper around feedparser to normalise RSS feeds (e.g. Google News)
into simple article dicts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional
import urllib.parse

try:
    import feedparser  # type: ignore
except Exception:  # pragma: no cover
    feedparser = None  # type: ignore


@dataclass
class ParsedArticle:
    title: str
    link: str
    published: Optional[str]
    summary: str


def parse_rss(url: str, limit: int = 5) -> List[ParsedArticle]:
    """
    Parse an RSS/Atom feed URL and return up to `limit` normalised articles.

    Each article contains:
      - title
      - link
      - published (ISO8601 string, UTC) if available
      - summary (plain text)
    """
    if not feedparser:
        return []

    feed = feedparser.parse(url)
    entries = getattr(feed, "entries", []) or []

    articles: List[ParsedArticle] = []
    for entry in entries[: max(limit, 0)]:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or entry.get("id") or "").strip()

        # Brute force fix: if link is Google News RSS articles URL, change to search URL to avoid XML
        if link and 'news.google.com/rss/articles' in link:
            link = f"https://news.google.com/search?q={urllib.parse.quote(title)}&hl=en-ZA&gl=ZA&ceid=ZA:en"

        # Summary / description
        summary = (entry.get("summary") or "").strip()
        if not summary and entry.get("content"):
            try:
                summary = entry["content"][0].get("value", "").strip()
            except (IndexError, AttributeError, KeyError):
                pass

        # Published timestamp
        published_iso: Optional[str] = None
        if entry.get("published_parsed"):
            try:
                dt = datetime(*entry["published_parsed"][:6], tzinfo=timezone.utc)
                published_iso = dt.isoformat()
            except Exception:
                pass
        if not published_iso:
            published_iso = datetime.now(timezone.utc).isoformat()

        if not title and not link:
            continue

        articles.append(
            ParsedArticle(
                title=title or link,
                link=link,
                published=published_iso,
                summary=summary,
            )
        )

    return articles

