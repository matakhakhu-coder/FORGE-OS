"""
Batch 6 step 3: the article reader and the court-step check, wired into the
collectors (Civic Intelligence and the Google News monitor).

Two parts, with different switches:

* The court-step check (court_step.py) runs on every signal both collectors
  build. It needs no network: it reads the headline and summary, plus the
  article text when there is one. It only adds metadata (court_step,
  step_kind, older_conduct, conduct_date, step_date, step_basis, new_event).
  It never changes a stream, a place or a timestamp. FORGE_COURT_STEP=0
  turns it off.

* The article reader (article_reader.py) fetches pages, so it is OFF unless
  a run asks for it: ``--read-articles`` on either collector, or
  FORGE_ARTICLE_READER=1. When on, it reads only shortlisted signals (those
  that passed the place check: a South African place estimate and no place
  review), and it keeps every outlet rule from article_reader: Caxton and the
  Newcastillian need a written permission recorded in
  data/article_reader_permissions.json, and the Limpopo Mirror and
  Zoutpansberger are headline, link and date only. A Google News link that
  can't be followed keeps its feed date and is flagged, never guessed.

When an article is read, the collector rebuilds the signal with the article
text so the Priority Intelligence rules read it (comments already removed),
and the signal's metadata gets the article's own date and an incident date
when the text gives one. The signal's timestamp stays the feed date; the
incident date is metadata. A read article whose incident date falls before a
fixed run start (--from) is dropped as outside the window, unless it is a
court or arrest step in an older case (a court step on an old case is still
news).

share_story_step() passes the court-step flag across every record of one
story (Batch 5's story_key), in the database, so the headline-only copies of
a story get the flag that only one record could show.
"""
from __future__ import annotations

import json
import os
from datetime import date
from typing import Optional

try:
    from forage.collectors import court_step as _cs
    from forage.collectors import article_reader as _ar
except ImportError:  # pragma: no cover - run from the collectors directory
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from forage.collectors import court_step as _cs
    from forage.collectors import article_reader as _ar

ENV_READER = "FORGE_ARTICLE_READER"
ENV_COURT_STEP = "FORGE_COURT_STEP"

# Set by a collector's --read-articles flag; None means "use the env var".
READ_ARTICLES: Optional[bool] = None

# The reader used when reading is on. Tests and the bench swap in an offline one.
_READER = None

STEP_KEYS = ("court_step", "step_kind", "older_conduct", "conduct_date", "step_date",
             "step_basis", "new_event")


def reader_enabled() -> bool:
    if READ_ARTICLES is not None:
        return bool(READ_ARTICLES)
    return os.environ.get(ENV_READER, "0") == "1"


def court_step_enabled() -> bool:
    return os.environ.get(ENV_COURT_STEP, "1") != "0"


def set_reader(reader) -> None:
    """Swap the reader (an object with read_signal(url, feed_date, shortlisted))."""
    global _READER
    _READER = reader


def get_reader():
    global _READER
    if _READER is None:
        _READER = _ar.ArticleReader()
    return _READER


def shortlisted(stream: str, meta: Optional[dict]) -> bool:
    """A signal is shortlisted for reading when it passed the place check:
    a place estimate in a South African province and no place review."""
    meta = meta or {}
    if meta.get("place_review") or meta.get("foreign") or meta.get("pi_foreign"):
        return False
    lc = meta.get("location_candidate") or {}
    return bool(lc.get("place") and lc.get("province"))


def _as_dict(reading) -> dict:
    if reading is None:
        return {}
    if isinstance(reading, dict):
        return reading
    return reading.to_dict()


def read(link: str, feed_date: Optional[str], stream: str, meta: dict):
    """Read the article behind a signal when reading is on and the signal is
    shortlisted. Returns the reading as a dict, or None when nothing was
    attempted. A failure in the reader never stops a signal being built."""
    if not reader_enabled() or not link or not shortlisted(stream, meta):
        return None
    try:
        r = get_reader().read_signal(link, feed_date, shortlisted=True)
    except Exception as exc:  # never block a save on the reader
        return {"status": "error", "reason": f"{type(exc).__name__}: {exc}"[:200], "signal_url": link}
    return _as_dict(r)


