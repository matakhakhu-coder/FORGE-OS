import re
from typing import Any, Dict, List

# Simple lexicon-based signal classification/extraction.
EVENT_KEYWORDS = {
    "protest": ["protest", "demonstration", "strike"],
    "conflict": ["shooting", "firefight", "clash", "battle", "attack", "bomb"],
    "economic": ["market", "stocks", "price", "inflation", "economy"],
    "legal": ["court", "indictment", "investigation", "conviction", "lawsuit"],
    "anomaly": ["explosion", "outage", "failure", "vulnerability", "breach"],
}

ACTOR_PATTERNS = {
    "NPA": re.compile(r"\b(NPA|National Prosecuting Authority)\b", re.I),
    "government": re.compile(r"\b(government|minister|department)\b", re.I),
    "company": re.compile(r"\b(inc\.?|ltd\.?|corporation|company|firm)\b", re.I),
    "location": re.compile(r"\b(South Africa|SA|Johannesburg|Cape Town|Pretoria)\b", re.I),
}

# Sprint 1 (2026-07-23): ACTOR_PATTERNS key -> actors table type. Used by
# _extract_actor_types() so callers (EntityResolver, entity_engine) can
# create actors with the right type instead of defaulting everyone to
# "institution". "government"/"company" matches are all blocked by
# _GENERIC_ACTOR_TERMS below in practice (bare words, never real names),
# so only "NPA" and "location" ever reach actor creation.
_ACTOR_PATTERN_TYPE_MAP = {
    "NPA":        "institution",
    "government": "institution",
    "company":    "organization",
    "location":   "location",
}

SEVERITY_WEIGHTS = {
    # Sprint 6 (2026-08-02): original list was ACLED/armed-conflict
    # vocabulary (hijack/bomb/murder/assault/attack/violence/raid) and
    # missed how SA crime/corruption journalism is actually written.
    # Verified live against the corpus before adding anything below —
    # e.g. "fraud" appears in 374 signal titles and previously scored
    # severity=0.0 on every single one. See docs/ROADMAP.md Sprint 6.
    "critical": [
        "hijack", "bomb", "murder", "assault",
        "rape", "massacre", "shot dead", "kidnap", "stabb",
        "gbv", "gender-based violence",
    ],
    "high": [
        "attack", "violence", "arrest", "raid",
        "fraud", "heist", "manhunt", "robbery", "robbed",
        "extortion", "shooting", "stolen",
    ],
    "medium": [
        "investigation", "protest", "sanction",
        "seized", "seizure", "firearms", "ammunition",
    ],
    "low": ["report", "meeting", "statement"],
    # Phase 64 Epsilon-III — Investigative Journalism Tier
    # These keywords signal deep OSINT/investigative content that the kinetic
    # scoring model previously underweighted. Each maps to a gravity boost:
    #   inv_critical (+0.85): structural criminal enterprise indicators
    #   inv_high     (+0.55): corruption / judicial interference signals
    #   inv_medium   (+0.35): financial crime / accountability journalism
    "inv_critical": [
        "racketeering", "money-laundering", "money laundering",
        "shell company", "shell structure", "judicial interference",
        "trafficking", "syndicate",
    ],
    "inv_high": [
        "bribes", "bribery", "convicted", "smuggling", "unaccounted for",
        "fraudulent contract", "corrupt", "poaching syndicate",
        "elite capture", "forged documents",
    ],
    "inv_medium": [
        "charges", "indicted", "laundering", "corporate opacity",
        "transparency deficit", "no eia", "legislative vacuum",
        "ministerial interference", "community revenue",
    ],
}

# Sprint 6 (2026-08-02): left-word-boundary regex per term, not plain
# substring `in` matching. Short stems added above ("rape", "gbv") collide
# with unrelated words under plain substring matching — confirmed live:
# "rape" matched inside "therapeutic", "Orapeleng" (a person's first name),
# "DRAPEKO"/"KRAPE MYRTLE"/"TRAPEZNIKOV" (OFAC SDN entity names). A left
# boundary (\bterm, not \bterm\b) fixes that while still preserving the
# inflection-catching plain substring matching relied on — "arrest" still
# matches "arrested"/"arrests", "murder" still matches "murders", etc. —
# since those inflections are suffixes, not prefixes, of the stem.
_SEVERITY_PATTERNS = {
    tier: [(term, re.compile(r"\b" + re.escape(term), re.I)) for term in terms]
    for tier, terms in SEVERITY_WEIGHTS.items()
}


def _score_severity(text: str) -> float:
    """Return clamped severity score. See score_severity_detailed() for audit trail."""
    return score_severity_detailed(text)["severity"]


