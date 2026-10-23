"""
Court and arrest steps on older cases (Batch 6 step 2, G8).

The sweeps found stories that report a step in a case, not a new event, and
the collectors filed them as if the thing had just happened:

  * Polokwane airport officers: arrested on 19 October 2026 for conduct on
    17 April 2025.
  * Oosthuizen: a murder case postponed to March 2027; the killing was in
    December 2025.
  * Mlambo: a trial in the Madadeni High Court on a 2023 case.
  * 88 firearms: arrested about 27 September for conduct between May and July.

assess() reads one record and says whether it is such a step. It never calls
a step a new event: the result's ``new_event`` is False whenever
``court_step`` is True, and the step's own date is kept apart from the
conduct date.

What counts:
  * A court step (appearance, bail, postponement, trial, judgment, sentence,
    charges added) is always a step, whatever the dates. A first appearance a
    day after a killing is still a step in that killing's case.
  * An arrest or an investigation is a step only when something shows the
    conduct is older: a conduct date from the article or a checked fixture, a
    year or month in the text before the step, "last year", "after a
    five-month investigation", or a follow-up arrest in an existing case.
    An arrest with no such sign is the event itself (the quad bike arrest
    while the bike was being stripped).

Evidence is read in this order: headline, summary, then the article text
when the reader has it (comments are already removed by the reader), then
checked fixture dates. Nothing here changes a stream, a place or the
Carrim court-place rule (still on hold); it only adds metadata.

Metadata keys (metadata()): court_step, step_kind, older_conduct,
conduct_date, step_date, step_basis, new_event.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta
from typing import Iterable, Optional

# A conduct date this many days or more before the step makes it an older case.
OLDER_DAYS = 30

_COURT_NOISE = re.compile(r"\b(food|tennis|basketball|netball|squash|volleyball|badminton) courts?\b|\bcourtyard\b|\bcourt(ing|ship)\b", re.I)

_COURT = re.compile(
    r"\b(court|courts|magistrates?'?|magistrate'?s|judge|judges|tribunal|trial|bail|postpone[sd]?|postponement"
    r"|remand(ed)?|in the dock|dock\b|appear(s|ed|ance)? (in|before|at) |court appearance"
    r"|convicted|conviction|sentenc(e|ed|ing)|acquit(ted|tal)|plead(s|ed)?|pleaded|plea\b|guilty"
    r"|verdict|judgment|judgement|indict(ed|ment)|charge sheet|(additional|fresh|more|further|new) charges"
    r"|charges? (added|withdrawn|dropped)|back in court|case (resumes|continues|adjourned))", re.I)

_ARREST = re.compile(
    r"\b(arrest(s|ed|ing)?|nabbed|apprehended|handed (himself|herself|themselves) over|taken into custody"
    r"|in custody|bust(ed)?|cuffed|detained)\b", re.I)

_INVESTIGATION = re.compile(
    r"\b(investigat\w*|probe[sd]?|probing|inquiry|inquest)\b", re.I)

# Signs, in words, that the conduct is older than the step.
_N = r"(\d+|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|several|a few|many)"
_LONG_INVESTIGATION = re.compile(
    rf"\b{_N}[- ]months?[- ](long )?(investigation|probe|inquiry|operation)\b"
    rf"|\b(months|years)[- ]long (investigation|probe|inquiry)\b"
    rf"|\b(investigation|probe) (that|which) (lasted|took) {_N} (months|years)\b"
    rf"|\b{_N} years? (after|later|ago)\b|\bcold case\b|\bdating back\b", re.I)
_LAST_YEAR = re.compile(r"\blast year\b", re.I)
_FOLLOW_UP = re.compile(
    r"\b(more|further|another|second|third|additional|latest|remaining)\b[^.]{0,30}\b(arrests?|arrested|suspects?)\b"
    r"|\bstill being sought\b|\bin (the|a) case (of|against|involving)\b|\bin connection with the\b"
    r"|\b(the|a) case of\b", re.I)
# Bare years with a lead-in that ties them to the conduct ("in 2023",
# "since 2023", "the 2023 murder"). "of 1998" (an Act) is not one.
_YEAR_LEAD = re.compile(
    r"\b(in|since|during|from|between|back to|dating to|around|early|late|mid)[- ](?:(?:january|february|march|april|may|june|"
    r"july|august|september|october|november|december)\s+)?((?:19|20)\d{2})\b", re.I)
_YEAR_NOUN = re.compile(
    r"\b((?:19|20)\d{2})\s+(case|incident|murder|killing|attack|robbery|heist|fraud|tender|contract|shooting|"
    r"assault|rape|theft|disappearance|arrest|matter)\b", re.I)

_FIXTURE_CONDUCT = re.compile(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$")


def _clean(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _headline(title: str) -> str:
    # "Headline - Outlet" from Google News: the outlet name is not evidence.
    t = _clean(title)
    return re.split(r"\s+[-|]\s+(?=[^-|]*$)", t)[0] if " - " in t or " | " in t else t


def _kind_in(text: str) -> Optional[str]:
    if not text:
        return None
    if _COURT.search(_COURT_NOISE.sub(" ", text)):
        return "court"
    if _ARREST.search(text):
        return "arrest"
    if _INVESTIGATION.search(text):
        return "investigation"
    return None


def step_kind(title: str, summary: str = "", article_text: str = "") -> tuple[Optional[str], str]:
    """What the record is mainly about, read headline first, then summary,
    then the article's opening. Returns (kind, where it was read)."""
    for text, where in ((_headline(title), "headline"), (_clean(summary)[:400], "summary"),
                        (_clean(article_text)[:600], "article lead")):
        k = _kind_in(text)
        if k:
            return k, where
    return None, ""


