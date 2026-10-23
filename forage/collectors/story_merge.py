"""
story_merge.py — one story, one record (Batch 5 of the collector fixes, gap G3).

The four town sweeps saved one story many times: the R524 crash as 11
records, Carrim six times across three streams, the airport officers in
three streams. This module gives every saved signal, in every stream and
from both collectors, a story check:

* a **story key** built from the normalised headline, its main names, the
  place and a date window;
* when a new record tells the same story as an earlier one, one record is
  the **main record** and the others are marked **supporting evidence** that
  point to it (``story_role`` / ``supports_signal_id``). Nothing is deleted
  and no record's stream column is rewritten; the main record is the one
  whose stream has the strongest evidence (a classifier verdict beats a
  search-word guess, which beats the default);
* a check of every new record against the published pins and the held cases
  in ``docs/signals/*.json`` (``repeats_pin`` / ``matches_held``), so a
  draft does not repeat a pin or a case the owner already held.

How two records are judged the same story (``same_story``):

* the same article link, or
* headlines that share enough weighted words (Dice score over the headline
  words, rare names weigh most, common news words and place names little),
  including at least one specific word that is not a place or a common news
  word, dated within 3 days of a record already in the story (7 days when
  the shared names are rare and the score is high), and
* the places do not conflict (same place, one unplaced, or within 25 km;
  never different provinces), and two Infrastructure reports of different
  failure types are never one story.
* Infrastructure keeps its own rule as well: the same failure type at the
  same place within 3 days, or 21 days for an arrest, a restoration or a
  report of an ongoing outage. A town search's story that names no place is
  treated as that town's for this rule only (not as a place estimate).
* **A name is not a story.** Two headlines that share only a name (at most
  two specific words, no road number or rand amount, no place) are one story
  only when they are dated the same day, and such a match never joins two
  stories together. A person in the news for two cases (Carrim's Madlanga
  Commission arrest and his Absa vehicle case) stays two stories.
* **A killing is never demoted.** A killing (``crime_kind`` killing, or the
  crime classifier reads one in the headline) is never the same story as a
  report with no violence or death in its words (Padayachee's murder is not
  part of Feroz Khan's commission testimony), never joins a story holding
  such a report, and is always the main record of its own story. It can only
  support another report of the same killing when that one is also a killing
  and was chosen main first.

A record joins at most one story, the one it matches best; two stories are
never joined because one later record matches both. This leans towards
leaving two records apart rather than merging two different events.

What this does not do: it reads only the saved headline (and the summary for
pins), so it cannot tell two stories apart when their headlines say the
same generic thing; it does not change any stream, place or date; and review
views do not yet collapse supporting records.
"""

from __future__ import annotations

import glob
import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

REPO = Path(__file__).resolve().parents[2]
PINS_GLOB = str(REPO / "docs" / "signals" / "*.json")

WINDOW_DAYS = 3          # a report within 3 days of a record in the story
LONG_WINDOW_DAYS = 7     # rare shared names and a high score
INFRA_FOLLOW_DAYS = 21   # arrest / restoration / ongoing outage (as infra_signals)
SCORE_MIN = 0.45
SCORE_LONG_MIN = 0.7
SCORE_PLACE_MIN = 0.5
SCORE_SAME_PLACE_MIN = 0.33   # both records placed at the same place, within 2 days
PLACE_KM = 25.0
# A road number (R524, N1) or a rand amount (R1.5m) shared by two headlines
# is a strong sign of one story, so less of the rest has to match.
ANCHOR = re.compile(r"[rn]\d[\d.]*(?:m|bn|k)?")
ANCHOR_MIN = 0.25
ANCHOR_LONG_MIN = 0.35

STOP = set("""
a an the and or but of in on at to for from by with without as is are was were be been being has have had
it its this that these those he she they them his her their our we you i not no yes into onto over under after
before during while about against between among up down out off than then so such very can could will would
should may might must do does did done just also only more most less new old now still how why what when where
who whom which there here amid via per vs says said say tell tells told get gets got make makes made one two
days day week weeks month year years today yesterday tonight morning night weekend monday tuesday wednesday
thursday friday saturday sunday october september november watch listen video recorded live update updates
report reports reported news latest breaking near following follows lost amid
""".split())

# Common news words: they count, but little.
GENERIC = set("""
police cop saps officer officers member members man men woman women boy girl child children person people
resident residents community communities family suspect suspected alleged allegedly accused arrest arrested
arrests court case cases charge charged charges appear appears appearance bail hearing trial judge magistrate
dead death deaths die died killed kill killing murder murdered shot shooting body bodies found missing injured
hurt attack attacked crime crimes criminal violence incident incidents investigation investigate probe
municipality municipal mayor council councillor government minister mec department province provincial
local national south africa african sa limpopo gauteng kwazulu natal kzn vhembe capricorn district town city
village area areas ward street road roads home house school learners water power electricity outage outages
cut cuts supply crisis service services eskom crash accident fire business businessman businesswoman owner
worker workers year-old old time times call calls demand demands action plan plans million billion rand
first second three four five six seven eight nine ten several many group groups unit task team general
high low major big top amid face faces facing back again more fresh additional added
""".split())