def score_severity_detailed(text: str) -> dict:
    """
    Phase 68 — Auditable severity scoring.

    Returns a dict:
      severity              float   clamped [0.0, 1.0]
      investigative_uplift  float   sum of inv_* tier contributions (pre-clamp)
      investigative_tier    str     'inv_critical' | 'inv_high' | 'inv_medium' | ''
      matched_inv_keywords  list    which investigative keywords fired
    """
    if not text:
        return {
            "severity": 0.0,
            "investigative_uplift": 0.0,
            "investigative_tier": "",
            "matched_inv_keywords": [],
        }

    score = 0.0
    inv_uplift = 0.0
    matched_inv: list[str] = []
    highest_inv_tier = ""
    tier_rank = {"inv_critical": 3, "inv_high": 2, "inv_medium": 1, "": 0}

    txt = text.lower()
    for weight, terms in _SEVERITY_PATTERNS.items():
        for term, pattern in terms:
            if pattern.search(txt):
                if weight == "critical":
                    score += 1.0
                elif weight == "high":
                    score += 0.6
                elif weight == "medium":
                    score += 0.3
                # Phase 64 Epsilon-III — Investigative Journalism Tier
                elif weight == "inv_critical":
                    contribution = 0.85
                    score     += contribution
                    inv_uplift += contribution
                    matched_inv.append(term)
                    if tier_rank["inv_critical"] > tier_rank.get(highest_inv_tier, 0):
                        highest_inv_tier = "inv_critical"
                elif weight == "inv_high":
                    contribution = 0.55
                    score     += contribution
                    inv_uplift += contribution
                    matched_inv.append(term)
                    if tier_rank["inv_high"] > tier_rank.get(highest_inv_tier, 0):
                        highest_inv_tier = "inv_high"
                elif weight == "inv_medium":
                    contribution = 0.35
                    score     += contribution
                    inv_uplift += contribution
                    matched_inv.append(term)
                    if tier_rank["inv_medium"] > tier_rank.get(highest_inv_tier, 0):
                        highest_inv_tier = "inv_medium"
                else:
                    score += 0.1

    return {
        "severity":              round(min(score, 1.0), 4),
        "investigative_uplift":  round(min(inv_uplift, 1.0), 4),
        "investigative_tier":    highest_inv_tier,
        "matched_inv_keywords":  matched_inv,
    }


# Sprint 6 follow-up (2026-08-02) — sentiment was never computed at all.
# interpret() never set a "sentiment" key, so gravity_engine's
# inputs.get("sentiment", 0.0) always hit its default, normalising to a
# permanently neutral 0.5 urgency_sentiment for every signal — 15% of the
# gravity formula's weight that could never differentiate anything.
#
# Convention matches the ACLED path's existing _ACLED_EVENT_SENTIMENT map:
# range [-1.0, +1.0], negative = urgent/bad news, positive = routine/good
# news (e.g. "Violence against civilians": -1.0).
#
# Two components, not a single new lexicon built from scratch:
#   1. Severity-derived: -severity directly. Crime/urgency content is
#      inherently negative-urgent, and SEVERITY_WEIGHTS is
#      already an evidence-checked signal (verified against real corpus
#      frequency above) -- re-deriving polarity for the same vocabulary
#      from zero would just duplicate that work with less rigor.
#   2. A small supplementary lexicon for polarity severity's crime-focused
#      vocabulary doesn't cover -- non-criminal bad news (an outage, a
#      resignation, a scandal) and genuinely good news (a launch, an
#      award). Every term below was checked against real corpus title
#      frequency before inclusion (33-370 hits each) -- none are guesses.
#
# Each term below is a left-boundary stem, not an exact word -- "resign"
# already matches "resignation" the same way "arrest" matches "arrested"
# in SEVERITY_WEIGHTS, so a longer inflection is only listed separately
# when it does NOT share a prefix with a shorter term already in the
# list (e.g. "failure"/"failed" both stem from "fail" but neither is a
# prefix of the other, so both are needed). Listing both a stem and its
# own inflection (the original draft had "launch" and "launches" as
# separate entries) double-counts a single real-world word as two hits.
NEGATIVE_SENTIMENT_TERMS = [
    "crisis", "collapse", "scandal", "failure", "failed", "shortage",
    "outage", "controversy", "backlash", "slammed", "warns", "warning",
    "outbreak", "disaster", "emergency", "resign", "fired",
    "dies", "death", "died", "killed", "delay",
]
POSITIVE_SENTIMENT_TERMS = [
    "launch", "wins", "award", "success",
    "celebrates", "celebration", "improve",
    "progress", "recovery", "growth", "breakthrough", "milestone",
    "approved", "grant",
]

