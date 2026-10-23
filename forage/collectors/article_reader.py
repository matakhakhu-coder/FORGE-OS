"""Article reader for shortlisted signals (Batch 6, step 1).

Reads the article behind a shortlisted signal so later steps can use its own
published date, its body text and an incident date taken from the text.

Not wired into any collector yet. Nothing here runs unless a caller asks.

Rules this module keeps (from the Batch 7 terms-of-use check, 23 Oct 2026):
  * Only shortlisted signals are read. ``read_signal`` refuses anything else.
  * Outlets whose terms or robots notice need written permission for automated
    reading are never fetched unless a permission is recorded in
    ``data/article_reader_permissions.json``. Today that is Caxton's sites
    (citizen.co.za, which carries Review Online and Northern Natal News; terms
    clause 2.7.1) and the Newcastillian (robots notice; caching needs written
    approval).
  * The Limpopo Mirror and Zoutpansberger are leads only: headline, link and
    date. Their article text is never fetched, and their database key is never
    used (this module has no code path that could use it).
  * Fetching is polite: an identified user agent, robots.txt obeyed, one request
    per host at a time spaced out, timeouts, a size cap, no paid service and no
    key of any kind.
  * A Google News redirect that can't be turned into the real article link is
    flagged ``feed_date_only``. Nothing is guessed.
  * The cache is local only (``data/cache/article_reader``, git-ignored) and
    entries expire.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Iterable, Optional
from urllib import robotparser
from urllib.parse import urlparse

try:  # bs4 and lxml are already in requirements.txt
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover
    BeautifulSoup = None  # type: ignore

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_CACHE_DIR = os.path.join(REPO_ROOT, "data", "cache", "article_reader")
PERMISSIONS_FILE = os.path.join(REPO_ROOT, "data", "article_reader_permissions.json")

USER_AGENT = (
    "FORGE-ArticleReader/0.1 (+https://forge-os-alpha.vercel.app/about; "
    "reads one article per shortlisted signal)"
)

# ---------------------------------------------------------------------------
# Outlet policy
# ---------------------------------------------------------------------------

POLICY_OPEN = "open"                      # fetch if robots.txt allows
POLICY_NEEDS_PERMISSION = "needs_permission"  # never fetch without a recorded written permission
POLICY_LEAD_ONLY = "lead_only"            # headline, link and date only; never fetch the body

# Host suffix -> (policy, reason). Matched on the end of the host name.
OUTLET_POLICIES: dict[str, tuple[str, str]] = {
    "citizen.co.za": (
        POLICY_NEEDS_PERMISSION,
        "Caxton terms clause 2.7.1 bans automated monitoring or copying without prior "
        "written authority (covers Review Online and Northern Natal News)",
    ),
    "reviewonline.co.za": (POLICY_NEEDS_PERMISSION, "Caxton title; terms clause 2.7.1"),
    "northernnatalnews.co.za": (POLICY_NEEDS_PERMISSION, "Caxton title; terms clause 2.7.1"),
    "newcastillian.com": (
        POLICY_NEEDS_PERMISSION,
        "robots.txt notice bans automated collection without express written "
        "permission; terms say caching needs written approval",
    ),
    "limpopomirror.co.za": (
        POLICY_LEAD_ONLY,
        "Batch 7 finding: headline, link and date only; no article text, no database key",
    ),
    "zoutpansberger.co.za": (POLICY_LEAD_ONLY, "Batch 7 finding: headline, link and date only"),
    "zoutnet.co.za": (POLICY_LEAD_ONLY, "Zoutpansberger site; Batch 7 finding"),
}


def load_permissions(path: str = PERMISSIONS_FILE) -> dict:
    """Written permissions on record, keyed by host suffix.

    Shape: {"permissions": {"citizen.co.za": {"granted_by": ..., "date": ...,
    "scope": ..., "evidence": ...}}}. A permission only counts if it names who
    granted it and where the written evidence is kept.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    perms = data.get("permissions") or {}
    return {
        k.lower(): v
        for k, v in perms.items()
        if isinstance(v, dict) and v.get("granted_by") and v.get("evidence")
    }


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _suffix_match(host: str, table: dict) -> Optional[str]:
    for suffix in sorted(table, key=len, reverse=True):
        if host == suffix or host.endswith("." + suffix):
            return suffix
    return None