# Words that say what kind of event it is, not which one: two stories that
# share only these ("illegal mining", "CIT robbers") are not one story.
TOPIC = set("""
illegal mining miner zama cit cash transit heist robbery robber robbed rob shootout gun gunman gunmen gunned
battle hijack hijacking hijacked drug drugs dagga cocaine mandrax seizure seized seize bust busted raid raided
operation operations syndicate syndicates gang gangs gangster tavern protest protester protesters strike march
commission inquiry testimony testify hearing sentenced sentence sentencing convicted conviction life
prison jail jailed behind bars weapon weapons firearm firearms ammunition explosive explosives rape raped
assault assaulted kidnapping kidnapped abduction abducted mob justice vigilante stabbed stabbing hit
fraud corruption corrupt bribe bribery theft stolen steal stealing smuggle trafficking trafficker human
undocumented immigrant immigrants foreign national nationals deport deported deportation border
crash dead injured hospital illness learner flood floods flooding storm fire fires blaze burnt burned
pipe burst leak sewage sewer reservoir taps load shedding loadshedding substation cable cables transformer
vandalism vandalised outage interruption maintenance pothole potholes
whistleblower scandal probe probed investigation allegation allegations claim claims
tender tenders contract contracts official officials employee employees staff manager director ceo
husband wife son daughter mother father brother sister uncle aunt nephew niece teen teenager toddler baby
farmer farm mine mines church pastor taxi taxis bus truck trucks driver drivers
postponed postpone postponement delayed delay adjourned remanded custody bid appear appeared appearance
state opposes oppose opposed denied granted released freed flight risk arrest arrested legal woe woes
deepen deepens continue continues dramatic stay stays remain remains expected
""".split())

OUTLET_TAIL = re.compile(r"\s+[-|–—]\s+[^-|–—]{2,60}$")
PREFIX = re.compile(r"^\s*(?:recorded|watch|listen|video|live|updated?|breaking|opinion|editorial|just in|exclusive)\s*[|:]\s*", re.I)
MONEY = re.compile(r"\br\s?(\d+(?:[.,]\d+)?)\s?(million|mil|m|billion|bn|b|k|thousand)?\b")
TOKEN = re.compile(r"r\d+(?:\.\d+)?(?:m|bn|k)?|[a-z][a-z'\-]*[a-z]|\d{2,}")

STREAM_ORDER = {"PRIORITY": 0, "CRIME_INTEL": 1, "INFRASTRUCTURE": 2, "GLOBAL": 3}


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(ch for ch in s if not unicodedata.combining(ch)).lower()


def normalise_headline(title: str) -> str:
    """Headline without the outlet tail (' - TimesLIVE') or a 'RECORDED |' prefix."""
    t = (title or "").strip()
    t = PREFIX.sub("", t)
    t = OUTLET_TAIL.sub("", t)
    t = t.replace("\u2018", "'").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
    return t.strip()


def _money(m: re.Match) -> str:
    num = m.group(1).replace(",", ".")
    unit = (m.group(2) or "").lower()
    try:
        v = float(num)
    except ValueError:
        return m.group(0)
    if unit in ("million", "mil", "m"):
        return f" r{v:g}m "
    if unit in ("billion", "bn", "b"):
        return f" r{v:g}bn "
    if unit in ("k", "thousand"):
        return f" r{v:g}k "
    return f" r{m.group(1).replace(',', '').replace(' ', '')} "


# Words the same story is told with (car / vehicle, smuggle / smuggling).
SYNONYMS = {
    "car": "vehicle", "cars": "vehicle", "vehicles": "vehicle", "bakkie": "vehicle",
    "smuggling": "smuggle", "smuggled": "smuggle", "smuggler": "smuggle", "smugglers": "smuggle",
    "cops": "police", "cop": "police", "policeman": "police", "policemen": "police",
    "cigarettes": "cigarette", "tobacco": "cigarette",
    "killed": "dead", "kill": "dead", "die": "dead", "died": "dead", "death": "dead",
    "hospitalised": "hospital", "hospitalized": "hospital",
    "ill": "illness", "sick": "illness",
    "collision": "crash", "collisions": "crash", "collides": "crash", "collided": "crash",
    "accident": "crash", "accidents": "crash", "head-on": "crash", "pile-up": "crash", "crashes": "crash",
    "officer": "police", "officers": "police",
    "saps": "police", "sap": "police", "lives": "dead", "dies": "dead", "fatal": "dead",
    "learners": "learner", "pupils": "learner", "pupil": "learner", "students": "learner",
}


def _stem(w: str) -> str:
    if w.endswith("'s"):
        w = w[:-2]
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss") and not w.endswith("us"):
        return w[:-1]
    return w


