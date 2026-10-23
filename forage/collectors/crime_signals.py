"""
Crime & Security classifier for killings and attempted murders, shared by
the collectors (Batch 2, 23 Oct 2026).

Why it exists
-------------
The four town sweeps of 23 October showed killings and shootings landing in
Africa & World (GLOBAL). The Generalized News Monitor picked the stream from
the search words (a query with "murder" in it went to CRIME_INTEL, anything
else to GLOBAL), and the Civic Intelligence collector starts each source in a
fixed stream and only moves a story to Crime on arrest, charge and corruption
words. So "Man killed, 2 injured after vehicle chase at Polokwane Cricket
Club" from a "Polokwane hospital" search went to GLOBAL, while a head-on
crash on the R524 from a "Makhado police murder" search went to Crime.

This module reads the story itself (headline and summary), not the search
words. Both collectors call ``route`` after their own rules and the
infrastructure step, and before the Priority Intelligence step.

What it decides (``crime_kind``)
--------------------------------
  killing           the story says someone was killed by another person: shot
                    dead, stabbed to death, murdered, killed in an attack, mob
                    justice that cost a life.
  attempted_murder  a non-fatal shooting or stabbing (attempted murder, or a
                    shooting or stabbing the victim survived). Stays in
                    Crime & Security (owner decision, 23 Oct 2026).
  unresolved_death  a body found with no cause given. Carries
                    crime_cause_unknown=True and is never labelled a killing
                    (the owner's Madombidzha and Mashawana rule).
  review            violence or a death whose outcome or circumstances the
                    story does not state: a shooting or stabbing with no
                    outcome, or deaths reported with a police probe, injuries
                    or a chase but no cause (the Polokwane Cricket Club case).
                    Carries crime_review=True; never labelled a killing.

What it excludes (``crime_exclusion``)
--------------------------------------
  road_crash             crashes, collisions, accidents on the road, unless
                         the story names a crime (hit-and-run, culpable
                         homicide, drunk driving, a shooting, a hijacking...).
  drowning_no_foul_play  drownings with no sign of foul play.
  accident_or_disaster   fires, storms, lightning, collapses, electrocution,
                         animal attacks and the like, with no crime word.
  court_step             a court step (postponed, trial, bail, sentenced...).
                         Not labelled a new killing. Batch 6 flags court steps
                         on older cases; this module only refuses to call them
                         new killings, and leaves their stream as it was.

Routing rules
-------------
* A killing, attempted murder, unresolved death or review case goes to
  CRIME_INTEL from GLOBAL when the story is South African (pi_signals.is_sa).
  A story already in CRIME_INTEL stays there and gains the label.
* A road crash, drowning or accident sitting in CRIME_INTEL goes to GLOBAL:
  only the search words or a collector keyword rule put it there.
* INFRASTRUCTURE and PRIORITY are never touched here. Priority Intelligence
  runs afterwards with its standalone rule unchanged.
* Nothing here adds a place, a date, a merge key or a court-step flag: those
  are Batches 3, 5 and 6.
"""

from __future__ import annotations

import re
from typing import Optional

try:
    from forage.collectors import pi_signals as _pi
except ImportError:  # pragma: no cover - script use
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from forage.collectors import pi_signals as _pi

STREAM = "CRIME_INTEL"
DEMOTE_TO = "GLOBAL"

KIND_LABELS = {
    "killing": "Killing",
    "attempted_murder": "Attempted murder (non-fatal shooting or stabbing)",
    "unresolved_death": "Unresolved death (cause unknown)",
    "review": "Violence or death, outcome or cause not stated (review)",
}
CRIME_KINDS = tuple(KIND_LABELS)
EXCLUSIONS = ("road_crash", "drowning_no_foul_play", "accident_or_disaster", "court_step")
# Exclusions that move a story out of CRIME_INTEL when only the search words
# or a keyword rule put it there. Court steps keep their stream.
_DEMOTING = {"road_crash", "drowning_no_foul_play", "accident_or_disaster"}


def _rx(p: str) -> re.Pattern:
    return re.compile(p, re.I)


