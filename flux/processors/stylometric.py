#!/usr/bin/env python3
from __future__ import annotations
"""
FORGE FLUX — Stylometric Fingerprint Engine  (Phase C + D)
═══════════════════════════════════════════════════════════
X-specific behavioural fingerprinting for SOCINT syndicate detection.

Zero external dependencies. Entire module runs on Python stdlib:
    re, collections, difflib, math, statistics, json

Architecture
────────────
  extract_fingerprint(text)               Build a feature vector from raw text.
  compare_fingerprints(fp_a, fp_b)        Pairwise resonance score [0.0–1.0].
  score_signal_against_corpus(text, corp) Actor-level corpus comparison (gated).
  update_actor_corpus(profile_json, text) Append a new text sample to corpus.
  _corpus_ready(corpus)                   Minimum viable corpus gate.

Resonance Formula (Phase D)
───────────────────────────
  R = (W_SIM   x sequence_similarity)    # difflib on normalized text bodies
    + (W_CASH  x cashtag_jaccard)        # $TICKER set overlap (Jaccard)
    + (W_EMOJI x emoji_bigram_cosine)    # ordered emoji-pair vector cosine
    + (W_CAPS  x caps_proximity)         # ALL-CAPS density alignment
    + (W_LEET  x leet_proximity)         # leetspeak substitution alignment

Weight constants are defined as module-level floats.
Recalibrate by changing ONLY those constants — no logic edits needed.

Corpus gate (_corpus_ready)
───────────────────────────
  Requires CORPUS_MIN_ITEMS  >= 7   tweets   AND
           CORPUS_MIN_CHARS  >= 2000 characters
  Single-post stylometry is noise. Gate enforced before any score is emitted.

X-specific features captured
─────────────────────────────
  Cashtags        $ZAR $JSE $GBPZAR — pump/dump syndicate fingerprint
  Emoji bigrams   Ordered pairs — covert positional signal patterns
  Leetspeak       0/o 1/i 3/e 4/a 5/s 7/t @/a — filter-evasion density
  Punctuation     ! !!! ?! density — emotional manipulation campaigns
  Thread markers  1/ 2/ 0/ ... n/n 🧵 — individual threading style
  ALL CAPS ratio  Per-word capitalisation density
  Hashtags        Extracted but stored only; not used in resonance formula
                  (too easily gamed; reserved for future topic clustering)
"""

import json
import math
import re
import statistics
from collections import Counter
from difflib import SequenceMatcher
from typing import Optional

# ── Weight constants ──────────────────────────────────────────────────────────
# Sum must equal 1.0. Adjust these to recalibrate without touching logic.

W_SIM   = 0.35   # difflib sequence similarity on normalized text
W_CASH  = 0.25   # cashtag Jaccard index
W_EMOJI = 0.20   # emoji bigram cosine similarity
W_CAPS  = 0.10   # ALL-CAPS density proximity
W_LEET  = 0.10   # leetspeak substitution density proximity

assert abs(W_SIM + W_CASH + W_EMOJI + W_CAPS + W_LEET - 1.0) < 1e-9, \
    "Stylometric weights must sum to 1.0"

# ── Corpus gate constants ─────────────────────────────────────────────────────

CORPUS_MIN_ITEMS = 7      # minimum number of text samples in corpus
CORPUS_MIN_CHARS = 2000   # minimum total character count across all samples

# ── Resonance threshold (emit only above this) ────────────────────────────────

RESONANCE_THRESHOLD = 0.65

# ── Leet substitution map ─────────────────────────────────────────────────────

_LEET_CHARS: frozenset = frozenset("013457@")

# ── Emoji detection ───────────────────────────────────────────────────────────
# Covers Miscellaneous Symbols, Dingbats, Emoticons, Supplemental Symbols,
# Enclosed Alphanumeric Supplement, and Transport/Map Symbols.