def headline_tokens(title: str) -> list[str]:
    t = _fold(normalise_headline(title))
    t = re.sub(r"(\d) (\d{3})\b", r"\1\2", t)       # R100 000 -> R100000
    t = MONEY.sub(_money, t)
    out = []
    for raw in TOKEN.findall(t):
        raw = raw.strip("'-")
        parts = [SYNONYMS[raw]] if raw in SYNONYMS else raw.split("-")   # vehicle-theft -> vehicle, theft
        for w in parts:
            if len(w) < 2 or w in STOP:
                continue
            w = SYNONYMS.get(w, w)
            w = _stem(w)
            w = SYNONYMS.get(w, w)
            if w in STOP:
                continue
            out.append(w)
    seen, uniq = set(), []
    for w in out:
        if w not in seen:
            seen.add(w)
            uniq.append(w)
    return uniq


@lru_cache(maxsize=1)
def _place_words() -> frozenset:
    try:
        from forage.collectors import infra_signals as _infra
        names = _infra._places().keys()
    except Exception:
        names = []
    # Single-word place names only: the words of "Polokwane International
    # Airport" or "Cape Town" are not place names on their own.
    words = set()
    for n in names:
        if re.fullmatch(r"[a-z]{3,}", n) and n not in ("hope",):
            words.add(n)
    return frozenset(words)


def _norm_word(w: str) -> str:
    w = SYNONYMS.get(w, w)
    w = _stem(w)
    return SYNONYMS.get(w, w)


@lru_cache(maxsize=1)
def _classes() -> tuple:
    """GENERIC and TOPIC as they look after headline_tokens ("bars" -> "bar")."""
    gen = frozenset(GENERIC | {_norm_word(w) for w in GENERIC})
    top = frozenset(TOPIC | {_norm_word(w) for w in TOPIC}) - gen
    return gen, top


def word_class(w: str) -> str:
    gen, top = _classes()
    if w in gen:
        return "generic"
    if w in top:
        return "topic"
    if w in _place_words():
        return "place"
    return "specific"


def _date(ts) -> Optional[date]:
    if not ts:
        return None
    if isinstance(ts, date):
        return ts
    try:
        return datetime.fromisoformat(str(ts)[:10]).date()
    except ValueError:
        return None


def _km(a, b) -> Optional[float]:
    if None in (a[0], a[1], b[0], b[1]):
        return None
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def stream_strength(stream: str, reason: str) -> int:
    """3: a classifier verdict (PI rules, crime classifier, infrastructure
    words); 2: a stream from search or keyword context; 1: the default."""
    r = (reason or "").lower()
    if r.startswith(("pi:", "crime:")) or "words in title" in r or r.startswith("infra"):
        return 3
    if r in ("general_media", "") and (stream or "GLOBAL") == "GLOBAL":
        return 1
    return 2


ONGOING = re.compile(r"\b(ongoing|continues|continuing|still without|\d+[- ]day|days without|weeks without|crisis|struggl\w*|dry taps|march\w* over|protest\w* over)\b", re.I)


@dataclass
class StoryRecord:
    rid: str
    title: str
    date: Optional[date]
    stream: str = "GLOBAL"
    stream_reason: str = ""
    place: Optional[str] = None
    province: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    link: Optional[str] = None
    infra_type: Optional[str] = None
    infra_kind: Optional[str] = None
    town_hint: Optional[str] = None   # the town a town search was for, when the story names no place
    crime_kind: Optional[str] = None  # the crime classifier's kind from the collector, when it ran
    order: int = 0
    tokens: list = field(default_factory=list)
    # filled in by StoryIndex
    story: Optional[int] = None

    def __post_init__(self):
        if not self.tokens:
            self.tokens = headline_tokens(self.title)

    @property
    def strength(self) -> int:
        return stream_strength(self.stream, self.stream_reason)

    @property
    def merge_place(self) -> Optional[str]:
        return self.place or self.town_hint


# ── killings ────────────────────────────────────────────────────────────────

def is_killing(rec: StoryRecord) -> bool:
    """A report of a killing: the collector's crime classifier said so, or the
    classifier reads one in the headline. A road crash is not a killing."""
    if rec.crime_kind:
        return rec.crime_kind == "killing"
    return _classify_kind(rec.title or "") == "killing"


@lru_cache(maxsize=8192)
def _classify_kind(title: str) -> Optional[str]:
    from forage.collectors import crime_signals as _crime
    return _crime.classify(title).get("kind")


@lru_cache(maxsize=8192)
def _violent_words(title: str) -> bool:
    from forage.collectors import crime_signals as _crime
    t = title or ""
    return bool(_crime._KILLING.search(t) or _crime._DEATH.search(t) or _crime._VIOLENCE.search(t)
                or _crime._BODY_FOUND.search(t) or _crime._ATTEMPTED.search(t))


def has_violence(rec: StoryRecord) -> bool:
    """The headline speaks of a death, a killing or an attack at all."""
    return is_killing(rec) or _violent_words(rec.title or "")