def _end_of(precision_date: str) -> Optional[date]:
    """Last day a 'YYYY', 'YYYY-MM', 'YYYY-MM-DD' or 'A/B' range could mean."""
    if not precision_date:
        return None
    s = str(precision_date).strip()
    if "/" in s:
        s = s.split("/")[-1].strip()
    m = _FIXTURE_CONDUCT.match(s)
    if not m:
        return None
    y = int(m.group(1))
    if m.group(3):
        return date(y, int(m.group(2)), int(m.group(3)))
    if m.group(2):
        mo = int(m.group(2))
        return date(y, mo, calendar.monthrange(y, mo)[1])
    return date(y, 12, 31)


def _to_date(v) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _text_conduct(text: str, step: date) -> list[tuple[str, str]]:
    """(conduct_date, phrase) pairs the words give, older than the step year
    or tied to a long investigation."""
    out = []
    for m in _YEAR_LEAD.finditer(text):
        y = int(m.group(2))
        if step.year - 30 <= y < step.year:
            out.append((str(y), m.group(0)))
    for m in _YEAR_NOUN.finditer(text):
        y = int(m.group(1))
        if step.year - 30 <= y < step.year:
            out.append((str(y), m.group(0)))
    if _LAST_YEAR.search(text):
        out.append((str(step.year - 1), "last year"))
    return out


def _mention_conduct(mentions: Iterable[dict], step: date, older_days: int) -> list[tuple[str, str]]:
    """Past dates in the article text older than the step by older_days.
    Future dates (the next court date) are never conduct."""
    out = []
    limit = step - timedelta(days=older_days)
    for m in mentions or []:
        if not isinstance(m, dict):
            m = getattr(m, "__dict__", {})
        if m.get("kind") != "past" or not m.get("date"):
            continue
        if m.get("weekday_ok") is False:
            continue
        end = _end_of(m["date"])
        if end and end < limit:
            out.append((m["date"], m.get("phrase", "")))
    return out


