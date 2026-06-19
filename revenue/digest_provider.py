#!/usr/bin/env python3
from __future__ import annotations
"""
Digest email provider abstraction.
SimulatedProvider is the default — logs to console, writes HTML to disk.
Real providers activated when REVENUE_LIVE = True and DIGEST_PROVIDER is set.
"""

import urllib.request
import json


class DigestProvider:
    def send(self, html: str, subject: str, *, preview: bool = False) -> bool:
        raise NotImplementedError


class SimulatedProvider(DigestProvider):
    def send(self, html: str, subject: str, *, preview: bool = False) -> bool:
        print(f"[digest-sim] Would send: \"{subject}\" ({len(html):,} bytes)")
        if preview:
            print(f"[digest-sim] Preview saved to dist/digest.html")
        return True


class ButtondownProvider(DigestProvider):
    def __init__(self, api_key: str):
        self.api_key = api_key

    def send(self, html: str, subject: str, *, preview: bool = False) -> bool:
        data = json.dumps({
            "subject": subject,
            "body": html,
            "status": "draft" if preview else "published",
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.buttondown.com/v1/emails",
            data=data,
            headers={
                "Authorization": f"Token {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                print(f"[digest] Buttondown: {resp.status} ({'draft' if preview else 'sent'})")
                return resp.status in (200, 201)
        except Exception as exc:
            print(f"[digest] Buttondown error: {exc}")
            return False


class ResendProvider(DigestProvider):
    def __init__(self, api_key: str, from_addr: str = "digest@za-divergent.com"):
        self.api_key = api_key
        self.from_addr = from_addr

    def send(self, html: str, subject: str, *, preview: bool = False) -> bool:
        if preview:
            print(f"[digest-resend] Preview mode — not sending")
            return True
        data = json.dumps({
            "from": self.from_addr,
            "to": ["subscribers@za-divergent.com"],
            "subject": subject,
            "html": html,
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                print(f"[digest] Resend: {resp.status}")
                return resp.status in (200, 201)
        except Exception as exc:
            print(f"[digest] Resend error: {exc}")
            return False


def get_provider() -> DigestProvider:
    try:
        from revenue.config import DIGEST_PROVIDER, DIGEST_API_KEY, REVENUE_LIVE
    except ImportError:
        return SimulatedProvider()

    if not REVENUE_LIVE or not DIGEST_PROVIDER:
        return SimulatedProvider()
    if DIGEST_PROVIDER == "buttondown":
        return ButtondownProvider(DIGEST_API_KEY or "")
    if DIGEST_PROVIDER == "resend":
        return ResendProvider(DIGEST_API_KEY or "")
    return SimulatedProvider()