# Reuses the same left-word-boundary matching as _SEVERITY_PATTERNS --
# same collision-avoidance rationale (see note above _SEVERITY_PATTERNS).
_NEGATIVE_SENTIMENT_PATTERNS = [
    (term, re.compile(r"\b" + re.escape(term), re.I)) for term in NEGATIVE_SENTIMENT_TERMS
]
_POSITIVE_SENTIMENT_PATTERNS = [
    (term, re.compile(r"\b" + re.escape(term), re.I)) for term in POSITIVE_SENTIMENT_TERMS
]


def score_sentiment(text: str, severity: float = 0.0) -> float:
    """
    Sentiment in [-1.0, +1.0] for gravity_engine's urgency_sentiment input.
    `severity` should be the value already computed by
    score_severity_detailed() for the same text -- passed in rather than
    recomputed here so callers that already have it (interpret()) don't
    pay for it twice.
    """
    if not text:
        return 0.0
    txt = text.lower()

    neg_hits = sum(1 for _, pattern in _NEGATIVE_SENTIMENT_PATTERNS if pattern.search(txt))
    pos_hits = sum(1 for _, pattern in _POSITIVE_SENTIMENT_PATTERNS if pattern.search(txt))
    lexicon_component = max(-1.0, min(1.0, 0.3 * (pos_hits - neg_hits)))

    severity_component = -max(0.0, min(1.0, severity))

    return round(max(-1.0, min(1.0, severity_component + lexicon_component)), 4)


# Strings matched by ACTOR_PATTERNS that are category indicators, not proper
# actor names. These must not be inserted as actor records in the graph.
_GENERIC_ACTOR_TERMS = frozenset({
    "government", "minister", "department",
    "inc", "inc.", "ltd", "ltd.", "corporation", "company", "firm",
})


def _extract_actors(text: str) -> List[str]:
    """
    Extract proper actor name strings from text using ACTOR_PATTERNS.

    Uses findall() to capture the actual matched text (e.g. "Cape Town",
    "National Prosecuting Authority") rather than the dict key ("location",
    "NPA"). Generic category words are filtered via _GENERIC_ACTOR_TERMS.
    """
    actors: set[str] = set()
    for pattern in ACTOR_PATTERNS.values():
        for match in pattern.findall(text):
            if match and match.lower().rstrip(".") not in _GENERIC_ACTOR_TERMS:
                actors.add(match)
    return sorted(actors)


def _extract_actor_types(text: str) -> Dict[str, str]:
    """
    Same extraction as _extract_actors(), but keeps the ACTOR_PATTERNS
    category each match came from, mapped to an actors-table type
    (_ACTOR_PATTERN_TYPE_MAP). This is what _extract_actors() itself
    discards (it returns a flat, category-less list) — that discard is
    the root cause of place names defaulting to "institution" downstream.
    """
    typed: Dict[str, str] = {}
    for category, pattern in ACTOR_PATTERNS.items():
        mapped_type = _ACTOR_PATTERN_TYPE_MAP.get(category, "institution")
        for match in pattern.findall(text):
            if match and match.lower().rstrip(".") not in _GENERIC_ACTOR_TERMS:
                typed[match] = mapped_type
    return typed


# Sprint 6 (2026-08-02): signal.get("source") holds specific collector IDs
# ("saps_media", "news24_crime", "dailymaverick_corruption", "ofac_sdn"),
# not the broad category labels ("NPA", "SAPS", "RSS", "GOVERNMENT") the
# old credibility_map/high_value_sources dicts in interpret() keyed on —
# verified against the live DB's 36 distinct signals.source values, none
# of which matched those broad labels except by accident. That mismatch
# meant both inputs silently fell back to their defaults for ~99% of the
# corpus, freezing 25% of the gravity formula's weight regardless of
# actual source quality. Tiers below are evidence-based (checked against
# real per-source signal counts) and consistent in spirit with
# forage/utils/admiralty.py's existing A/B/C/D source grading, though kept
# local here rather than imported — admiralty's short-name coverage
# targets artifacts.source, a different column with a different naming
# convention, and unifying the two is a larger separate change. The old
# broad labels are kept alongside the real IDs in case any
# not-currently-active collector still emits them. Lookup is
# case-insensitive since collectors are inconsistent in practice (GDACS
# is stored uppercase in this DB; most others aren't).
_SOURCE_CREDIBILITY_TIERS: List[tuple] = [
    (frozenset({  # primary official / prosecutorial / structured-scientific
        "npa", "npa_media", "hawks", "hawks_media", "saps", "saps_media",
        "government", "pmg_parliamentary", "treasury", "fpb_enforcement",
        "cipc_collector", "saflii", "pdf_infiltrator", "analyst_manual",
        "usgs", "firms",
    }), 0.90),
    (frozenset({  # established investigative journalism
        "amabhungane", "dailymaverick", "dailymaverick_corruption",
        "occrp_us", "icij", "propublica", "oxpeckers", "groundup",
        "revealnews", "theintercept", "news24_crime", "timeslive_corruption",
        "civic",
    }), 0.80),
    (frozenset({  # general SA/regional news, structured official feeds
        "defenceweb", "citizen_news", "eskom_news", "municipal_infrastructure",
        "us_infrastructure", "ofac_sdn", "forge_incident",
        "disease_outbreak_collector", "gdacs", "gdelt",
    }), 0.65),
    (frozenset({"google_news_monitor", "rss"}), 0.55),     # aggregators, low editorial vetting
    (frozenset({"dork", "x_pulse", "unverified"}), 0.35),  # scraped / social / explicitly unverified
]