def killing_conflict(a: StoryRecord, b: StoryRecord) -> bool:
    """One is a killing and the other says nothing of violence or death: a
    commission hearing, a raid, a bail bid. They are never one story, so the
    killing can never become supporting evidence of the other."""
    return (is_killing(a) and not has_violence(b)) or (is_killing(b) and not has_violence(a))


def fits_story(rec: StoryRecord, members: Iterable[StoryRecord]) -> bool:
    """``rec`` can join a story only when it has no killing conflict with any
    of its records (a story never holds a killing and a non-violent report)."""
    return not any(killing_conflict(rec, m) for m in members)


def _text_places(tokens: list) -> list:
    from forage.collectors import infra_signals as _infra
    out = []
    for w in tokens:
        if word_class(w) == "place":
            hits = _infra._places().get(w) or []
            if hits:
                out.append((hits[0]["lat"], hits[0]["lng"]))
    return out


def text_places_conflict(a: list, b: list) -> bool:
    """Both headlines name places, and every pair is more than 40 km apart."""
    pa, pb = _text_places(a), _text_places(b)
    if not pa or not pb:
        return False
    return all((_km(x, y) or 0) > 40 for x in pa for y in pb)


def _shares_word(a: list, b: list) -> bool:
    """A shared word that is not a common news word: a name, a place or a
    word for the kind of event."""
    return any(word_class(w) != "generic" for w in set(a) & set(b))


def places_compatible(a: StoryRecord, b: StoryRecord) -> bool:
    """A place conflict needs two named places: a province guess alone
    ("North West businessman") does not stop a match."""
    if not a.place or not b.place:
        return True
    if a.province and b.province and _fold(a.province) != _fold(b.province):
        return False
    if _fold(a.place) == _fold(b.place):
        return True
    d = _km((a.lat, a.lng), (b.lat, b.lng))
    return d is not None and d <= PLACE_KM


class Weights:
    """Word weights from how often a word appears in the pool of headlines.

    ``base``: (n, df) from a larger pool, added to the counts (the bench uses
    the word counts of all saved sweep rows, as a live database would)."""

    def __init__(self, token_lists: Iterable[list], base: Optional[tuple] = None):
        self.df: dict[str, int] = dict(base[1]) if base else {}
        self.n = base[0] if base else 0
        for toks in token_lists:
            self.n += 1
            for w in set(toks):
                self.df[w] = self.df.get(w, 0) + 1

    def add(self, toks: list) -> None:
        self.n += 1
        for w in set(toks):
            self.df[w] = self.df.get(w, 0) + 1

    def idf(self, w: str) -> float:
        return math.log((self.n + 1) / (self.df.get(w, 0) + 1)) + 1.0

    def weight(self, w: str) -> float:
        c = word_class(w)
        x = self.idf(w)
        return x * {"generic": 0.25, "topic": 0.5, "place": 0.5}.get(c, 1.0)

    def rare(self, w: str) -> bool:
        return word_class(w) == "specific" and self.df.get(w, 0) <= max(3, 0.005 * self.n)


def _near(x: str, y: str) -> bool:
    """One letter apart, for names of six letters or more (Sulliman / Suliman)."""
    if abs(len(x) - len(y)) > 1 or min(len(x), len(y)) < 6 or x[0] != y[0]:
        return False
    if len(x) == len(y):
        return sum(c1 != c2 for c1, c2 in zip(x, y)) == 1
    s, l = (x, y) if len(x) < len(y) else (y, x)
    return any(l[:i] + l[i + 1:] == s for i in range(len(l)))


def _align(sa: set, sb: set) -> set:
    """sb with a specific word spelt one letter differently from a word in sa
    replaced by sa's spelling."""
    out = set(sb)
    for w in sb - sa:
        if word_class(w) != "specific":
            continue
        for v in sa - sb:
            if word_class(v) == "specific" and _near(w, v):
                out.discard(w)
                out.add(v)
                break
    return out


def headline_score(a: list, b: list, wts: Weights) -> tuple[float, list]:
    sa = set(a)
    sb = _align(sa, set(b))
    shared = sa & sb
    if not shared:
        return 0.0, []
    wa = sum(wts.weight(w) for w in sa)
    wb = sum(wts.weight(w) for w in sb)
    ws = sum(wts.weight(w) for w in shared)
    return (2 * ws / (wa + wb) if wa + wb else 0.0), sorted(shared)


def _norm_link(u: Optional[str]) -> Optional[str]:
    if not u:
        return None
    u = u.strip().lower().split("#")[0]
    if "news.google.com" in u:
        return u.split("?")[0]
    u = re.sub(r"^https?://(www\.)?", "", u).split("?")[0].rstrip("/")
    return u