# Not news about violence at all.
_NOISE = _rx(
    r"\b(movie|film|series|season \d|episode|trailer|box office|album|netflix|showmax|video game|"
    r"shooting (range|star|guard|stars|script|schedule|location)|photo ?shoot|sharpshoot\w*|"
    r"heart attack|panic attack|asthma attack|killed it|killing it|shot (on goal|at goal|stopper)|"
    r"shoot-?out (win|loss|victory|defeat)|penalty shoot)\b")

_DEATH_WORD = r"(deaths?|dead|died|dies|killed|kills?|killing|fatal(ly|ities)?|lost (his|her|their) li(fe|ves)|claim(s|ed)? (a |another |\w+ )?li(fe|ves))"
_DEATH = _rx(rf"\b{_DEATH_WORD}\b")

# Explicit killings: the story itself says a person killed someone.
_KILLING = _rx(
    r"\b(shot dead|shot and killed|shot to death|gunned down|stabbed to death|hacked to death|"
    r"beaten to death|bludgeoned|strangled|burnt to death by|burned to death by|necklac\w*|lynch\w*|"
    r"murder(ed|s|ing)?|killing of|killed in (an? |the )?(attack|shooting|stabbing|robbery|ambush|hijacking|mob)|"
    r"attacked and killed|assassinat\w*|executed by|died (after|from|of) (being |his |her )?(shot|stabbed|attacked|assaulted|beaten|gunshot|stab)\w*|"
    r"fatal(ly)? (shot|shooting|stabbing|stabbed|assault\w*|attack\w*)|"
    r"(shooting|stabbing|attack|ambush) (claims|claimed|leaves|left) \w+ (dead|li(fe|ves))|"
    r"mob (justice|attack)\w*[^.]{0,40}\b(li(fe|ves)|dead|killed|died|death)|"
    r"\b(li(fe|ves)|dead|killed|died|death)[^.]{0,40}\bmob (justice|attack))\b")
# "attempted murder" must not read as a killing.
_ATTEMPTED = _rx(r"\b(attempted murders?|assassination attempts?|attempted assassination|attempts? on (his|her|their|the \w+'?s?) li(fe|ves)|survived (an? )?(assassination|hit|attempt))\b")

_SHOOT_STAB = r"(shot|shooting|shootings|stabbed|stabbing|stabbings|hacked|panga attack|axe attack|gunmen|gunfire|opened fire|knife attack)"
_VIOLENCE = _rx(rf"\b({_SHOOT_STAB}|attack(ed|s)?|assault(ed|s)?|beaten|mob justice|mob attack)\b")
_SHOOT_STAB_RX = _rx(rf"\b{_SHOOT_STAB}\b")
_SURVIVED = _rx(r"\b(injur(ed|ies|y)|wounded|survived|surviv\w+|hospitali[sz]ed|in hospital|critical condition|"
                r"stable condition|left for dead|recovering|rushed to hospital)\b")

# A body found with no cause: never a killing.
_BODY_FOUND = _rx(
    r"\b(bod(y|ies)\b[^.]{0,60}\b(found|discovered|recovered|retrieved|dumped|spotted|floating)|"
    r"(found|discovered|recovered|retrieved)\b[^.]{0,30}\bbod(y|ies)|"
    r"bodies (of|on|in|at|near)\b|found dead|found (lifeless|floating|hanging|motionless)|lifeless body|"
    r"decomposed|decomposing|skeletal remains|human remains|remains (were |have been )?found|corpse)")

# Deaths with a police probe, injuries or a chase, but no cause: review.
_DEATH_CONTEXT = _rx(
    r"\b(police (probe|probing|investigat\w*|are investigating|opened|launched|hunt\w*|search\w*)|"
    r"investigation|inquest|foul play|suspects?|manhunt|case of|docket|injured|chase|"
    r"parking lot incident|tavern|shebeen)\b")

_ROAD_CRASH = _rx(
    r"\b(crash(es|ed)?|collisions?|collided|head-?on|pile-?up|overturn(ed|s)?|accidents?|"
    r"(car|vehicle|truck|bus|taxi|bakkie|minibus) (rolled|plunged|veered|capsized)|"
    r"knocked (down|over) by|run over|pedestrian (killed|knocked|dies|died)|road (carnage|fatalit\w*))\b")
