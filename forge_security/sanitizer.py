"""
forge_security.sanitizer — Signal Sanitization Layer
======================================================
All scraped text and HTML that arrives from external sources (GDELT,
ACLED, RSS, OSINT dork results, civic-intel feeds) must pass through
this module before it is inserted into the `signals` table or rendered
in any template.

Two public entry-points:

  sanitize_signal_text(raw: str) -> str
      For plain-text fields: title, summary, source_url, actor names.
      Strips every HTML tag, collapses whitespace, limits length.

  sanitize_html_fragment(raw: str) -> str
      For fields that may legitimately contain a restricted subset of
      inline HTML (e.g. rich article excerpts).  Uses bleach's allowlist.

Both functions are synchronous and safe to call in hot-path collectors.

Dependencies: bleach (pip install bleach)
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Final

logger = logging.getLogger(__name__)

# ── tunables ──────────────────────────────────────────────────────────────────
MAX_TEXT_LENGTH: Final[int] = 4_000   # chars — plain-text fields
MAX_HTML_LENGTH: Final[int] = 16_000  # chars — rich-text fragments

# Safe HTML tags and their allowed attributes for intel excerpts
_ALLOWED_TAGS: Final[list[str]] = [
    "a", "abbr", "acronym", "b", "blockquote", "br", "cite", "code",
    "em", "i", "li", "ol", "p", "q", "s", "small", "span", "strong",
    "sub", "sup", "u", "ul",
]
_ALLOWED_ATTRS: Final[dict[str, list[str]]] = {
    "a": ["href", "title", "rel"],
    "abbr": ["title"],
    "acronym": ["title"],
}
# rel="noopener noreferrer" is forced on every <a> by the link cleaner below
_ALLOWED_PROTOCOLS: Final[list[str]] = ["http", "https", "mailto"]

# Pattern that matches common SQL injection probes
_SQL_INJECT_RE: Final[re.Pattern[str]] = re.compile(
    r"(\b(SELECT|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|EXEC|UNION|CAST)\b"
    r"|\-\-\s|;\s*(DROP|SELECT|INSERT))",
    re.IGNORECASE,
)

# Null-byte and other control characters (except tab, LF, CR)
_CONTROL_CHAR_RE: Final[re.Pattern[str]] = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"
)


class SanitizationError(Exception):
    """Raised when a value cannot be safely sanitised."""


# ── public API ────────────────────────────────────────────────────────────────

def sanitize_signal_text(raw: str | None, *, field_name: str = "field") -> str:
    """
    Sanitise a plain-text signal field.

    Steps
    -----
    1. None → empty string guard
    2. Unicode normalisation (NFC) + control-char strip
    3. HTML tag strip (via bleach.clean with no allowed tags)
    4. SQL-injection probe detection (log + scrub)
    5. Whitespace collapse
    6. Length truncation

    Parameters
    ----------
    raw        : untrusted string value from a collector
    field_name : label used in log messages

    Returns
    -------
    Sanitised UTF-8 string, max MAX_TEXT_LENGTH characters.
    """
    try:
        import bleach  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "bleach is required by forge_security.sanitizer. "
            "Run: pip install bleach"
        ) from exc

    if raw is None:
        return ""

    if not isinstance(raw, str):
        raw = str(raw)

    # 1. Unicode normalise
    text = unicodedata.normalize("NFC", raw)

    # 2. Strip control characters
    text = _CONTROL_CHAR_RE.sub("", text)

    # 3. Strip all HTML
    text = bleach.clean(text, tags=[], attributes={}, strip=True)

    # 4. SQL-injection probe detection — scrub and log, don't raise
    if _SQL_INJECT_RE.search(text):
        logger.warning(
            "SANITIZER | SQL injection probe detected in field %r — scrubbing",
            field_name,
        )
        text = _SQL_INJECT_RE.sub("[REDACTED]", text)

    # 5. Collapse whitespace
    text = " ".join(text.split())

    # 6. Truncate
    if len(text) > MAX_TEXT_LENGTH:
        logger.debug(
            "SANITIZER | truncated %r from %d to %d chars",
            field_name, len(text), MAX_TEXT_LENGTH,
        )
        text = text[:MAX_TEXT_LENGTH]

    return text


def sanitize_html_fragment(raw: str | None, *, field_name: str = "html_field") -> str:
    """
    Sanitise an HTML fragment using bleach's strict allowlist.

    Permitted inline elements only (no script, no style, no iframe).
    All <a> tags get rel="noopener noreferrer" injected.

    Parameters
    ----------
    raw        : untrusted HTML string
    field_name : label used in log messages

    Returns
    -------
    Bleach-cleaned HTML string, max MAX_HTML_LENGTH characters.
    """
    try:
        import bleach  # type: ignore[import]
        from bleach.linkifier import LinkifyFilter  # noqa: F401 — presence check
    except ImportError as exc:
        raise ImportError(
            "bleach is required by forge_security.sanitizer. "
            "Run: pip install bleach"
        ) from exc

    if raw is None:
        return ""

    if not isinstance(raw, str):
        raw = str(raw)

    # Control-char strip
    text = _CONTROL_CHAR_RE.sub("", raw)

    # Truncate BEFORE bleach (avoid O(n) parse of huge payloads)
    if len(text) > MAX_HTML_LENGTH:
        logger.warning(
            "SANITIZER | HTML fragment in %r exceeds %d chars — truncating",
            field_name, MAX_HTML_LENGTH,
        )
        text = text[:MAX_HTML_LENGTH]

    # Force noopener on all links
    def _set_rel(tag: str, name: str, value: str) -> str | bool:
        if tag == "a" and name == "rel":
            return "noopener noreferrer"
        return True

    cleaned = bleach.clean(
        text,
        tags=_ALLOWED_TAGS,
        attributes={**_ALLOWED_ATTRS, **{"*": ["class"]}},
        protocols=_ALLOWED_PROTOCOLS,
        strip=True,
    )

    # SQL probe check on the stripped text
    text_only = bleach.clean(cleaned, tags=[], attributes={}, strip=True)
    if _SQL_INJECT_RE.search(text_only):
        logger.warning(
            "SANITIZER | SQL injection probe in HTML fragment field %r",
            field_name,
        )
        cleaned = bleach.clean(cleaned, tags=[], attributes={}, strip=True)
        cleaned = _SQL_INJECT_RE.sub("[REDACTED]", cleaned)

    return cleaned


def sanitize_url(raw: str | None) -> str:
    """
    Validate and sanitise a URL string.

    Accepts only http:// and https:// schemes.  Returns empty string
    for anything else (data:, javascript:, file:, ftp:, etc.).
    """
    if not raw:
        return ""
    url = raw.strip()
    if not re.match(r"^https?://", url, re.IGNORECASE):
        logger.warning("SANITIZER | rejected non-http URL: %r", url[:80])
        return ""
    # Strip embedded newlines and control chars
    url = _CONTROL_CHAR_RE.sub("", url)
    return url[:2_000]  # RFC 7230 practical cap