def same_story(a: StoryRecord, b: StoryRecord, wts: Weights) -> tuple[float, str]:
    """(score, reason) when a and b tell the same story, else (0, why not)."""
    la, lb = _norm_link(a.link), _norm_link(b.link)
    if la and la == lb:
        return 1.0, "same link"
    if killing_conflict(a, b):
        return 0.0, "a killing and a report with no violence"
    if not places_compatible(a, b):
        return 0.0, "places conflict"
    if a.infra_type and b.infra_type and a.infra_type != b.infra_type:
        return 0.0, "different infrastructure failures"
    if a.date is None or b.date is None:
        return 0.0, "no date"
    gap = abs((a.date - b.date).days)
    # Infrastructure: same failure at the same place (the event_key rule, widened).
    if a.infra_type and a.infra_type == b.infra_type and a.merge_place and b.merge_place \
            and _fold(a.merge_place) == _fold(b.merge_place) and (a.place or b.place) \
            and (a.place and b.place or _shares_word(a.tokens, b.tokens)):
        # The same failure at the same place. A town search's unplaced story
        # counts as that town's only when its headline shares a word with the
        # placed one, so a foreign "power cut" is not tied to Newcastle.
        later = a if a.date >= b.date else b
        follow = later.infra_kind in ("arrest", "restored") or bool(ONGOING.search(later.title or ""))
        if gap <= WINDOW_DAYS or (follow and gap <= INFRA_FOLLOW_DAYS):
            return 0.9, f"same {a.infra_type} at {a.merge_place}"
    if (is_thin(a) or is_thin(b)) and gap > 1:
        # "Suliman Carrim back in court" says only who: it is matched to
        # reports from the same or the next day, never used to bridge days.
        return 0.0, "name-only headline, more than a day apart"
    score, shared = headline_score(a.tokens, b.tokens, wts)
    specific = [w for w in shared if word_class(w) == "specific"]
    anchor = [w for w in specific if ANCHOR.fullmatch(w)]
    if text_places_conflict(a.tokens, b.tokens):
        return 0.0, "headlines name places far apart"
    name_only = bool(specific) and len(specific) <= 2 and not anchor \
        and not any(word_class(w) == "place" for w in shared)
    if name_only and gap > 0:
        # "Suliman Carrim's bail bid postponed" and "Suliman Carrim's
        # vehicle-theft case postponed": the same man, two cases.
        return 0.0, "only a name shared, on different days"
    if name_only:
        if score >= SCORE_MIN:
            return score, "headline (name only): " + ", ".join(specific)
        return 0.0, f"only a name shared, score {score:.2f}"
    if not specific:
        places = [w for w in shared if word_class(w) == "place"]
        # Same named place, same kind of event, within two days.
        if places and gap <= 2 and score >= SCORE_PLACE_MIN:
            return score, "headline (same place): " + ", ".join(sorted(shared)[:6])
        return 0.0, "no shared name"
    if gap <= WINDOW_DAYS and (score >= SCORE_MIN or (anchor and score >= ANCHOR_MIN)):
        return score, "headline: " + ", ".join(specific[:6])
    same_place = a.place and b.place and _fold(a.place) == _fold(b.place)
    if same_place and gap <= 2 and score >= SCORE_SAME_PLACE_MIN:
        return score, f"headline at {a.place}: " + ", ".join(specific[:6])
    rare = [w for w in specific if wts.rare(w)]
    if gap <= LONG_WINDOW_DAYS and ((score >= SCORE_LONG_MIN and len(rare) >= 2)
                                    or (anchor and score >= ANCHOR_LONG_MIN)):
        return score, "headline (rare names): " + ", ".join(specific[:6])
    return 0.0, f"score {score:.2f}, {gap} days apart"


def choose_main(members: list[StoryRecord]) -> StoryRecord:
    """The record whose stream has the strongest evidence; then the more
    specific stream; then one with a place; then the earliest report.
    A killing always comes first: it is never demoted to supporting evidence."""
    return sorted(members, key=lambda r: (0 if is_killing(r) else 1, -r.strength,
                                          STREAM_ORDER.get(r.stream or "GLOBAL", 9),
                                          0 if r.place else 1, r.date or date.max, r.order))[0]


def story_key(main: StoryRecord, wts: Optional[Weights] = None) -> str:
    """The story's key, from its main record: the headline's main names, the
    place and the date, plus the normalised headline so two different
    stories with the same names, place and day do not share a key."""
    names = sorted(w for w in main.tokens if word_class(w) == "specific")[:6]
    d = main.date.isoformat() if main.date else ""
    place = _fold(main.merge_place or "")
    raw = "|".join([" ".join(names), place, d, " ".join(main.tokens)])
    return "story:" + hashlib.sha1(raw.encode()).hexdigest()[:12]


def is_thin(rec: StoryRecord) -> bool:
    """A headline that carries little more than a name ("Suliman Carrim back
    in court"): it may join a story but never joins two stories together."""
    if any(ANCHOR.fullmatch(w) for w in rec.tokens):
        return False
    return sum(1 for w in rec.tokens if word_class(w) in ("specific", "topic")) <= 2


@dataclass
class Match:
    score: float
    why: str
    story: object
    member: str
    thin_member: bool = False

    @property
    def short(self) -> bool:
        return not self.why.startswith("headline (rare names)")

    @property
    def bridges(self) -> bool:
        """Strong enough to join two stories: not a long-window match and
        not a match on a name alone."""
        return self.short and not self.why.startswith("headline (name only)")