_ROAD_CTX = _rx(
    r"\b(road|roads|R\d{2,3}|N\d{1,2}|highway|freeway|intersection|vehicles?|cars?|trucks?|bus|buses|taxis?|"
    r"bakkies?|minibus\w*|motorists?|drivers?|pedestrians?|motorcycle\w*|motorbike|collision|head-?on|pile-?up)\b")
# A crime named in a crash story keeps it out of the road-crash exclusion.
_CRASH_CRIME = _rx(
    r"\b(hit[- ]and[- ]run|culpable homicide|drunk(en)?[- ]driv\w*|driving under the influence|reckless|"
    r"negligent driving|dangerous driving|arrest\w*|charged|fled the scene|stolen (car|vehicle|bakkie)|hijack\w*|shot|shooting|stabb\w*|"
    r"murder\w*|robbery|robbers|cash[- ]in[- ]transit|police chase|fleeing (police|suspects))\b")

_DROWNING = _rx(r"\b(drown(ed|ing|s)?)\b")
_PERSON = _rx(r"\b(man|men|woman|women|boy|boys|girl|girls|child|children|kids?|learners?|pupils?|teens?|teenager|"
               r"toddler|baby|person|people|fisherm[ae]n|swimmers?|students?|residents?|\d+)\b")
_FOUL_PLAY = _rx(r"\b(foul play (is |was )?suspected|suspect(s|ed)? foul play|murder\w*|assault\w*|pushed|thrown|"
                 r"tied up|bound|stab\w*|shot)\b")
_NO_FOUL_PLAY = _rx(r"\b(no (sign of )?foul play|foul play (is |was |has been )?(not suspected|ruled out)|"
                    r"not suspect(ing)? foul play|do not suspect foul play|does not suspect foul play)\b")

_DISASTER = _rx(
    r"\b(fire|blaze|flames|shack fire|house fire|veld ?fire|storm|lightning|flood(s|ing|ed)?|tornado|"
    r"collapse[ds]?|electrocut\w*|explosion at (the )?mine|mine accident|mudslide|landslide|stampede|"
    r"gas leak|carbon monoxide|food poisoning|illness|outbreak|cholera|disease|"
    r"(elephant|hippo|crocodile|lion|leopard|snake|dog|dogs|shark|buffalo|bees|rhino) (attack|bite|trampl)\w*|"
    r"(attacked|mauled|trampled|bitten|killed|gored) by (an? )?(elephant|hippo|crocodile|lion|leopard|snake|dogs?|shark|buffalo|bees))\b")
_DISASTER_CRIME = _rx(r"\b(arson|petrol bomb\w*|set alight|set on fire|torched|murder\w*|shot|shoot\w*|stabb\w*|"
                      r"gunm[ae]n|gunfire|opened fire|open fire|poisoned by|deliberately)\b")

_COURT = _rx(
    r"\b(court|trial|bail|postpon\w*|sentenc\w*|convicted|conviction|acquitted|verdict|plea|judge|"
    r"magistrate|charge sheet|case against|accused|appear(ed|s|ance) (in|before)|life imprisonment|"
    r"years? (behind bars|in prison|imprisonment|jail))\b")


def _first(pat: re.Pattern, text: str) -> Optional[str]:
    m = pat.search(text)
    return m.group(0) if m else None