def assess(title: str, summary: str = "", *,
           step_date=None,
           article_text: str = "",
           date_mentions: Optional[Iterable[dict]] = None,
           known_conduct_date: Optional[str] = None,
           known_step_date=None,
           window_start=None,
           older_days: int = OLDER_DAYS) -> dict:
    """Is this record a court or arrest step in an older case?

    step_date: the record's own date (article published date, else feed
    date). known_conduct_date / known_step_date: dates checked by hand (the
    sweep notes, carried in the bench fixtures), e.g. '2025-04-17',
    '2023', '2026-05/2026-07'. window_start: a fixed run start; conduct
    before it is older too. article_text must already have reader comments
    removed (article_reader.extract_body does that).
    """
    title, summary, article_text = title or "", summary or "", article_text or ""
    step = _to_date(known_step_date) or _to_date(step_date)
    kind, where = step_kind(title, summary, article_text)
    res = {
        "court_step": False,
        "step_kind": kind,
        "older_conduct": False,
        "conduct_date": None,
        "step_date": step.isoformat() if step else None,
        "step_basis": "",
        "new_event": None,
    }
    if kind is None:
        res["step_basis"] = "no court, arrest or investigation step in the record"
        return res

    evidence: list[tuple[str, str, str]] = []   # (conduct_date, phrase, source)
    if step:
        limit = step - timedelta(days=older_days)
        ws = _to_date(window_start)
        if known_conduct_date:
            end = _end_of(known_conduct_date)
            if end and (end < limit or (ws and end < ws)):
                evidence.append((str(known_conduct_date), str(known_conduct_date), "checked fixture"))
        for text, src in ((_headline(title), "headline"), (_clean(summary), "summary"),
                          (_clean(article_text), "article text")):
            for d, phrase in _text_conduct(text, step):
                evidence.append((d, phrase, src))
        for d, phrase in _mention_conduct(date_mentions or [], step, older_days):
            evidence.append((d, phrase, "article dates"))
        if ws:
            for d, phrase in _mention_conduct(date_mentions or [], ws + timedelta(days=older_days), older_days):
                if (d, phrase, "article dates") not in evidence:
                    evidence.append((d, phrase, "article dates (before the run start)"))

    signs: list[str] = []
    for text, src in ((_headline(title), "headline"), (_clean(summary), "summary"),
                      (_clean(article_text)[:1500], "article text")):
        m = _LONG_INVESTIGATION.search(text)
        if m:
            signs.append(f'{src}: "{m.group(0)}"')
        m = _FOLLOW_UP.search(text)
        if m and kind in ("arrest", "investigation"):
            signs.append(f'{src}: follow-up in an existing case, "{m.group(0)}"')

    older = bool(evidence) or any("follow-up" not in s for s in signs)
    res["older_conduct"] = older
    if evidence:
        # Earliest conduct date given, at the precision it was given.
        best = min(evidence, key=lambda e: (_end_of(e[0]) or date.max, -len(e[0])))
        res["conduct_date"] = best[0]

    if kind == "court":
        res["court_step"] = True
    else:
        res["court_step"] = bool(older or signs)
    res["new_event"] = not res["court_step"]

    parts = [f"{kind} step read in the {where}"]
    if evidence:
        parts.append("older conduct: " + "; ".join(f'{s} "{p}"' if p != d else f"{s} {d}"
                                                  for d, p, s in evidence[:3]))
    if signs:
        parts.append("signs: " + "; ".join(signs[:2]))
    if not res["court_step"]:
        parts.append(f"no sign of older conduct, so the {kind} is treated as the event")
    elif kind == "court" and not older:
        parts.append("conduct date not given")
    res["step_basis"] = ". ".join(parts)
    return res


def metadata(result: dict) -> dict:
    """The keys a collector adds to a signal's metadata. Empty when the
    record is not a step, so ordinary records carry nothing new."""
    if not result.get("court_step"):
        return {}
    keys = ("court_step", "step_kind", "older_conduct", "conduct_date", "step_date",
            "step_basis", "new_event")
    return {k: result[k] for k in keys if result.get(k) is not None}


def assess_reading(title: str, summary: str, reading, *, feed_date=None,
                   fixture: Optional[dict] = None, window_start=None) -> dict:
    """assess() fed from an article_reader.ArticleReading (or its dict) and,
    when given, a bench fixture's manual_facts. Only a reading with status
    'read' contributes article text and dates."""
    r = reading if isinstance(reading, dict) else (reading.to_dict() if reading is not None else {})
    read = r.get("status") == "read"
    fixture = fixture or {}
    step = (r.get("published") or "")[:10] if read and r.get("published") else None
    step = step or fixture.get("published") or fixture.get("feed_date") or feed_date or r.get("feed_date")
    return assess(
        title, summary,
        step_date=step,
        article_text=r.get("body_text", "") if read else "",
        date_mentions=r.get("date_mentions") if read else None,
        known_conduct_date=fixture.get("conduct_date"),
        window_start=window_start,
    )


def story_flags(results: list[dict]) -> list[dict]:
    """Share a step flag across the records of one story (story_merge group).

    Most Google News copies of a story are headline-only, so only the record
    with a summary or article text can show the older conduct (the airport
    officers: \"after a five-month investigation\" is only in one summary).
    When any record of the story is a step with older conduct, an arrest or
    investigation record in the same story is that step too; records that
    are not about an arrest, investigation or court step are left alone.
    Returns new dicts; the inputs are not changed.
    """
    lead = next((r for r in results if r.get("court_step") and r.get("older_conduct")), None)
    out = []
    for r in results:
        r2 = dict(r)
        if lead is not None and not r2.get("court_step") and r2.get("step_kind") in ("arrest", "investigation"):
            r2.update(court_step=True, new_event=False, older_conduct=True,
                      conduct_date=r2.get("conduct_date") or lead.get("conduct_date"),
                      step_basis=(r2.get("step_basis", "") + ". shared from another record of the same story: "
                                  + lead.get("step_basis", "")[:200]).strip(". "))
        out.append(r2)
    return out