def outlet_policy(url: str, permissions: Optional[dict] = None) -> tuple[str, str]:
    """Return (policy, reason) for a URL, taking recorded permissions into account."""
    host = _host(url)
    perms = load_permissions() if permissions is None else permissions
    key = _suffix_match(host, OUTLET_POLICIES)
    if key is None:
        return POLICY_OPEN, "no outlet restriction on record; robots.txt still applies"
    policy, reason = OUTLET_POLICIES[key]
    if policy == POLICY_NEEDS_PERMISSION and _suffix_match(host, perms):
        return POLICY_OPEN, f"written permission on record for {key}"
    return policy, reason


# ---------------------------------------------------------------------------
# Google News links
# ---------------------------------------------------------------------------

GOOGLE_NEWS_HOSTS = ("news.google.com",)


def is_google_news(url: str) -> bool:
    host = _host(url)
    return any(host == h or host.endswith("." + h) for h in GOOGLE_NEWS_HOSTS)


_URL_IN_BYTES = re.compile(rb"https?://[\x21-\x7e]+")


def decode_google_news_id(url: str) -> Optional[str]:
    """Pull the article link out of an old-style Google News article id.

    Older ids (``CBMi...``) are base64 protobufs with the real URL inside.
    Newer ids carry an opaque token (``AU_yqL...``) and can't be decoded
    offline; for those this returns None.
    """
    path = urlparse(url).path
    m = re.search(r"/articles/([A-Za-z0-9_-]+)", path)
    if not m:
        return None
    token = m.group(1)
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except (ValueError, base64.binascii.Error):
        return None
    found = _URL_IN_BYTES.search(raw)
    if not found:
        return None
    link = found.group(0).decode("ascii", "ignore")
    # Protobuf field bytes can trail the URL; cut at the first control-ish char.
    link = re.split(r"[\x00-\x20\"'<>]", link)[0]
    if is_google_news(link) or not urlparse(link).hostname:
        return None
    return link


@dataclass
class Resolution:
    original_url: str
    url: Optional[str]
    status: str  # "direct" | "decoded" | "redirected" | "feed_date_only"
    reason: str = ""


# ---------------------------------------------------------------------------
# Polite fetching
# ---------------------------------------------------------------------------


@dataclass
class FetchResult:
    url: str
    status: str  # "ok" | "blocked_policy" | "blocked_robots" | "error" | "not_html" | "too_large"
    http_status: Optional[int] = None
    final_url: Optional[str] = None
    html: Optional[str] = None
    reason: str = ""