def _source_credibility(source: str) -> float:
    s = (source or "").strip().lower()
    for tier_sources, weight in _SOURCE_CREDIBILITY_TIERS:
        if s in tier_sources:
            return weight
    return 0.5  # unknown source — neutral default (was the universal fallback before this fix)


# Same top-two tiers qualify for the frequency "high-value source" floor —
# same underlying judgment (official + established investigative sources
# deserve elevated baseline attention). Reuses _SOURCE_CREDIBILITY_TIERS
# rather than maintaining a second, driftable source list.
_HIGH_VALUE_SOURCES: frozenset = frozenset(
    _SOURCE_CREDIBILITY_TIERS[0][0] | _SOURCE_CREDIBILITY_TIERS[1][0]
)


def _infer_event_type(text: str) -> str:
    txt = text.lower()
    for event_type, keywords in EVENT_KEYWORDS.items():
        for kw in keywords:
            if kw in txt:
                return event_type
    return "unknown"


class SignalInterpreter:
    """Translate a raw signal into structured self-describing metadata."""

    def __init__(self) -> None:
        self.version = "1.0"

    def interpret(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        """Returns metadata map from a signal dict."""
        text = "".join(
            [
                str(signal.get("title", "")),
                " ",
                str(signal.get("content", "")),
            ]
        )
        encoded = signal.get("metadata_json")
        if not text and encoded:
            try:
                import json

                m = json.loads(encoded)
                text = " ".join(str(v) for v in m.values() if isinstance(v, str))
            except Exception:
                text = ""

        actors = _extract_actors(text)
        actor_types = _extract_actor_types(text)
        ev_type = _infer_event_type(text)

        # Phase 68: use detailed scoring to capture investigative uplift audit trail
        severity_detail = score_severity_detailed(text)
        severity = severity_detail["severity"]

        # Sprint 6 follow-up: sentiment was never computed before this --
        # see score_sentiment()'s docstring for why it's derived this way.
        sentiment = score_sentiment(text, severity)

        # actor_importance: more actors = higher importance, capped at 1.0
        actor_importance = min(len(actors) * 0.3, 1.0)

        # frequency: use is_priority flag and known high-value sources
        source = str(signal.get("source", ""))
        is_priority = int(signal.get("is_priority", 0) or 0)
        freq_base = 0.4 if source.strip().lower() in _HIGH_VALUE_SOURCES else 0.2
        frequency = min(freq_base + (0.3 * is_priority), 1.0)

        # source_credibility: see _SOURCE_CREDIBILITY_TIERS above
        source_credibility = _source_credibility(source)

        result = {
            "type": "event" if ev_type != "unknown" else "unknown",
            "actors": actors,
            "actor_types": actor_types,
            "event_type": ev_type,
            "severity": round(severity, 2),
            "sentiment": round(sentiment, 2),
            "actor_importance": round(actor_importance, 2),
            "frequency": round(frequency, 2),
            "source_credibility": round(source_credibility, 2),
            "raw_signal_id": signal.get("signal_id"),
        }
        # Phase 68: propagate investigative uplift metadata for conclave_meta audit
        if severity_detail["investigative_tier"]:
            result["investigative_uplift"]      = severity_detail["investigative_uplift"]
            result["investigative_tier"]         = severity_detail["investigative_tier"]
            result["matched_inv_keywords"]       = severity_detail["matched_inv_keywords"]
        return result

    def batch_interpret(self, signals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [self.interpret(s) for s in signals]