_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F5FF"   # Misc symbols & pictographs
    "\U0001F600-\U0001F64F"   # Emoticons
    "\U0001F680-\U0001F6FF"   # Transport & map
    "\U0001F700-\U0001F77F"   # Alchemical symbols
    "\U0001F780-\U0001F7FF"   # Geometric shapes extended
    "\U0001F800-\U0001F8FF"   # Supplemental arrows C
    "\U0001F900-\U0001F9FF"   # Supplemental symbols
    "\U0001FA00-\U0001FA6F"   # Chess symbols
    "\U0001FA70-\U0001FAFF"   # Symbols and pictographs extended A
    "\U00002600-\U000027BF"   # Misc symbols
    "\U0001F1E0-\U0001F1FF"   # Flags (regional indicators)
    "]",                       # NO + quantifier — one codepoint per match
    flags=re.UNICODE,          # so consecutive emojis produce separate items
)

# ── Regex extractors ──────────────────────────────────────────────────────────

_CASHTAG_RE     = re.compile(r'\$[A-Z]{1,6}\b')
_HASHTAG_RE     = re.compile(r'#\w+')
_THREAD_RE      = re.compile(r'\b\d{1,2}\/\d{0,2}\b|\b\d{1,2}\.\s|\U0001F9F5')
_PUNCT_BANG_RE  = re.compile(r'!{3,}')
_PUNCT_INTERRO  = re.compile(r'\?!')
_URL_RE         = re.compile(r'https?://\S+|www\.\S+', re.IGNORECASE)
_MENTION_RE     = re.compile(r'@\w+')


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _normalise_text(text: str) -> str:
    """
    Strip URLs, @mentions, leading/trailing whitespace, and collapse runs of
    whitespace. Preserve cashtags, hashtags, emojis, and leetspeak — these
    ARE part of the stylometric signal.
    """
    t = _URL_RE.sub(" ", text)
    t = _MENTION_RE.sub(" ", t)
    t = re.sub(r"[ \t]{2,}", " ", t).strip()
    return t


def _extract_emojis(text: str) -> list[str]:
    """Return ordered list of all emoji characters found in text."""
    return _EMOJI_RE.findall(text)


def _emoji_bigrams(emojis: list[str]) -> Counter:
    """
    Build a Counter of adjacent emoji pairs.
    ('🔥', '💰') and ('💰', '🔥') are treated as distinct bigrams —
    position is part of the stylometric signature.
    """
    if len(emojis) < 2:
        return Counter()
    return Counter(
        (emojis[i], emojis[i + 1]) for i in range(len(emojis) - 1)
    )


def _cosine(a: Counter, b: Counter) -> float:
    """
    Cosine similarity between two Counter vectors.
    Returns 1.0 if both Counters are empty (identical absence = similarity).
    Returns 0.0 if exactly one is empty.
    """
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    keys = set(a) | set(b)
    dot  = sum(a.get(k, 0) * b.get(k, 0) for k in keys)
    mag_a = math.sqrt(sum(v ** 2 for v in a.values()))
    mag_b = math.sqrt(sum(v ** 2 for v in b.values()))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _jaccard(set_a: set, set_b: set) -> float:
    """
    Jaccard index for two sets.
    Both empty → 1.0 (shared absence is shared trait).
    One empty   → 0.0 (asymmetry is divergence).
    """
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def _caps_ratio(text: str) -> float:
    """Fraction of words that are entirely uppercase (length > 1)."""
    words = text.split()
    if not words:
        return 0.0
    return sum(1 for w in words if w.isupper() and len(w) > 1) / len(words)


def _leet_density(text: str) -> float:
    """
    Ratio of leet-substitution characters to total characters.
    Characters counted: 0 1 3 4 5 7 @
    """
    if not text:
        return 0.0
    return sum(1 for c in text if c in _LEET_CHARS) / len(text)