class PoliteFetcher:
    """Fetches pages one host at a time, spaced out, obeying robots.txt.

    ``session`` can be any object with a requests-style ``get``; tests pass a
    fake so nothing touches the network.
    """

    def __init__(
        self,
        session=None,
        min_interval_s: float = 10.0,
        timeout_s: float = 15.0,
        max_bytes: int = 2_000_000,
        user_agent: str = USER_AGENT,
        permissions: Optional[dict] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        if session is None:
            import requests  # local import keeps tests network-free

            session = requests.Session()
        self.session = session
        self.min_interval_s = min_interval_s
        self.timeout_s = timeout_s
        self.max_bytes = max_bytes
        self.user_agent = user_agent
        self.permissions = load_permissions() if permissions is None else permissions
        self._sleep = sleep
        self._clock = clock
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, Optional[robotparser.RobotFileParser]] = {}
        self._lock = threading.Lock()

    # -- spacing -----------------------------------------------------------
    def _wait_turn(self, host: str) -> None:
        with self._lock:
            last = self._last_hit.get(host)
            now = self._clock()
            if last is not None and now - last < self.min_interval_s:
                self._sleep(self.min_interval_s - (now - last))
            self._last_hit[host] = self._clock()

    def _get(self, url: str, allow_redirects: bool = True):
        host = _host(url)
        self._wait_turn(host)
        return self.session.get(
            url,
            headers={"User-Agent": self.user_agent, "Accept": "text/html,application/xhtml+xml"},
            timeout=self.timeout_s,
            allow_redirects=allow_redirects,
            stream=False,
        )

    # -- robots ------------------------------------------------------------
    def robots_allows(self, url: str) -> tuple[bool, str]:
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        if base not in self._robots:
            rp = robotparser.RobotFileParser()
            try:
                resp = self._get(base + "/robots.txt")
                code = getattr(resp, "status_code", 0)
                if code in (401, 403):
                    rp = None  # robots itself refused: treat as disallow all
                elif code >= 400:
                    rp.parse([])  # no robots file: allowed by convention
                else:
                    rp.parse((getattr(resp, "text", "") or "").splitlines())
            except Exception as exc:  # network trouble: don't fetch
                self._robots[base] = None
                return False, f"robots.txt unreachable ({type(exc).__name__})"
            self._robots[base] = rp
        rp = self._robots[base]
        if rp is None:
            return False, "robots.txt refused or unreachable"
        ok = rp.can_fetch(self.user_agent, url)
        return ok, "robots.txt allows" if ok else "robots.txt disallows this path"

    # -- public ------------------------------------------------------------
    def resolve(self, url: str) -> Resolution:
        """Turn a feed link into the article link, or flag it feed-date-only."""
        if not url:
            return Resolution(url, None, "feed_date_only", "no link on the record")
        if not is_google_news(url):
            return Resolution(url, url, "direct")
        decoded = decode_google_news_id(url)
        if decoded:
            return Resolution(url, decoded, "decoded")
        try:
            resp = self._get(url, allow_redirects=True)
            final = getattr(resp, "url", None) or url
        except Exception as exc:
            return Resolution(url, None, "feed_date_only", f"redirect failed ({type(exc).__name__})")
        if final and not is_google_news(final):
            return Resolution(url, final, "redirected")
        return Resolution(
            url,
            None,
            "feed_date_only",
            "Google News link is an opaque redirect that only resolves in a browser; not guessed",
        )

    def fetch(self, url: str) -> FetchResult:
        policy, reason = outlet_policy(url, self.permissions)
        if policy != POLICY_OPEN:
            return FetchResult(url, "blocked_policy", reason=f"{policy}: {reason}")
        ok, why = self.robots_allows(url)
        if not ok:
            return FetchResult(url, "blocked_robots", reason=why)
        try:
            resp = self._get(url)
        except Exception as exc:
            return FetchResult(url, "error", reason=f"{type(exc).__name__}: {exc}"[:200])
        code = getattr(resp, "status_code", None)
        final = getattr(resp, "url", None) or url
        # A redirect onto a restricted outlet is still restricted.
        fpolicy, freason = outlet_policy(final, self.permissions)
        if fpolicy != POLICY_OPEN:
            return FetchResult(url, "blocked_policy", code, final, reason=f"{fpolicy}: {freason}")
        if code is None or code >= 400:
            return FetchResult(url, "error", code, final, reason=f"HTTP {code}")
        ctype = (getattr(resp, "headers", {}) or {}).get("Content-Type", "text/html")
        if "html" not in ctype.lower():
            return FetchResult(url, "not_html", code, final, reason=ctype)
        body = getattr(resp, "content", b"") or b""
        if len(body) > self.max_bytes:
            return FetchResult(url, "too_large", code, final, reason=f"{len(body)} bytes")
        text = getattr(resp, "text", None)
        if text is None:
            text = body.decode("utf-8", "replace")
        return FetchResult(url, "ok", code, final, html=text)