def match_stories(rec: StoryRecord, stories: dict, wts: Weights,
                  only: Optional[set] = None) -> list[Match]:
    """The best match in each story that ``rec`` tells the same story as,
    best first. ``stories``: story id -> list of StoryRecord; ``only``: the
    story ids worth comparing (a shared word, link or failure type)."""
    out = []
    for sid, members in stories.items():
        if only is not None and sid not in only:
            continue
        if not fits_story(rec, members):
            continue
        best = None
        for m in members:
            sc, why = same_story(rec, m, wts)
            if sc > 0 and (best is None or sc > best.score):
                best = Match(sc, why, sid, m.rid, is_thin(m))
        if best:
            out.append(best)
    return sorted(out, key=lambda m: -m.score)


def plan_join(rec: StoryRecord, matches: list[Match]) -> tuple[Optional[Match], list[Match]]:
    """(story to join, other stories to join to it). A record joins the story
    it matches best. It joins two stories together only when it is not a
    thin headline and matches each within the short window (or by link or
    the Infrastructure rule) — a story split only because its reports came
    in an order that hid the link between them."""
    if not matches:
        return None, []
    first = matches[0]
    if is_thin(rec) or not first.bridges:
        return first, []
    return first, [m for m in matches[1:] if m.bridges and m.score >= SCORE_MIN and not m.thin_member]


class StoryIndex:
    """Records in the order they were saved; each joins the story it matches
    best, or starts a new one (see ``plan_join``)."""

    def __init__(self, pool_tokens: Optional[Iterable[list]] = None, base: Optional[tuple] = None):
        self.records: list[StoryRecord] = []
        self.stories: dict[int, list[StoryRecord]] = {}
        self.links: dict[str, tuple[float, str, str]] = {}  # rid -> (score, reason, matched rid)
        self.unions: list[tuple[str, int, int]] = []        # (rid that joined them, kept, folded in)
        self.wts = Weights(pool_tokens or [], base)
        self._fixed_pool = pool_tokens is not None or base is not None
        self._next = 0
        self._by_key: dict[str, set] = {}   # word / link / failure type -> story ids

    @staticmethod
    def _keys(rec: StoryRecord) -> list[str]:
        keys = ["w:" + w for w in rec.tokens if word_class(w) == "specific"]
        if _norm_link(rec.link):
            keys.append("l:" + _norm_link(rec.link))
        if rec.infra_type:
            keys.append("i:" + rec.infra_type)
        return keys

    def _candidates(self, rec: StoryRecord) -> set:
        out = set()
        for k in self._keys(rec):
            out |= self._by_key.get(k, set())
        return out

    def _index(self, rec: StoryRecord, sid: int) -> None:
        for k in self._keys(rec):
            self._by_key.setdefault(k, set()).add(sid)

    def add(self, rec: StoryRecord) -> int:
        rec.order = len(self.records)
        if not self._fixed_pool:
            self.wts.add(rec.tokens)
        join, fold = plan_join(rec, match_stories(rec, self.stories, self.wts, self._candidates(rec)))
        if join is None:
            sid = self._next
            self._next += 1
            self.stories[sid] = [rec]
        else:
            sid = join.story
            self.stories[sid].append(rec)
            self.links[rec.rid] = (round(join.score, 3), join.why, join.member)
            kept = list(self.stories[sid])
            for m in fold:
                if not all(fits_story(r, kept) for r in self.stories[m.story]):
                    continue   # never fold a killing in with a non-violent report
                kept.extend(self.stories[m.story])
                for r in self.stories.pop(m.story):
                    r.story = sid
                    self.stories[sid].append(r)
                    self._index(r, sid)
                self.unions.append((rec.rid, sid, m.story))
        rec.story = sid
        self._index(rec, sid)
        self.records.append(rec)
        return sid

    def main_of(self, sid: int) -> StoryRecord:
        return choose_main(self.stories[sid])

    def roles(self) -> dict[str, dict]:
        """rid -> {story_key, story_role, supports, story_size, story_match}."""
        out = {}
        for sid, members in self.stories.items():
            main = choose_main(members)
            key = story_key(main)
            for m in members:
                d = {"story_key": key, "story_size": len(members), "story_main": main.rid,
                     "story_role": "main" if m is main else "supporting_evidence"}
                if m is not main:
                    d["supports"] = main.rid
                if m.rid in self.links:
                    s, why, other = self.links[m.rid]
                    d["story_match"] = {"score": s, "reason": why, "matched": other}
                out[m.rid] = d
        return out


# ── published pins and held cases ───────────────────────────────────────────

@dataclass
class PinRef:
    ref: str
    kind: str              # "pin" | "held"
    text_tokens: list
    title_tokens: list
    links: set
    lat: Optional[float] = None
    lng: Optional[float] = None
    start: Optional[date] = None
    end: Optional[date] = None
    label: str = ""


def _tokens_text(s: str) -> list[str]:
    return headline_tokens(re.sub(r"[\n.;:()]", " ", s or ""))


