"""
FORGE — source independence check  (forge_modules/counterintel/independence.py)
================================================================================
Sprint 4 repair of counterintel for hand-checked pins.

The old detectors compare signal titles and summaries. For checked pins those
are our own summaries, written to one template, so everything looks alike.
This detector reads the *saved article text* of each source instead and asks,
for the reports behind one incident:

  * copied      - one article reuses long runs of another's wording
                  (syndication, a press release pasted in)
  * shared      - the articles are written separately but repeat the same
                  quoted statement word for word (usually a police statement)
  * independent - separate wording and no shared quotes

and, per article, whose words it carries: police, family, community, witness,
official. An incident whose every article carries only police voices rests on
police statements, however many outlets ran it.

Pure functions: no database, no network. The runner (tools/evidence_layer.py)
feeds it the private article cache and writes its results privately. Nothing
here decides that two *incidents* are linked; it only describes the reporting
behind one incident.
"""
from __future__ import annotations

import re
from itertools import combinations

SHINGLE = 8               # words per shingle for copy detection
COPY_CONTAINMENT = 0.50   # share of the shorter text found in the longer one
SHARED_PASSAGE = 0.08     # smaller overlaps: shared paragraphs / statement text
MIN_QUOTE_WORDS = 6

_QUOTE_RE = re.compile(r'[\u201c"]([^\u201c\u201d"]{20,600})[\u201d"]')

VOICES = {
    "police": re.compile(
        r"\b(police|saps|spokesperson|spokesman|spokeswoman|warrant officer|"
        r"captain|lieutenant|lt-col|lt col|colonel|sergeant|sgt|detectives?|"
        r"anti-gang unit|metro police|law enforcement)\b", re.I),
    "family": re.compile(
        r"\b(mother|father|aunt|uncle|brother|sister|cousin|grandmother|"
        r"grandfather|family|relative|son|daughter|wife|husband|girlfriend|"
        r"boyfriend|niece|nephew)\b", re.I),
    "community": re.compile(
        r"\b(cpf|community policing forum|councillor|ward|resident|residents|"
        r"neighbour|neighbours|community leader|activist|pastor|principal|"
        r"neighbourhood watch|chairperson|chair of)\b", re.I),
    "witness": re.compile(r"\b(witness|witnesses|eyewitness|onlooker|bystander)\b", re.I),
    "official": re.compile(
        r"\b(mec|minister|mayor|mayco|premier|department|ipid|npa|"
        r"prosecuting authority|magistrate|court)\b", re.I),
}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", (text or "").lower())


def shingles(text: str, n: int = SHINGLE) -> set[tuple[str, ...]]:
    w = _words(text)
    return {tuple(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}


def containment(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def quotes(text: str) -> list[str]:
    out = []
    for m in _QUOTE_RE.finditer(text or ""):
        q = re.sub(r"\s+", " ", m.group(1)).strip()
        if len(q.split()) >= MIN_QUOTE_WORDS:
            out.append(q)
    return out


def _norm(q: str) -> str:
    return " ".join(_words(q))


def shared_quotes(a: str, b: str) -> list[str]:
    """Quotes (6+ words) in a that also appear word for word in b's text."""
    nb = " ".join(_words(b))
    seen, out = set(), []
    for q in quotes(a):
        n = _norm(q)
        # a quote may be trimmed by one outlet; compare its central 6 words too
        core = " ".join(n.split()[:8])
        if (n in nb or (len(core.split()) >= MIN_QUOTE_WORDS and core in nb)) and n not in seen:
            seen.add(n)
            out.append(q)
    return out


def voices_in_quotes(text: str) -> dict[str, int]:
    """Which kinds of people the article attributes its statements to.

    Looks at the sentence around each attribution verb (said / says / told /
    confirmed / added) and counts the voice words found there."""
    counts = {k: 0 for k in VOICES}
    for sent in re.split(r"(?<=[.!?\u201d])\s+", text or ""):
        if not re.search(r"\b(said|says|told|confirmed|added|according to|stated|explained|appealed)\b", sent, re.I):
            continue
        for k, rx in VOICES.items():
            if rx.search(sent):
                counts[k] += 1
    return counts


def compare_pair(a: dict, b: dict) -> dict:
    """a, b: {"url", "outlet", "text"}."""
    sa, sb = shingles(a["text"]), shingles(b["text"])
    c = round(containment(sa, sb), 3)
    sq = shared_quotes(a["text"], b["text"])
    if c >= COPY_CONTAINMENT:
        verdict = "copied"
    elif sq or c >= SHARED_PASSAGE:
        verdict = "shared"
    else:
        verdict = "independent"
    return {"a": a["url"], "b": b["url"], "outlet_a": a.get("outlet"),
            "outlet_b": b.get("outlet"), "containment": c,
            "shared_quotes": sq, "verdict": verdict}


def assess(articles: list[dict]) -> dict:
    """Describe the reporting behind one incident.

    articles: [{"url", "outlet", "text", "author"?}], text = saved article text.
    Returns a dict with per-article voices, pairwise comparisons and a machine
    verdict. The verdict is a draft for a person to confirm, never a label that
    goes on the site by itself.
    """
    arts = [a for a in articles if (a.get("text") or "").strip()]
    per = []
    for a in arts:
        v = voices_in_quotes(a["text"])
        per.append({"url": a["url"], "outlet": a.get("outlet"),
                    "author": a.get("author"), "words": len(_words(a["text"])),
                    "voices": v,
                    "non_police_voices": sorted(k for k, n in v.items() if n and k != "police")})
    pairs = [compare_pair(x, y) for x, y in combinations(arts, 2)]
    outlets = sorted({(a.get("outlet") or "").strip() for a in arts if a.get("outlet")})
    police_only = bool(per) and all(not p["non_police_voices"] for p in per)

    if not arts:
        verdict = "no_text"
    elif len(outlets) <= 1:
        verdict = "single_outlet"
    elif any(p["verdict"] == "copied" for p in pairs):
        verdict = "copied"
    elif police_only:
        verdict = "police_statements_only"
    elif all(p["verdict"] == "independent" for p in pairs):
        verdict = "independent"
    else:
        verdict = "partly_independent"
    return {"articles": per, "pairs": pairs, "outlets": outlets,
            "police_only": police_only, "verdict": verdict}