# ---------------------------------------------------------------------------
# Parsing: published date and body text
# ---------------------------------------------------------------------------

_NOISE_TAGS = ("script", "style", "noscript", "nav", "header", "footer", "aside", "form",
               "iframe", "svg", "button", "figure", "figcaption")
_NOISE_ATTR = re.compile(
    r"comment|disqus|wpdiscuz|respond|reply|related|recommend|more-stor|read-more|also-read|"
    r"you-may|popular|trending|most-read|share|social|newsletter|subscribe|signup|advert|"
    r"\bad-|\bads\b|promo|sponsor|sidebar|widget|breadcrumb|tags?\b|author-bio|byline-box|"
    r"cookie|paywall|outbrain|taboola|jp-relatedposts|menu|navbar|footer|header|"
    r"byline|dateline|entry-meta|post-meta|posted-on",
    re.I,
)
_BODY_LABEL = re.compile(
    r"entry-content|article-body|article-content|post-content|story-body|articleBody|td-post-content",
    re.I,
)
_BODY_SELECTORS = (
    "[itemprop=articleBody]",
    ".entry-content",
    ".article-body",
    ".article-content",
    ".post-content",
    ".story-body",
    ".td-post-content",
    "article",
    "main",
)
_META_DATE_KEYS = (
    ("property", "article:published_time"),
    ("name", "article:published_time"),
    ("itemprop", "datePublished"),
    ("name", "parsely-pub-date"),
    ("name", "publish-date"),
    ("name", "pubdate"),
    ("name", "date"),
    ("name", "DC.date.issued"),
    ("property", "og:published_time"),
)


def _parse_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    v = value.strip()
    v = re.sub(r"Z$", "+00:00", v)
    for candidate in (v, v[:19], v[:10]):
        try:
            dt = datetime.fromisoformat(candidate)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _jsonld_dates(soup) -> list[str]:
    out = []
    for tag in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        try:
            data = json.loads(tag.string or tag.get_text() or "null")
        except ValueError:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                typ = node.get("@type")
                types = typ if isinstance(typ, list) else [typ]
                if any(t in ("NewsArticle", "Article", "ReportageNewsArticle", "BlogPosting")
                       for t in types) and node.get("datePublished"):
                    out.append(str(node["datePublished"]))
                stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
    return out


def extract_published(soup) -> tuple[Optional[datetime], str]:
    """The article's own published date, and where it was read from."""
    for value in _jsonld_dates(soup):
        dt = _parse_iso(value)
        if dt:
            return dt, "json-ld datePublished"
    for attr, key in _META_DATE_KEYS:
        tag = soup.find("meta", attrs={attr: key})
        if tag and tag.get("content"):
            dt = _parse_iso(tag["content"])
            if dt:
                return dt, f"meta {key}"
    scope = soup.find("article") or soup
    t = scope.find("time", attrs={"datetime": True})
    if t:
        dt = _parse_iso(t["datetime"])
        if dt:
            return dt, "time element"
    return None, "not found"


def _strip_noise(soup) -> None:
    for name in _NOISE_TAGS:
        for tag in soup.find_all(name):
            tag.decompose()
    for tag in list(soup.find_all(True)):
        if getattr(tag, "decomposed", False):
            continue
        if tag.name in ("html", "body", "article", "main"):
            continue
        attrs = getattr(tag, "attrs", None)
        if not attrs:
            continue
        label = " ".join(
            [tag.get("id") or ""]
            + list(tag.get("class") or [])
            + [tag.get("role") or "", tag.get("aria-label") or ""]
        )
        if label.strip() and _NOISE_ATTR.search(label):
            # Don't throw away the article body because a second class looks like noise.
            if _BODY_LABEL.search(label) or tag.get("itemprop") == "articleBody":
                continue
            tag.decompose()