@lru_cache(maxsize=4)
def load_pins(pattern: str = PINS_GLOB) -> tuple:
    refs: list[PinRef] = []
    for f in sorted(glob.glob(pattern)):
        try:
            d = json.loads(Path(f).read_text())
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        for s in d.get("signals", []) or []:
            if not isinstance(s, dict):
                continue
            m = s.get("metadata") or {}
            ck = m.get("checked") or {}
            links = {_norm_link(m.get("link"))}
            for src in ck.get("sources", []) or []:
                if isinstance(src, dict):
                    links.add(_norm_link(src.get("url")))
            links.discard(None)
            start = _date(ck.get("incident_start") or s.get("timestamp"))
            end = _date(ck.get("reported_at") or s.get("published_at") or s.get("timestamp"))
            ttl = m.get("collected_title") or ""
            refs.append(PinRef(
                ref=s.get("external_id") or s.get("signal_id") or "", kind="pin",
                text_tokens=_tokens_text(f"{s.get('title', '')} {ttl} {' '.join(ck.get('search_aliases', []) or [])}"),
                title_tokens=headline_tokens(ttl) if ttl else [],
                links=links, lat=s.get("lat"), lng=s.get("lng"), start=start, end=end,
                label=(s.get("title") or "")[:120]))
        for i, h in enumerate(d.get("held_back", []) or []):
            if not isinstance(h, dict):
                continue
            links = {_norm_link(h.get("source"))} if str(h.get("source", "")).startswith("http") else set()
            links.discard(None)
            refs.append(PinRef(ref=f"{Path(f).stem}#held{i}", kind="held",
                               text_tokens=_tokens_text(f"{h.get('place', '')} {h.get('source', '')}"),
                               title_tokens=[], links=links, label=(h.get("place") or "")[:120]))
    return tuple(refs)


def check_pins(rec: StoryRecord, wts: Weights, pins: Optional[tuple] = None) -> dict:
    """{'repeats_pin': {...}} and/or {'matches_held': {...}} when the record
    tells a story already published as a pin or already held."""
    pins = load_pins() if pins is None else pins
    out: dict = {}
    link = _norm_link(rec.link)
    toks = set(rec.tokens)
    spec = {w for w in toks if word_class(w) == "specific"}
    if not spec:
        return out
    for p in pins:
        key = "repeats_pin" if p.kind == "pin" else "matches_held"
        if key in out:
            continue
        if link and link in p.links:
            out[key] = {"ref": p.ref, "why": "same link", "label": p.label}
            continue
        if p.kind == "pin":
            if rec.date and p.start and p.end and not (
                    (p.start - _days(3)) <= rec.date <= (p.end + _days(14))):
                continue
            if rec.lat is not None and p.lat is not None:
                d = _km((rec.lat, rec.lng), (p.lat, p.lng))
                if d is not None and d > 60:
                    continue
            if p.title_tokens:
                s, shared = headline_score(rec.tokens, p.title_tokens, wts)
                if s >= SCORE_MIN and any(word_class(w) == "specific" for w in shared):
                    out[key] = {"ref": p.ref, "why": f"headline {s:.2f}", "label": p.label}
                    continue
        # coverage of the record's specific words by the pin / held text
        cover = spec & set(p.text_tokens)
        rare = [w for w in cover if wts.rare(w)]
        if len(cover) >= 2 and len(cover) / len(spec) >= 0.5 and rare:
            out[key] = {"ref": p.ref, "why": "names: " + ", ".join(sorted(cover)[:5]), "label": p.label}
    return out


def _days(n: int):
    from datetime import timedelta
    return timedelta(days=n)


# ── collector hook ──────────────────────────────────────────────────────────

DB_LOOKBACK_DAYS = 21
DB_POOL_LIMIT = 4000


def record_from_row(rid: str, title: str, timestamp: str, stream: str, meta: dict,
                    town_hint: Optional[str] = None) -> StoryRecord:
    lc = meta.get("location_candidate") or {}
    return StoryRecord(
        rid=rid, title=title or "", date=_date(timestamp), stream=stream or "GLOBAL",
        stream_reason=meta.get("stream_reason") or "", place=lc.get("place"), province=lc.get("province"),
        lat=lc.get("lat"), lng=lc.get("lng"), link=meta.get("link") or meta.get("url"),
        infra_type=meta.get("infra_event_type"), infra_kind=meta.get("infra_kind"),
        crime_kind=meta.get("crime_kind"),
        town_hint=(meta.get("story_town_hint") if not lc.get("place") else None) or (town_hint if not lc.get("place") else None))