def _aggression_score(text: str) -> float:
    """
    Punctuation aggression: density of !, !!!, and ?! patterns.
    Normalised to text length to allow cross-post comparison.
    """
    if not text:
        return 0.0
    bangs      = text.count("!")
    triple_bang = len(_PUNCT_BANG_RE.findall(text))
    interro    = len(_PUNCT_INTERRO.findall(text))
    raw = bangs + (triple_bang * 2) + interro
    return raw / len(text)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def _corpus_ready(corpus: list[str]) -> bool:
    """
    Gate: returns True only when the actor's corpus is large enough for
    reliable stylometric analysis.

    Requirements (both must pass):
        >= CORPUS_MIN_ITEMS  text samples
        >= CORPUS_MIN_CHARS  total characters across all samples

    Rationale: a 280-char X post contains ~7-10 stylometric signals.
    Fewer than 7 posts = insufficient statistical base. Fewer than 2000
    chars = high false-positive risk on SequenceMatcher comparison.
    """
    if len(corpus) < CORPUS_MIN_ITEMS:
        return False
    return sum(len(t) for t in corpus) >= CORPUS_MIN_CHARS


def extract_fingerprint(text: str) -> dict:
    """
    Build a stylometric feature vector from a single text sample.

    Returns a JSON-serialisable dict suitable for storage in
    socint_resonance.features_json or actors.socint_profile.

    All values are computed from raw text — the normalised body is also
    stored so compare_fingerprints() can run SequenceMatcher without
    re-normalising.

    Parameters
    ----------
    text : str
        Raw text from an X post. May contain URLs, @mentions, emojis,
        cashtags, hashtags, and leetspeak.

    Returns
    -------
    dict with keys:
        text_norm       str           Normalised text (URLs/mentions stripped)
        cashtags        list[str]     e.g. ["$ZAR", "$JSE"]
        hashtags        list[str]     e.g. ["#loadshedding"]
        emojis          list[str]     Ordered emoji list
        emoji_bigrams   dict          Counter of emoji pairs (serialised)
        caps_ratio      float         ALL-CAPS word density [0.0-1.0]
        leet_density    float         Leet-char ratio [0.0-1.0]
        aggression      float         Punctuation aggression [0.0-1.0]
        thread_markers  list[str]     e.g. ["1/", "2/"]
        char_count      int           Raw character count
        word_count      int           Word count
    """
    norm  = _normalise_text(text)
    upper = text.upper()

    cashtags = sorted(set(_CASHTAG_RE.findall(upper)))
    hashtags = sorted(set(t.lower() for t in _HASHTAG_RE.findall(text)))
    emojis   = _extract_emojis(text)
    bigrams  = _emoji_bigrams(emojis)

    return {
        "text_norm":      norm,
        "cashtags":       cashtags,
        "hashtags":       hashtags,
        "emojis":         emojis,
        "emoji_bigrams":  dict(bigrams),   # Counter → plain dict for JSON
        "caps_ratio":     round(_caps_ratio(norm),    6),
        "leet_density":   round(_leet_density(text),  6),
        "aggression":     round(_aggression_score(text), 6),
        "thread_markers": _THREAD_RE.findall(text),
        "char_count":     len(text),
        "word_count":     len(text.split()),
    }


def compare_fingerprints(fp_a: dict, fp_b: dict) -> float:
    """
    Compute a pairwise resonance score between two fingerprint dicts.

    Formula
    ───────
    R = (W_SIM   x sequence_similarity)
      + (W_CASH  x cashtag_jaccard)
      + (W_EMOJI x emoji_bigram_cosine)
      + (W_CAPS  x caps_proximity)
      + (W_LEET  x leet_proximity)

    Parameters
    ----------
    fp_a, fp_b : dict
        Fingerprint dicts produced by extract_fingerprint().

    Returns
    -------
    float in [0.0, 1.0]
        Higher = more stylometrically similar.
        Values >= RESONANCE_THRESHOLD (0.65) indicate syndicate-level
        similarity and should trigger an entity_relationships write.
    """
    # Component 1: sequence similarity on normalised bodies
    sim = SequenceMatcher(
        None,
        fp_a.get("text_norm", ""),
        fp_b.get("text_norm", ""),
        autojunk=False,
    ).ratio()

    # Component 2: cashtag Jaccard
    cash = _jaccard(
        set(fp_a.get("cashtags", [])),
        set(fp_b.get("cashtags", [])),
    )

    # Component 3: emoji bigram cosine
    bigrams_a = Counter(fp_a.get("emoji_bigrams", {}))
    bigrams_b = Counter(fp_b.get("emoji_bigrams", {}))
    emoji = _cosine(bigrams_a, bigrams_b)

    # Component 4: ALL-CAPS density proximity (1 = identical density)
    caps = 1.0 - abs(fp_a.get("caps_ratio", 0.0) - fp_b.get("caps_ratio", 0.0))

    # Component 5: leet density proximity
    leet = 1.0 - abs(fp_a.get("leet_density", 0.0) - fp_b.get("leet_density", 0.0))

    score = (W_SIM   * sim
           + W_CASH  * cash
           + W_EMOJI * emoji
           + W_CAPS  * caps
           + W_LEET  * leet)

    return round(min(max(score, 0.0), 1.0), 6)