def extract_body(soup) -> str:
    """Body text with comments, navigation and related-story blocks removed."""
    _strip_noise(soup)
    container = None
    for sel in _BODY_SELECTORS:
        found = soup.select_one(sel)
        if found and len(found.get_text(" ", strip=True)) > 200:
            container = found
            break
    container = container or soup.body or soup
    paras = []
    for p in container.find_all(["p", "h2", "h3", "li"]):
        txt = re.sub(r"\s+", " ", p.get_text(" ", strip=True)).strip()
        if len(txt) < 25 and p.name == "p":
            continue
        if p.name == "li" and len(txt) < 60:
            continue
        paras.append(txt)
    if not paras:
        txt = re.sub(r"\s+", " ", container.get_text(" ", strip=True))
        return txt.strip()
    # Drop exact repeats (pull quotes, duplicated standfirsts).
    seen, out = set(), []
    for t in paras:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return "\n\n".join(out)


def extract_title(soup) -> str:
    tag = soup.find("meta", attrs={"property": "og:title"})
    if tag and tag.get("content"):
        return tag["content"].strip()
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(" ", strip=True)
    return (soup.title.get_text(strip=True) if soup.title else "")


# ---------------------------------------------------------------------------
# Incident dates from the text
# ---------------------------------------------------------------------------

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
MONTHS["sept"] = 9
WEEKDAYS = {d: i for i, d in enumerate(
    ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"])}

_MON = r"(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec)\.?"
_WD = r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
_YEAR = r"(?:,?\s+(\d{4}))?"

# "(on) (Friday,) 16 October (2026)"
_RE_DAY_MONTH = re.compile(
    rf"\b(on\s+|since\s+|from\s+|until\s+|to\s+|by\s+)?(?:{_WD},?\s+)?(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MON}{_YEAR}\b",
    re.I,
)
# "(on) (Friday,) October 16(th) (, 2026)"
_RE_MONTH_DAY = re.compile(
    rf"\b(on\s+|since\s+|from\s+|until\s+|to\s+|by\s+)?(?:{_WD},?\s+)?{_MON}\s+(\d{{1,2}})(?:st|nd|rd|th)?{_YEAR}\b",
    re.I,
)
# "on Friday (night|morning|...)", "last Friday"
_RE_WEEKDAY_ONLY = re.compile(
    rf"\b(on|last|this past|early on|late on)\s+{_WD}\b(?!,?\s+\d)(?!,?\s+{_MON})",
    re.I,
)
_RE_RELATIVE = re.compile(r"\b(yesterday|last night|earlier today|this morning)\b", re.I)
# "in September (2026)", "between May and July"
_RE_MONTH_ONLY = re.compile(rf"\b(in|during|since|between|from|early|late|mid-?)\s+{_MON}(?:\s+(\d{{4}}))?\b(?!\s+\d)", re.I)

_FUTURE_CUE = re.compile(
    r"(postponed|remanded|adjourned|deferred|back in court|return to court|will (?:appear|return|be)|"
    r"expected to|scheduled|set down|until|due to appear|next appearance|trial date)",
    re.I,
)
_COURT_CUE = re.compile(
    r"(court|magistrate|bail|remanded|postponed|trial|accused|appear(?:ed|ance)?|sentenc|plea|charge)",
    re.I,
)
_EVENT_CUE = re.compile(
    r"(shot|killed|stabbed|murder|died|found|arrested|seized|confiscat|raid|attack|robbed|"
    r"stole|stolen|intercept|incident|happened|occurred|took place|operation|inspection|"
    r"disappear|went missing|crash|burn|explo)",
    re.I,
)


@dataclass
class DateMention:
    phrase: str
    date: Optional[str]      # ISO day, or YYYY-MM for month precision
    precision: str           # "day" | "month"
    kind: str                # "past" | "future" | "unknown"
    context: str             # a short window of the sentence, for review
    cue: str = ""            # "event" | "court" | ""
    weekday_ok: Optional[bool] = None


def _sentence_window(text: str, start: int, end: int, width: int = 90) -> str:
    a = max(0, start - width)
    b = min(len(text), end + width)
    s = text[a:b]
    return re.sub(r"\s+", " ", s).strip()


def _resolve_year(month: int, day: int, year: Optional[int], ref: date, future: bool) -> Optional[date]:
    if year:
        try:
            return date(year, month, day)
        except ValueError:
            return None
    for y in ((ref.year, ref.year + 1) if future else (ref.year, ref.year - 1)):
        try:
            d = date(y, month, day)
        except ValueError:
            continue
        if future and d >= ref:
            return d
        if not future and d <= ref + timedelta(days=1):
            return d
    return None


def extract_date_mentions(text: str, published: date) -> list[DateMention]:
    """Every date the text gives, resolved against the article's published date."""
    out: list[DateMention] = []
    taken: list[tuple[int, int]] = []

    def overlaps(a: int, b: int) -> bool:
        return any(a < y and b > x for x, y in taken)

    def classify(start: int, end: int, lead: str) -> tuple[bool, str, str]:
        before = text[max(0, start - 70):start]
        window = _sentence_window(text, start, end, 70)
        future = bool(_FUTURE_CUE.search(before)) or (lead or "").strip().lower() in ("until", "to")
        if (lead or "").strip().lower() == "to" and not _FUTURE_CUE.search(before):
            future = False
        cue = "event" if _EVENT_CUE.search(window) else ("court" if _COURT_CUE.search(window) else "")
        return future, cue, window

    for rx, order in ((_RE_DAY_MONTH, "dm"), (_RE_MONTH_DAY, "md")):
        for m in rx.finditer(text):
            if overlaps(m.start(), m.end()):
                continue
            lead = m.group(1) or ""
            wd = m.group(2)
            if order == "dm":
                day, mon, yr = int(m.group(3)), MONTHS[m.group(4).lower().rstrip(".")], m.group(5)
            else:
                mon, day, yr = MONTHS[m.group(3).lower().rstrip(".")], int(m.group(4)), m.group(5)
            if not 1 <= day <= 31:
                continue
            future, cue, window = classify(m.start(), m.end(), lead)
            d = _resolve_year(mon, day, int(yr) if yr else None, published, future)
            weekday_ok = None
            if d and wd:
                weekday_ok = d.weekday() == WEEKDAYS[wd.lower()]
                if not weekday_ok and not yr:
                    # try the other plausible year before giving up
                    alt = _resolve_year(mon, day, (d.year - 1) if not future else (d.year + 1), published, future)
                    if alt and alt.weekday() == WEEKDAYS[wd.lower()]:
                        d, weekday_ok = alt, True
            kind = "unknown" if d is None else ("future" if d > published else "past")
            out.append(DateMention(m.group(0).strip(), d.isoformat() if d else None, "day", kind,
                                   window, cue, weekday_ok))
            taken.append((m.start(), m.end()))

    for m in _RE_WEEKDAY_ONLY.finditer(text):
        if overlaps(m.start(), m.end()):
            continue
        wd = WEEKDAYS[m.group(2).lower()]
        future, cue, window = classify(m.start(), m.end(), "")
        if future:
            delta = (wd - published.weekday()) % 7 or 7
            d = published + timedelta(days=delta)
        else:
            delta = (published.weekday() - wd) % 7
            if m.group(1).lower() == "last" and delta == 0:
                delta = 7
            d = published - timedelta(days=delta)
        out.append(DateMention(m.group(0).strip(), d.isoformat(), "day",
                               "future" if d > published else "past", window, cue, True))
        taken.append((m.start(), m.end()))

    for m in _RE_RELATIVE.finditer(text):
        if overlaps(m.start(), m.end()):
            continue
        word = m.group(1).lower()
        d = published - timedelta(days=1) if word in ("yesterday", "last night") else published
        _, cue, window = classify(m.start(), m.end(), "")
        out.append(DateMention(m.group(0), d.isoformat(), "day", "past", window, cue))
        taken.append((m.start(), m.end()))

    for m in _RE_MONTH_ONLY.finditer(text):
        if overlaps(m.start(), m.end()):
            continue
        mon = MONTHS[m.group(2).lower().rstrip(".")]
        yr = int(m.group(3)) if m.group(3) else None
        future, cue, window = classify(m.start(), m.end(), "")
        if yr is None:
            yr = published.year if (mon <= published.month or future) else published.year - 1
            if future and mon < published.month:
                yr = published.year + 1
        kind = "future" if (yr, mon) > (published.year, published.month) else "past"
        out.append(DateMention(m.group(0).strip(), f"{yr:04d}-{mon:02d}", "month", kind, window, cue))
        taken.append((m.start(), m.end()))

    order = {id(x): i for i, x in enumerate(out)}
    out.sort(key=lambda x: text.find(x.phrase) if x.phrase in text else order[id(x)])
    return out


def pick_incident_date(mentions: Iterable[DateMention], published: date,
                       max_age_days: int = 400) -> tuple[Optional[str], str]:
    """Best guess at when the incident happened, with the phrase it came from.

    Prefers a day-precision past date tied to an event word (shot, seized,
    arrested...). Court-step stories often name an older date; those come back
    too, and step 2 decides what a court step means. Returns (None, reason) when
    the text gives no usable date.
    """
    floor = published - timedelta(days=max_age_days)
    past = [m for m in mentions if m.kind == "past" and m.precision == "day" and m.date
            and floor <= date.fromisoformat(m.date) <= published and m.weekday_ok is not False]
    for pool, why in (
        ([m for m in past if m.cue == "event"], "first dated event in the text"),
        (past, "first past date in the text"),
    ):
        if pool:
            first = pool[0]
            return first.date, f"{why}: \"{first.phrase}\""
    months = [m for m in mentions if m.kind == "past" and m.precision == "month" and m.cue == "event"]
    if months:
        return months[0].date, f"month only: \"{months[0].phrase}\""
    return None, "no dated incident in the text"


# ---------------------------------------------------------------------------
# Putting it together
# ---------------------------------------------------------------------------


@dataclass
class ArticleReading:
    signal_url: str
    status: str                      # "read" | "feed_date_only" | "blocked" | "not_shortlisted" | "error"
    reason: str = ""
    resolution: str = ""
    article_url: Optional[str] = None
    feed_date: Optional[str] = None
    published: Optional[str] = None
    published_source: str = ""
    title: str = ""
    body_text: str = ""
    incident_date: Optional[str] = None
    incident_basis: str = ""
    date_mentions: list = field(default_factory=list)
    fetched_at: Optional[str] = None
    from_cache: bool = False

    @property
    def effective_date(self) -> Optional[str]:
        """Incident date, else the article's own date, else the feed date."""
        return self.incident_date or (self.published[:10] if self.published else None) or self.feed_date

    def to_dict(self) -> dict:
        d = asdict(self)
        d["effective_date"] = self.effective_date
        return d


def parse_article(html: str, url: str = "", feed_date: Optional[str] = None) -> ArticleReading:
    """Read a saved page. Pure function: no network."""
    if BeautifulSoup is None:  # pragma: no cover
        raise RuntimeError("beautifulsoup4 is required")
    soup = BeautifulSoup(html, "lxml")
    published, source = extract_published(soup)
    title = extract_title(soup)
    body = extract_body(soup)
    ref = published.date() if published else (date.fromisoformat(feed_date[:10]) if feed_date else None)
    mentions: list[DateMention] = extract_date_mentions(body, ref) if ref else []
    incident, basis = pick_incident_date(mentions, ref) if ref else (None, "no reference date to resolve against")
    return ArticleReading(
        signal_url=url,
        status="read",
        article_url=url,
        feed_date=feed_date,
        published=published.isoformat() if published else None,
        published_source=source,
        title=title,
        body_text=body,
        incident_date=incident,
        incident_basis=basis,
        date_mentions=[asdict(m) for m in mentions],
    )


class ArticleCache:
    """Local, expiring cache of readings. Never committed (see .gitignore)."""

    def __init__(self, directory: str = DEFAULT_CACHE_DIR, ttl_days: int = 14):
        self.dir = directory
        self.ttl = timedelta(days=ttl_days)

    def _path(self, url: str) -> str:
        return os.path.join(self.dir, hashlib.sha1(url.encode("utf-8")).hexdigest() + ".json")

    def get(self, url: str) -> Optional[dict]:
        try:
            with open(self._path(url), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return None
        fetched = _parse_iso(data.get("fetched_at") or "")
        if not fetched or datetime.now(timezone.utc) - fetched > self.ttl:
            return None
        return data

    def put(self, url: str, reading: ArticleReading) -> None:
        os.makedirs(self.dir, exist_ok=True)
        tmp = self._path(url) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(reading.to_dict(), fh, ensure_ascii=False)
        os.replace(tmp, self._path(url))

    def purge_expired(self) -> int:
        if not os.path.isdir(self.dir):
            return 0
        n = 0
        for name in os.listdir(self.dir):
            p = os.path.join(self.dir, name)
            try:
                with open(p, encoding="utf-8") as fh:
                    fetched = _parse_iso(json.load(fh).get("fetched_at") or "")
            except (OSError, ValueError):
                fetched = None
            if not fetched or datetime.now(timezone.utc) - fetched > self.ttl:
                os.remove(p)
                n += 1
        return n


def _reading_from_dict(d: dict) -> ArticleReading:
    d = dict(d)
    d.pop("effective_date", None)
    known = {f for f in ArticleReading.__dataclass_fields__}
    return ArticleReading(**{k: v for k, v in d.items() if k in known})


class ArticleReader:
    """Reads the article behind one shortlisted signal."""

    def __init__(self, fetcher: Optional[PoliteFetcher] = None, cache: Optional[ArticleCache] = None):
        self.fetcher = fetcher or PoliteFetcher()
        self.cache = cache if cache is not None else ArticleCache()

    def read_signal(self, url: str, feed_date: Optional[str] = None, shortlisted: bool = False) -> ArticleReading:
        if not shortlisted:
            return ArticleReading(url, "not_shortlisted", "only shortlisted signals are read",
                                  feed_date=feed_date)
        res = self.fetcher.resolve(url)
        if res.status == "feed_date_only" or not res.url:
            return ArticleReading(url, "feed_date_only", res.reason, res.status, feed_date=feed_date)
        cached = self.cache.get(res.url) if self.cache else None
        if cached:
            reading = _reading_from_dict(cached)
            reading.signal_url, reading.from_cache = url, True
            reading.feed_date = reading.feed_date or feed_date
            return reading
        fr = self.fetcher.fetch(res.url)
        if fr.status != "ok":
            status = "blocked" if fr.status.startswith("blocked") else "error"
            # Blocked or failed: the signal keeps its feed date, flagged, never guessed.
            return ArticleReading(url, status, fr.reason, res.status, article_url=res.url, feed_date=feed_date)
        reading = parse_article(fr.html or "", fr.final_url or res.url, feed_date)
        reading.signal_url = url
        reading.resolution = res.status
        reading.fetched_at = datetime.now(timezone.utc).isoformat()
        if self.cache:
            self.cache.put(res.url, reading)
        return reading


def read_signal(url: str, feed_date: Optional[str] = None, shortlisted: bool = False,
                reader: Optional[ArticleReader] = None) -> ArticleReading:
    """Convenience wrapper. Collectors will call this in step 3."""
    return (reader or ArticleReader()).read_signal(url, feed_date, shortlisted)