def classify(title: str, summary: str = "") -> dict:
    """Read the story and return what kind of Crime & Security event it is.

    Returns a dict with ``kind`` (one of CRIME_KINDS or None), ``exclusion``
    (one of EXCLUSIONS or None) and ``matched`` (the words that decided it).
    Pure: no places, no network, no database.
    """
    text = f"{title or ''}. {(summary or '')[:800]}"
    out = {"kind": None, "exclusion": None, "matched": None}
    if _NOISE.search(text) and not _first(_KILLING, text):
        return out

    # Exclusions first: an accident is not a killing whatever the search was.
    if _ROAD_CRASH.search(text) and _ROAD_CTX.search(text) and not _CRASH_CRIME.search(text):
        out.update(exclusion="road_crash", matched=_first(_ROAD_CRASH, text))
        return out
    if (_DROWNING.search(text) and (_DEATH.search(text) or _PERSON.search(text))
            and (_NO_FOUL_PLAY.search(text) or not _FOUL_PLAY.search(text))):
        out.update(exclusion="drowning_no_foul_play", matched=_first(_DROWNING, text))
        return out
    if _DISASTER.search(text) and not _DISASTER_CRIME.search(text):
        if _DEATH.search(text) or _VIOLENCE.search(text):
            out.update(exclusion="accident_or_disaster", matched=_first(_DISASTER, text))
        return out
    if _COURT.search(text) and (_DEATH.search(text) or _VIOLENCE.search(text) or _ATTEMPTED.search(text)
                               or _KILLING.search(text)):
        out.update(exclusion="court_step", matched=_first(_COURT, text))
        return out

    # Attempted murder before killing: "attempted murder" contains "murder".
    attempted = _first(_ATTEMPTED, text)
    text_wo_attempt = _ATTEMPTED.sub(" ", text)
    killing = _first(_KILLING, text_wo_attempt)
    if attempted and not killing:
        out.update(kind="attempted_murder", matched=attempted)
        return out
    if killing:
        out.update(kind="killing", matched=killing)
        return out

    body = _first(_BODY_FOUND, text)
    if body:
        out.update(kind="unresolved_death", matched=body)
        return out

    death = _first(_DEATH, text)
    shoot = _first(_SHOOT_STAB_RX, text)
    violence = _first(_VIOLENCE, text)
    if shoot and not death:
        survived = _first(_SURVIVED, text)
        if survived:
            out.update(kind="attempted_murder", matched=f"{shoot}; {survived}")
        else:
            out.update(kind="review", matched=shoot)
        return out
    if death and violence:
        # A death the story ties to a shooting, stabbing or attack.
        out.update(kind="killing", matched=f"{violence}; {death}")
        return out
    if death:
        ctx = _first(_DEATH_CONTEXT, text)
        if ctx:
            out.update(kind="review", matched=f"{death}; {ctx}")
        return out
    return out


def route(stream: str, title: str, summary: str = "", province: Optional[str] = None,
          sa_source: bool = False) -> tuple[str, bool, dict]:
    """Apply the classifier on top of a collector's own routing.

    ``stream`` is what the collector decided so far (after its own rules and
    the infrastructure step, before Priority Intelligence). Returns
    (stream, is_crime_event, crime_meta). crime_meta is empty when the story
    is neither a crime kind nor an exclusion.
    """
    if stream in ("INFRASTRUCTURE", _pi.STREAM):
        return stream, False, {}
    c = classify(title, summary)
    if not c["kind"] and not c["exclusion"]:
        return stream, False, {}
    meta: dict = {"crime_matched": c["matched"]}
    if c["exclusion"]:
        meta["crime_exclusion"] = c["exclusion"]
        if stream == STREAM and c["exclusion"] in _DEMOTING:
            meta["crime_route"] = "moved_out"
            meta["crime_reason"] = f"not a crime by the story's words ({c['exclusion']}); moved from CRIME_INTEL"
            return DEMOTE_TO, False, meta
        meta["crime_route"] = "unchanged"
        meta["crime_reason"] = f"not labelled a killing ({c['exclusion']})"
        return stream, False, meta

    kind = c["kind"]
    meta.update({
        "crime_kind": kind,
        "crime_label": KIND_LABELS[kind],
        "crime_reason": f"{KIND_LABELS[kind]}: {c['matched']}",
    })
    if kind == "unresolved_death":
        meta["crime_cause_unknown"] = True
    if kind in ("unresolved_death", "review"):
        meta["crime_review"] = True
    if stream == STREAM:
        meta["crime_route"] = "kept"
        return STREAM, True, meta
    text = f"{title}. {summary}"
    if not _pi.is_sa(text, province=province, sa_source=sa_source):
        meta["crime_route"] = "not_moved_not_south_african"
        return stream, False, meta
    meta["crime_route"] = "moved_in"
    return STREAM, True, meta