def score_signal_against_corpus(
    signal_text: str,
    corpus: list[str],
) -> Optional[float]:
    """
    Score a new text sample against an actor's accumulated text corpus.

    The corpus gate (_corpus_ready) is enforced here. Returns None when
    the corpus is too thin — callers must handle None without raising.

    Strategy: extract a fingerprint for the new signal, extract and
    average fingerprints from the corpus, then compare. This avoids the
    O(n²) cost of full pairwise comparison at ingestion time.

    Parameters
    ----------
    signal_text : str
        Raw text of the incoming signal.
    corpus : list[str]
        List of previous text samples attributed to this actor.

    Returns
    -------
    float in [0.0, 1.0] if corpus is ready, else None.
    """
    if not _corpus_ready(corpus):
        return None

    signal_fp = extract_fingerprint(signal_text)

    # Build a composite corpus fingerprint by averaging numeric features
    # and merging set/list features across all samples.
    corpus_fps = [extract_fingerprint(t) for t in corpus]

    # Merge text into a single normalised body for SequenceMatcher
    merged_norm = " ".join(fp["text_norm"] for fp in corpus_fps)

    all_cashtags: set = set()
    all_bigrams: Counter = Counter()
    caps_vals:  list[float] = []
    leet_vals:  list[float] = []

    for fp in corpus_fps:
        all_cashtags.update(fp.get("cashtags", []))
        all_bigrams.update(Counter(fp.get("emoji_bigrams", {})))
        caps_vals.append(fp.get("caps_ratio",  0.0))
        leet_vals.append(fp.get("leet_density", 0.0))

    corpus_fp: dict = {
        "text_norm":     merged_norm,
        "cashtags":      list(all_cashtags),
        "emoji_bigrams": dict(all_bigrams),
        "caps_ratio":    statistics.mean(caps_vals)  if caps_vals  else 0.0,
        "leet_density":  statistics.mean(leet_vals)  if leet_vals  else 0.0,
    }

    return compare_fingerprints(signal_fp, corpus_fp)


def update_actor_corpus(
    profile_json: Optional[str],
    new_text: str,
    max_samples: int = 100,
) -> str:
    """
    Append a new text sample to an actor's socint_profile corpus.

    Reads the existing JSON corpus from actors.socint_profile, appends the
    new sample, trims to max_samples (FIFO), and returns the updated JSON
    string ready for an UPDATE statement.

    Parameters
    ----------
    profile_json : str | None
        Current value of actors.socint_profile. None = first sample.
    new_text : str
        New text sample to append (raw, pre-normalisation).
    max_samples : int
        Rolling window size. Default 100 — approx. one month of daily posts.

    Returns
    -------
    str
        Updated JSON string for storage in actors.socint_profile.

    Example stored structure
    ─────────────────────────
    {
        "corpus": ["tweet text 1", "tweet text 2", ...],
        "x_handles": ["@username"],
        "x_display_names": ["Display Name"]
    }
    """
    if profile_json:
        try:
            profile = json.loads(profile_json)
        except (json.JSONDecodeError, TypeError):
            profile = {}
    else:
        profile = {}

    corpus: list[str] = profile.get("corpus", [])
    corpus.append(new_text)

    # FIFO trim — keep the most recent max_samples entries
    if len(corpus) > max_samples:
        corpus = corpus[-max_samples:]

    profile["corpus"] = corpus
    return json.dumps(profile, ensure_ascii=False)