def article_metadata(reading) -> dict:
    """What a signal's metadata says about its article."""
    r = _as_dict(reading)
    if not r:
        return {}
    out = {"article_status": r.get("status")}
    if r.get("status") == "read":
        if r.get("article_url"):
            out["article_url"] = r["article_url"]
        if r.get("published"):
            out["article_published"] = r["published"]
        if r.get("incident_date"):
            out["incident_date"] = r["incident_date"]
            out["incident_basis"] = (r.get("incident_basis") or "")[:200]
    else:
        out["article_reason"] = (r.get("reason") or "")[:200]
        out["date_basis"] = "feed date (article not read)"
    return out


def step_result(title: str, summary: str, reading=None, feed_date: Optional[str] = None,
                fixture: Optional[dict] = None, window_start=None) -> dict:
    """court_step.assess fed from the record and, when read, its article."""
    return _cs.assess_reading(title or "", summary or "", _as_dict(reading) or None,
                              feed_date=(feed_date or "")[:10] or None, fixture=fixture,
                              window_start=window_start)


def step_metadata(result: dict) -> dict:
    """The step keys a signal saves. A record with a court, arrest or
    investigation word that is not a step keeps only step_kind, so the
    story-level sharing can find it later."""
    md = _cs.metadata(result)
    if not md and result.get("step_kind"):
        md = {"step_kind": result["step_kind"]}
    return md


def annotate(meta: dict, title: str, summary: str, reading=None, feed_date: Optional[str] = None,
             fixture: Optional[dict] = None, window_start=None) -> dict:
    """Add article and court-step metadata to a signal's metadata (in place,
    and returned). Never raises."""
    try:
        meta.update(article_metadata(reading))
        if court_step_enabled():
            meta.update(step_metadata(step_result(title, summary, reading, feed_date, fixture, window_start)))
    except Exception as exc:  # never block a save on the step check
        meta["court_step_error"] = f"{type(exc).__name__}: {exc}"[:200]
    return meta


def before_window(meta: dict, window) -> bool:
    """A read article whose incident date is before a fixed run start is
    outside the window, unless the record is a step in an older case."""
    try:
        if not window or getattr(window, "start", None) is None:
            return False
        inc = meta.get("incident_date")
        if not inc or meta.get("court_step"):
            return False
        return date.fromisoformat(inc[:10]) < window.start
    except Exception:
        return False


def _result_from_meta(m: dict) -> dict:
    r = {k: m.get(k) for k in STEP_KEYS}
    r["court_step"] = bool(m.get("court_step"))
    r["older_conduct"] = bool(m.get("older_conduct"))
    r["step_basis"] = m.get("step_basis") or ""
    return r


def _apply(m: dict, r: dict) -> dict:
    m = dict(m)
    for k in STEP_KEYS:
        if r.get(k) is not None:
            m[k] = r[k]
    m["court_step_shared"] = True
    return m


def share_story_step(conn, signal_id: str, timestamp: str, meta: dict, lookback_days: int = 21) -> dict:
    """Pass the court-step flag across one story's records.

    Reads the saved records with the same story_key (21 days either side),
    runs court_step.story_flags over them plus this record, writes the flag
    to saved records that gain it and returns this record's metadata. Only
    arrest and investigation records gain it (court records already have
    it; records with no step word are left alone). Never raises.
    """
    if os.environ.get("FORGE_STORY_MERGE", "1") == "0" or not court_step_enabled():
        return meta
    key = meta.get("story_key")
    if not key:
        return meta
    try:
        rows = conn.execute(
            "SELECT signal_id, metadata_json FROM signals WHERE source_type='live' "
            "AND timestamp >= date(?, ?) AND timestamp <= date(?, ?) AND metadata_json LIKE ?",
            ((timestamp or "")[:10], f"-{lookback_days} days", (timestamp or "")[:10], f"+{lookback_days} days",
             f"%{key}%")).fetchall()
        members = []
        for sid, mj in rows:
            try:
                m = json.loads(mj or "{}")
            except Exception:
                continue
            if m.get("story_key") == key and sid != signal_id:
                members.append((sid, m))
        if not members:
            return meta
        results = [_result_from_meta(m) for _, m in members] + [_result_from_meta(meta)]
        shared = _cs.story_flags(results)
        for (sid, m), before, after in zip(members, results, shared):
            if after["court_step"] and not before["court_step"]:
                conn.execute("UPDATE signals SET metadata_json=? WHERE signal_id=?",
                             (json.dumps(_apply(m, after), ensure_ascii=False), sid))
        if shared[-1]["court_step"] and not results[-1]["court_step"]:
            meta = _apply(meta, shared[-1])
        return meta
    except Exception as exc:  # never block a save
        out = dict(meta)
        out["court_step_error"] = f"story share: {type(exc).__name__}: {exc}"[:200]
        return out


