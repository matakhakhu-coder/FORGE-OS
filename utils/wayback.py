#!/usr/bin/env python3
from __future__ import annotations
"""
Zero-cost evidence preservation via the Internet Archive Wayback Machine.
Submits source URLs to the Save Page Now (SPN) API and returns the archive URL.
No API key required. Rate limit: 1 request per 5 seconds (courteous).
"""

import logging
import urllib.request

_log = logging.getLogger("forge.wayback")

SPN_URL = "https://web.archive.org/save/"
UA = "FORGE-OSINT/2.1 (evidence preservation; non-commercial)"


def archive_url(url: str, timeout: int = 15) -> str | None:
    if not url or not url.startswith("http"):
        return None
    try:
        req = urllib.request.Request(
            SPN_URL + url,
            method="GET",
            headers={"User-Agent": UA},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                archived = resp.url
                _log.info("[wayback] Archived: %s", archived[:80])
                return archived
    except Exception as exc:
        _log.debug("[wayback] Failed for %s: %s", url[:60], exc)
    return None