def corpus_from_profile(profile_json: Optional[str]) -> list[str]:
    """
    Extract the text corpus list from actors.socint_profile JSON.
    Safe to call with None — returns an empty list.

    Parameters
    ----------
    profile_json : str | None
        Raw value of actors.socint_profile column.

    Returns
    -------
    list[str]
        Text samples. Empty list if profile is None, malformed, or
        contains no corpus key.
    """
    if not profile_json:
        return []
    try:
        profile = json.loads(profile_json)
        return profile.get("corpus", [])
    except (json.JSONDecodeError, TypeError):
        return []


# ─────────────────────────────────────────────────────────────────────────────
# CLI smoke test
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    # Reconfigure stdout to UTF-8 so emoji in test samples print correctly
    # on Windows terminals that default to cp1252.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # A and B: same syndicate — same cashtags, same emoji ORDER, same leet style.
    # C: unrelated analyst post — no cashtags, no emojis, no leet.
    _A = (
        "$ZAR $JSE loading up now!! 🔥💰🚨 they cant stop us 1/5 "
        "s3cur1ty 1s a j0ke #corruption #loadshedding"
    )
    _B = (
        "$ZAR $JSE BUY NOW!!! 🔥💰🚨 alg0r1thm 0wns this 2/5 "
        "system 1s r1gged #ZAR #corruption"
    )
    _C = (
        "The committee met today to discuss infrastructure challenges "
        "in the Northern Cape. Eskom representatives were present."
    )

    fp_a = extract_fingerprint(_A)
    fp_b = extract_fingerprint(_B)
    fp_c = extract_fingerprint(_C)

    score_ab = compare_fingerprints(fp_a, fp_b)
    score_ac = compare_fingerprints(fp_a, fp_c)

    print("=== Stylometric Engine Smoke Test ===")
    print()
    print(f"Sample A  : {_A[:60]}...")
    print(f"  cashtags     : {fp_a['cashtags']}")
    print(f"  emojis       : {fp_a['emojis']}")
    print(f"  emoji_bigrams: {fp_a['emoji_bigrams']}")
    print(f"  caps_ratio   : {fp_a['caps_ratio']}")
    print(f"  leet_density : {fp_a['leet_density']}")
    print(f"  aggression   : {fp_a['aggression']}")
    print()
    print(f"Sample B  : {_B[:60]}...")
    print(f"  cashtags     : {fp_b['cashtags']}")
    print()
    print(f"Sample C  : {_C[:60]}...")
    print()
    print(f"Resonance A <-> B : {score_ab:.4f}  (expect HIGH  - same syndicate)")
    print(f"Resonance A <-> C : {score_ac:.4f}  (expect LOW   - different actor class)")
    print()
    print(f"RESONANCE_THRESHOLD : {RESONANCE_THRESHOLD}")
    print(f"A<->B above threshold: {score_ab >= RESONANCE_THRESHOLD}")
    print(f"A<->C above threshold: {score_ac >= RESONANCE_THRESHOLD}")
    print()

    # Corpus gate test — need >= 7 items AND >= 2000 total chars
    thin_corpus = ["short post"] * 3
    # Build a realistic fat corpus: 10 items, each a full 280-char-style post
    _long_post  = (_A + " " + _B) * 2   # ~370 chars per sample
    fat_corpus  = [_long_post] * 10      # 10 items, ~3700 chars total
    print(f"_corpus_ready (3 short posts) : {_corpus_ready(thin_corpus)}")
    print(f"_corpus_ready (10 long posts) : {_corpus_ready(fat_corpus)}")
    print(f"  -> items : {len(fat_corpus)} / {CORPUS_MIN_ITEMS}")
    print(f"  -> chars : {sum(len(t) for t in fat_corpus)} / {CORPUS_MIN_CHARS}")
    print()

    # update_actor_corpus round-trip
    p = None
    for sample in [_A, _B, _C]:
        p = update_actor_corpus(p, sample)
    recovered = corpus_from_profile(p)
    print(f"Corpus round-trip : {len(recovered)} samples stored and recovered")
    print()
    print("Smoke test complete.")
    sys.exit(0)