def link_story(conn, signal_id: str, title: str, stream: str, timestamp: str, meta: dict,
               town_hint: Optional[str] = None) -> dict:
    """Collector hook: before a signal is saved, find whether an earlier live
    record (any stream, either collector, this run included) tells the same
    story, and whether a pin or held case already covers it. Returns the
    metadata to save. When the new record has stronger stream evidence than
    the story's main record, it becomes the main record and the others are
    re-pointed to it (their metadata only; no row is deleted)."""
    meta = dict(meta)
    if town_hint and not (meta.get("location_candidate") or {}).get("place"):
        meta["story_town_hint"] = town_hint
    rec = record_from_row(signal_id, title, timestamp, stream, meta, town_hint)
    rows = []
    try:
        rows = conn.execute(
            "SELECT signal_id, title, timestamp, stream, metadata_json FROM signals "
            "WHERE source_type='live' AND timestamp >= date(?, ?) AND timestamp <= date(?, ?) "
            "ORDER BY timestamp DESC LIMIT ?",
            (timestamp[:10], f"-{DB_LOOKBACK_DAYS} days", timestamp[:10], f"+{DB_LOOKBACK_DAYS} days",
             DB_POOL_LIMIT)).fetchall()
    except Exception:
        rows = []
    cands = []
    for sid, t, ts, st, mj in rows:
        try:
            m = json.loads(mj or "{}")
        except Exception:
            m = {}
        cands.append((record_from_row(sid, t, ts, st, m), m))
    wts = Weights([c.tokens for c, _ in cands] + [rec.tokens])
    # Only records sharing a specific word, the link or the failure type can
    # match (the same candidate rule as StoryIndex); this keeps a run of a
    # thousand saves from scoring every pair.
    rkeys = set(StoryIndex._keys(rec))
    scored = [(c, m) for c, m in cands if rkeys & set(StoryIndex._keys(c))]

    def _members(c, m):
        mid = (m.get("supports_signal_id") if m.get("story_role") == "supporting_evidence" else None) or c.rid
        return [x for x, xm in cands if x.rid == mid or xm.get("supports_signal_id") == mid]

    best = (0.0, None, "", None)
    for c, m in scored:
        s, why = same_story(rec, c, wts)
        if s > best[0] and fits_story(rec, _members(c, m)):
            best = (s, c, why, m)
    meta.update(check_pins(rec, wts))
    if best[1] is None:
        meta["story_key"] = story_key(rec)
        meta["story_role"] = "main"
        return meta
    other, om = best[1], best[3]
    main_id = om.get("supports_signal_id") if om.get("story_role") == "supporting_evidence" else other.rid
    main_id = main_id or other.rid
    members = [(c, m) for c, m in cands if c.rid == main_id or m.get("supports_signal_id") == main_id]
    main_rec = next((c for c, _ in members if c.rid == main_id), other)
    key = om.get("story_key") or story_key(main_rec)
    meta["story_key"] = key
    meta["story_match"] = {"score": round(best[0], 3), "reason": best[2], "matched": other.rid}
    if choose_main([main_rec, rec]) is rec:
        meta["story_role"] = "main"
        for c, m in members:
            m = dict(m)
            m["story_role"] = "supporting_evidence"
            m["supports_signal_id"] = signal_id
            m["story_key"] = key
            conn.execute("UPDATE signals SET metadata_json=? WHERE signal_id=?", (json.dumps(m), c.rid))
    else:
        meta["story_role"] = "supporting_evidence"
        meta["supports_signal_id"] = main_id
    return meta


def safe_link_story(conn, signal_id: str, title: str, stream: str, timestamp: str, meta: dict,
                    town_hint: Optional[str] = None) -> dict:
    """What the collectors call. A failure in the story check never stops a
    signal being saved: the record is saved as it was, marked unchecked.
    FORGE_STORY_MERGE=0 turns the check off."""
    import os
    if os.environ.get("FORGE_STORY_MERGE", "1") == "0":
        return meta
    try:
        return link_story(conn, signal_id, title, stream, timestamp or "", meta, town_hint)
    except Exception as exc:  # never block a save on the story check
        out = dict(meta)
        out["story_check_error"] = f"{type(exc).__name__}: {exc}"[:200]
        return out


def bench_roles(rows: list[dict], key: str = "outcome", base: Optional[tuple] = None) -> dict:
    """Bench hook: run the story check over replayed rows in saved order,
    one index per town sweep, and return row index -> role dict. Dropped
    rows are skipped (they are not records)."""
    by_town: dict[str, list[int]] = {}
    for i, r in enumerate(rows):
        if r[key].get("dropped"):
            continue
        by_town.setdefault(r["town"], []).append(i)
    out = {}
    for town, idxs in by_town.items():
        idx = StoryIndex(base=base)
        for i in idxs:
            r = rows[i]
            o = r[key]
            idx.add(StoryRecord(
                rid=str(i), title=r.get("title") or "", date=_date(o.get("date")),
                stream=o.get("stream") or "GLOBAL", stream_reason=o.get("stream_reason") or "",
                place=o.get("place"), province=o.get("province"), lat=o.get("lat"), lng=o.get("lng"),
                link=o.get("link"), infra_type=o.get("infra_event_type"), infra_kind=o.get("infra_kind"),
                crime_kind=o.get("crime_kind")))
        for rid, d in idx.roles().items():
            out[int(rid)] = d
    return out