# ── offline reader for the bench and tests ──────────────────────────────────

class FixtureReader:
    """Answers read_signal from data/bench/article_fixtures.json, offline.

    A link the fixtures hold gets the outcome the step 1 reader actually had
    (read, blocked or feed_date_only). A 'read' link is parsed from its saved
    page in data/bench/article_fixtures_local/ when that page is on this
    machine; without it the reading keeps its dates but has no text. A link
    the fixtures don't hold is answered as the live reader would decide
    before fetching: blocked by outlet policy, feed_date_only for an opaque
    Google News link, otherwise 'not_in_fixture' (not read). Nothing is
    fetched.
    """

    def __init__(self, fixtures_path: Optional[str] = None, local_dir: Optional[str] = None,
                 permissions: Optional[dict] = None):
        fixtures_path = fixtures_path or os.path.join(_ar.REPO_ROOT, "data", "bench", "article_fixtures.json")
        self.local_dir = local_dir or os.path.join(_ar.REPO_ROOT, "data", "bench", "article_fixtures_local")
        self.permissions = permissions if permissions is not None else _ar.load_permissions()
        self.links: dict = {}
        self.calls: list = []
        try:
            with open(fixtures_path, encoding="utf-8") as f:
                data = json.load(f)
            for case in data.get("cases", {}).values():
                for ln in case.get("links", []):
                    self.links[ln["link"]] = ln
        except FileNotFoundError:
            pass

    def _local_html(self, url: str) -> Optional[str]:
        import hashlib
        p = os.path.join(self.local_dir, hashlib.sha1(url.encode()).hexdigest()[:16] + ".html")
        if os.path.exists(p):
            with open(p, encoding="utf-8", errors="replace") as f:
                return f.read()
        return None

    def read_signal(self, url: str, feed_date: Optional[str] = None, shortlisted: bool = False) -> dict:
        self.calls.append(url)
        if not shortlisted:
            return {"signal_url": url, "status": "not_shortlisted", "feed_date": feed_date}
        ln = self.links.get(url)
        if ln is None:
            policy, why = _ar.outlet_policy(url, self.permissions)
            if policy != _ar.POLICY_OPEN:
                return {"signal_url": url, "status": "blocked", "reason": f"{policy}: {why}", "feed_date": feed_date}
            if _ar.is_google_news(url) and not _ar.decode_google_news_id(url):
                return {"signal_url": url, "status": "feed_date_only", "feed_date": feed_date,
                        "reason": "Google News link is an opaque redirect; not guessed"}
            return {"signal_url": url, "status": "not_in_fixture", "feed_date": feed_date,
                    "reason": "bench is offline and holds no saved page for this link"}
        if ln.get("status") == "read":
            target = ln.get("article_url") or url
            # Re-check the outlet policy: a permission can only widen reading.
            policy, why = _ar.outlet_policy(target, self.permissions)
            if policy != _ar.POLICY_OPEN:
                return {"signal_url": url, "status": "blocked", "reason": f"{policy}: {why}", "feed_date": feed_date}
            html = self._local_html(target)
            if html:
                r = _ar.parse_article(html, target, feed_date).to_dict()
                r["signal_url"] = url
                return r
            return {**ln, "signal_url": url, "body_text": "", "feed_date": feed_date or ln.get("feed_date")}
        return {**ln, "signal_url": url, "feed_date": feed_date or ln.get("feed_date")}
