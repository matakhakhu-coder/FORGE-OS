"""
Priority Intelligence (PI) rules, shared by the collectors (23 Oct 2026).

What PI is
----------
The Crime & Security stream is street crime (the killings on the map). PI is
the "different collared" layer: organised, political and state-level crime,
the signals a national agency would own. The test for every story is:

    Would the Hawks (DPCI, national priority offences under the SAPS Act:
    serious organised crime, serious commercial crime, serious corruption),
    SAPS Crime Intelligence, NICOC or the SSA own this?

Five categories answer that test (CATEGORIES below). A story reaches PI only
when
  * it names one of those categories' markers, or an owning agency, and
  * it reports something an official, a court or credible reporting says
    happened (an arrest, a court step, an incident, an investigation, a
    finding, a stated allegation), not policy talk or commentary, and
  * it is about South Africa.

No FORGE analysis is attached: metadata records which words matched and who
said what kind of thing, never a theory. The owner's own pattern analysis
stays private until a source backs it.

Overlap with the other streams: decided 23 Oct 2026, standalone
---------------------------------------------------------------
Some PI stories are also a killing (a councillor shot dead) or an
infrastructure event (a substation stripped by a syndicate). The owner chose
standalone on 23 Oct 2026: those keep their home stream with the PI-candidate
flag, and the killings and infrastructure counts do not change. Escalate is
still built and FORGE_PI_MODE still switches it, but nothing should set it
without a new owner decision. Showing one pin in two streams (a "linked" badge
on the map) is a future publishing change, not built. FORGE_PI_MODE:

  standalone (default)  overlaps keep their home stream (CRIME_INTEL or
                        INFRASTRUCTURE) and carry pi_route=
                        "escalation_candidate" plus the PI fields, so they
                        can be listed or moved later without re-collecting.
  escalate              overlaps move to PRIORITY; pi_home_stream records
                        where they would otherwise have gone.

The default changes nothing for the killings or infrastructure streams.
"""

from __future__ import annotations

import os
import re
from typing import Optional

try:
    from forage.collectors import infra_signals as _infra
    from forage.collectors import place_signals as _place
except ImportError:  # pragma: no cover - script use
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from forage.collectors import infra_signals as _infra
    from forage.collectors import place_signals as _place

STREAM = "PRIORITY"
MODES = ("standalone", "escalate")
# Owner decision, 23 Oct 2026.
DEFAULT_MODE = "standalone"

CATEGORIES = {
    "organised_network":  "Organised networks",
    "political_violence": "Political violence",
    "state_sabotage":     "Sabotage of the state",
    "illegal_mining":     "Illegal mining syndicates",
    "corruption_capture": "Serious corruption and capture",
    "priority_offence":   "National priority offence (agency-led, category not named)",
}


def mode(value: Optional[str] = None) -> str:
    m = (value or os.environ.get("FORGE_PI_MODE") or DEFAULT_MODE).strip().lower()
    return m if m in MODES else DEFAULT_MODE


def _rx(p: str, flags=re.I) -> re.Pattern:
    return re.compile(p, flags)


# ── Owning agencies ─────────────────────────────────────────────────────────
# Case-sensitive acronyms where the lower-case word means something else.
OWNERS = [
    ("Hawks (DPCI)", _rx(r"\b(Hawks|DPCI|Directorate for Priority Crime Investigation)\b", 0)),
    ("SAPS Crime Intelligence", _rx(r"\bcrime intelligence\b")),
    ("NICOC", _rx(r"\b(NICOC|National Intelligence Co-?ordinating Committee)\b")),
    ("SSA", _rx(r"\b(SSA\b|State Security Agency)", 0)),
    ("NPA Investigating Directorate (IDAC)", _rx(r"\b(IDAC|Investigating Directorate)\b")),
    ("NPA Asset Forfeiture Unit", _rx(r"\b(asset forfeiture unit|AFU)\b", 0)),
    ("SIU", _rx(r"\b(SIU|Special Investigating Unit)\b", 0)),
    ("Political Killings Task Team", _rx(r"\b(political killings task team|PKTT|inter-?ministerial task team)\b")),
    ("Madlanga Commission", _rx(r"\bMadlanga\b")),
]

# An owner named with no category marker. SAPS Crime Intelligence is left
# out on purpose: routine station statements say "acting on crime
# intelligence" about street arrests, so it needs a category marker too.
OWNER_DEFAULT_CATEGORY = {
    "Hawks (DPCI)": "priority_offence",
    "NPA Investigating Directorate (IDAC)": "corruption_capture",
    "SIU": "corruption_capture",
    "NPA Asset Forfeiture Unit": "corruption_capture",
    "Madlanga Commission": "corruption_capture",
    "Political Killings Task Team": "political_violence",
    "NICOC": "priority_offence",
    "SSA": "priority_offence",
}

# The Hawks bird and the Sharks/Stormers rugby sense of "Hawks" are not the DPCI.
_HAWKS_NOISE = _rx(r"\b(hawks (vs?|v\.|beat|lose|win|coach|player)|hawthorn|atlanta hawks|bird of prey)\b")


# ── Category markers ────────────────────────────────────────────────────────
# Each entry: (category, strong pattern). Strong markers name the category
# itself; they still need an event or official step (see KIND_PATTERNS).
_VIOLENCE = r"(shot dead|shot and killed|gunned down|killed|murder(ed)?|assassinat\w*|ambush\w*|attempted murder|shot at|shooting|hit[- ]?m[ae]n|stabbed to death)"
_TARGET = r"(councillors?|mayors?|deputy mayor|speaker|\bMPL?s?\b|member of parliament|ward candidates?|politicians?|whistle-?blowers?|taxi (boss|bosses|owner|owners|association|leader|leaders)|izinduna|induna|traditional leaders?|branch (chair|secretary|leader)|union (leader|official)s?|activists?|municipal manager|official who|prosecutor|magistrate|investigator)"

CATEGORY_PATTERNS = [
    ("political_violence", _rx(
        rf"\b{_TARGET}\b[^.]{{0,80}}\b{_VIOLENCE}|\b{_VIOLENCE}\b[^.]{{0,60}}\b{_TARGET}\b"
        r"|\bpolitical(ly)?[- ](motivated|killings?|violence|murders?|hits?)\b|\bassassination\b"
        r"|\btaxi (violence|war|feud|turf)\b|\btender[- ](linked|related) (killing|murder|hit|shooting)s?\b")),
    ("state_sabotage", _rx(
        r"\b(economic sabotage|act of sabotage|deliberate sabotage|sabotage[ds]?|saboteurs?)\b"
        r"|\bcritical infrastructure (protection act|protection)\b")),
    ("illegal_mining", _rx(
        r"\bzama[- ]?zamas?\b|\billegal (gold )?min(ing|ers?)\b|\billicit (gold|mining)\b"
        r"|\bgold smuggling\b|\bunwrought precious metals?\b|\bvala umgodi\b")),
    ("organised_network", _rx(
        r"\bextort\w*|\bprotection (fees?|money|racket)\b|\b(construction|business forum|taxi|tender) mafia\b|\bmafia\b"
        r"|\bcash[- ]in[- ]transit\b|\bCIT (heist|robbery|robberies|gang|crew|suspects?)\b"
        r"|\bsyndicates?\b|\bcartels?\b|\bkingpins?\b|\bdrug ?lords?\b|\bcrime bosse?s?\b|\borganised crime\b|\borganized crime\b"
        r"|\b(drug|human|people|child|firearms?|gun|arms|rhino horn|abalone|wildlife|cigarette)s? (traffick|smuggl)\w*"
        r"|\btraffick(ing|ers?|ed)\b|\bsmuggl(ing|ers?|ed)\b"
        r"|\b(drug|clandestine|mandrax|tik|meth) (lab|laboratory)\b|\bkidnap\w* for ransom\b|\bransom\b"
        r"|\bracketeering\b|\bPOCA\b|\bPrevention of Organised Crime\b")),
    ("corruption_capture", _rx(
        r"\b(tender|procurement|contract) (fraud|corruption|rigging|irregularit\w*)\b|\bcorrupt(ion|ly)?\b"
        r"|\bbrib(e|es|ery|ed)\b|\bkickbacks?\b|\bmoney[- ]laundering\b|\blaundered\b|\bstate capture\b|\bZondo\b"
        r"|\bPRECCA\b|\bmisappropriat\w*|\blooting\b|\bunlawful(ly)? award\w*|\binflated (contract|price)s?\b"
        r"|\bcrime intelligence (boss|head|chief|deputy|officers?|officials?|general|members?)\b"
        r"|\bfraudulent(ly)? (passports?|ids?|identity documents?|permits?|visas?|birth certificates?)\b"
        r"|\bfraud\b[^.]{0,40}\bR ?\d[\d.,]* ?(million|billion|m\b|bn\b)|\bR ?\d[\d.,]* ?(million|billion|m\b|bn\b)[^.]{0,40}\bfraud\b")),
]

# Plain "fraud" counts only when an owning agency is involved.
_FRAUD = _rx(r"\bfraud\w*\b")

# What happened, in the order it is read: the first that matches is the kind.
KIND_PATTERNS = [
    ("court", _rx(r"\b(court|tribunal|appear(ed|s|ance)?|bail|convicted|sentenced|guilty|acquitted|indict\w*|plea|trial|charge sheet|judgment|ruling|postpone\w*|(loses|lost|fails?|failed) (in )?(a )?bid)\b")),
    ("arrest", _rx(r"\b(arrest\w*|nabbed|apprehended|bust(ed)?|raid(ed|s)?|seiz(ed|ure)|intercepted|confiscated|dismantled|cuffed|in custody|handed (himself|herself|themselves) over)\b")),
    ("incident", _rx(r"\b(shot dead|gunned down|killed|murder(ed)?|attack(ed)?|heist|robbery|robbed|bomb\w*|explosi\w*|ambush\w*|torched|set alight|blown up|kidnapped|abducted|sabotaged|extorted|stolen)\b")),
    # The names of bodies ("commission", "inquiry", "task team") are not
    # events: a story only naming the Madlanga Commission is not a step.
    ("investigation", _rx(r"\b(investigat\w*|probe[sd]?|probing|proclamation|warrant|preservation order|frozen|freeze|forfeit\w*|recover(s|ed|y)?|search(ed)? (and seizure|operation))\b")),
    ("finding", _rx(r"\b(report (finds|found|reveals)|found that|auditor-general|findings?|testif\w*|testimony|witness told|told the commission|expose[sd]?|uncover(s|ed)?)\b")),
    ("allegation", _rx(r"\b(alleg\w*|accused|claims?|implicated|linked to)\b")),
]

# Policy talk, opinion and features: context, never routed by itself.
_CONTEXT = _rx(
    r"\b(opinion|op-ed|editorial|column|analysis|explainer|podcast|webinar|conference|summit|strategy|"
    r"white paper|bill\b|legislation|budget vote|campaign against|fight against|war on|vows? to|pledges?|"
    r"calls? for|urges?|must|should|how to|why|what you need to know|lessons)\b")

# Protests about corruption are civic news, not a PI step.
_PROTEST = _rx(r"\b(march(es|ed)?|protest\w*|picket\w*|shutdown|memorandum|petition)\b")

# Fiction, sport and entertainment senses of the same words.
_NOISE = _rx(
    r"\b(movie|film|series|season \d|episode|trailer|album|song|novel|book review|netflix|showmax|"
    r"box office|video game|rugby|soccer|cricket|psl|derby|match report|fixture|squad)\b")

# Death words: a PI story that is also a killing overlaps the killings stream.
_DEATH = _rx(r"\b(death toll|deaths?|killing|shot dead|shot and killed|gunned down|killed|murder(ed|s)?|body|bodies|stabbed to death|died|dead|fatal(ly)?|assassinated)\b")

# South African context. National collectors also pick up foreign stories.
_SA = _rx(
    r"\b(South Africa\w*|Mzansi|SAPS|SANDF|NPA|Hawks|DPCI|SIU|IDAC|Madlanga|Zondo|Ramaphosa|Eskom|Transnet|PRASA|Sassa|ANC|EFF|IFP|MK [Pp]arty|"
    r"ActionSA|Gauteng|Limpopo|Mpumalanga|KwaZulu|KZN|Natal|Eastern Cape|Western Cape|Northern Cape|"
    r"North West|Free State|Johannesburg|Joburg|Pretoria|Tshwane|Durban|eThekwini|Cape Town|Gqeberha|Soweto|"
    r"Polokwane|Mbombela|Bloemfontein|Kimberley|Mahikeng|Rustenburg|East London|Ekurhuleni)\b", 0)


def _owners(text: str) -> list[str]:
    found = []
    for name, pat in OWNERS:
        if pat.search(text):
            if name.startswith("Hawks") and _HAWKS_NOISE.search(text):
                continue
            found.append(name)
    return found


def _kind(title: str, summary: str) -> Optional[str]:
    for k, pat in KIND_PATTERNS:
        if pat.search(title):
            return k
    for k, pat in KIND_PATTERNS:
        if pat.search(summary[:400]):
            return k
    return None


# Places that make a story foreign. Used for the direct South African feeds,
# which also carry world news.
_FOREIGN = _rx(
    r"\b(United States|USA|America\w*|UK|Britain|British|England|London|Mexic\w*|Colombia\w*|Brazil\w*|"
    r"India\w*|Pakistan\w*|China|Chinese|Russia\w*|Ukrain\w*|Israel\w*|Gaza|Iran\w*|Europe\w*|"
    r"Nigeria\w*|Kenya\w*|Ghana\w*|Zimbabwe\w*|Mozambi\w*|Malawi\w*|Zambia\w*|Botswana|Namibia\w*|"
    r"Lesotho|Eswatini|DRC|Congo\w*|Tanzania\w*|Uganda\w*|Ethiopia\w*|Sudan\w*|Australia\w*|"
    r"Canada|Canadian|Philippines|Filipino|Italy|Italian|France|French|Germany|German|Spain|Spanish|"
    r"Ecuador\w*|Peru\w*|Venezuela\w*|Haiti\w*|Taiwan\w*|Ireland|Irish|UDA|IRA|Loyalist\w*|Afghan\w*|Syria\w*)\b", 0)

# Townships and places the GeoNames gazetteer lacks or names ambiguously.
_SA_PLACES = _rx(
    r"\b(Umlazi|KwaMashu|Inanda|Ntuzuma|Pinetown|Umhlanga|Phoenix|Chatsworth|Khayelitsha|Mitchells Plain|"
    r"Gugulethu|Nyanga|Philippi|Hanover Park|Manenberg|Delft|Bishop Lavis|Tembisa|Alexandra|Diepsloot|"
    r"Mamelodi|Soshanguve|Atteridgeville|Katlehong|Thokoza|Vosloorus|Daveyton|Orange Farm|Zandspruit|"
    r"Mdantsane|Motherwell|KwaNobuhle|Seshego|Mankweng|Giyani|Thohoyandou|Musina|Tzaneen|Mthatha|"
    r"Klerksdorp|Stilfontein|Krugersdorp|Boksburg|Benoni|Germiston|Springs|Ermelo|Emalahleni|Witbank|"
    r"Middelburg|Standerton|Secunda|Barberton|Nelson Mandela Bay|Wallmansthal|Weenen|Umgababa|OR Tambo)\b", 0)

# South African outlets, read from the " - Outlet" suffix Google News adds.
_SA_OUTLET = _rx(
    r"\b(News24|IOL|TimesLIVE|Sowetan|EWN|eNCA|SABC|The Citizen|Daily Maverick|Mail & Guardian|amaBhungane|"
    r"AmaBhungane|Jacaranda|Algoa FM|ECR|Heart ?FM|HeartFM|Inside Politic|Sunday World|Sunday Times|City Press|"
    r"Moneyweb|Financial Mail|BusinessDay|Business Day|BusinessTech|The South African|Briefly News|Newzroom Afrika|"
    r"Polity|GroundUp|Cape Times|Cape Argus|Weekend Argus|weekendargus|The Witness|thepost|Review Online|"
    r"Lowvelder|Limpopo Mirror|Zoutpansberger|Capricorn Voice|Bosveld|Letaba Herald|novanews|TaungDailyNews|"
    r"Knysna-Plett Herald|DispatchLIVE|Daily Dispatch|Mpumalanga News|Witbank News|Middelburg Observer|Joburg ETC|"
    r"newsnote|yfm|Kaya|702|CapeTalk|Scrolla|South African Daily|Bulrushes|Congress of South African Trade|"
    r"PE Express|Tabloid Newspapers|African Times|Herald Live|HeraldLIVE|Netwerk24|Maroela Media|Rapport|"
    r"News Briefs|Sowetan LIVE|The Post)\b", 0)


def is_sa(text: str, province: Optional[str] = None, sa_source: bool = False) -> bool:
    """True only when something in the story or its source places it in
    South Africa. Google News also returns foreign stories for these
    searches (Delhi, Pskov, Gold Coast in the 23 Oct test), so a story
    with no South African marker is not read as South African."""
    if province:
        return True
    strong = bool(_SA.search(text) or _SA_PLACES.search(text)
                  or re.search(r"\bR ?\d[\d.,]* ?(million|billion|m|bn|k)\b", text))
    title = text.split(". ", 1)[0]
    # A foreign place in the headline outweighs a South African outlet or a
    # gazetteer name (Taiwan in Daily Maverick, Belfast's UDA, 23 Oct test).
    if _FOREIGN.search(title) and not strong:
        return False
    if strong or sa_source or _SA_OUTLET.search(text):
        return True
    loc = _infra.locate(text)
    return bool(loc)


def classify(title: str, summary: str = "", province: Optional[str] = None,
             sa_source: bool = False) -> dict:
    """Read one story against the PI rules.

    Returns {'is_pi', 'category', 'owners', 'kind', 'role', 'matched',
    'reason'}. role is 'signal' (an event or official step), 'context'
    (policy talk, opinion, no event) or None when the story is not PI.
    """
    title = title or ""
    summary = summary or ""
    text = f"{title}. {summary}"
    out = {"is_pi": False, "category": None, "owners": [], "kind": None, "role": None,
           "matched": [], "reason": ""}
    if _NOISE.search(title):
        out["reason"] = "entertainment or sport sense of the words"
        return out

    owners = _owners(text)
    cats = []
    for cat, pat in CATEGORY_PATTERNS:
        m = pat.search(text)
        if m:
            cats.append(cat)
            out["matched"].append(m.group(0).strip()[:60])
    if not cats and owners and _FRAUD.search(text):
        cats.append("corruption_capture")
        out["matched"].append(_FRAUD.search(text).group(0))
    if not cats:
        for o in owners:
            if o in OWNER_DEFAULT_CATEGORY:
                cats.append(OWNER_DEFAULT_CATEGORY[o])
                break
    if not cats:
        out["reason"] = "no PI category or owning agency"
        return out
    if not is_sa(text, province, sa_source):
        out["reason"] = "not about South Africa"
        return out

    # Title categories outrank summary-only ones.
    title_cats = [c for c, p in CATEGORY_PATTERNS if c in cats and p.search(title)]
    category = (title_cats or cats)[0]
    kind = _kind(title, summary)
    out.update(is_pi=True, category=category, owners=owners, kind=kind)
    question = title.rstrip(" -|").split(" - ")[0].rstrip().endswith("?")
    title_kind = _kind(title, "")
    protest = bool(_PROTEST.search(title)) and title_kind not in ("arrest", "court")
    if kind is None or question or protest or (_CONTEXT.search(title) and kind not in ("arrest", "court")):
        out["role"] = "context"
        out["reason"] = f"{CATEGORIES[category]} words, but no event, arrest, court step or investigation"
    else:
        out["role"] = "signal"
        out["reason"] = f"{CATEGORIES[category]}: {kind}" + (f" ({', '.join(owners)})" if owners else "")
    return out


def basis(c: dict) -> str:
    """Who the story says stands behind it, without adding anything."""
    if c.get("kind") == "court":
        return "court step reported"
    if c.get("owners"):
        # Named in the story, not necessarily speaking in it.
        return "agency named: " + ", ".join(c["owners"])
    if c.get("kind") == "allegation":
        return "allegation as reported (not a finding)"
    return "credible reporting (check the outlet)"


def route(stream: str, title: str, summary: str = "", province: Optional[str] = None,
          infra_meta: Optional[dict] = None, pi_mode: Optional[str] = None,
          sa_source: bool = False) -> tuple[str, bool, dict]:
    """Apply PI on top of a collector's own routing.

    ``stream`` is what the collector decided without PI. Returns
    (stream, is_priority, pi_meta). is_priority is True when the story
    reaches PRIORITY or is an escalation candidate. pi_meta is empty when
    the story is not PI. sa_source=True when the source itself is a South
    African outlet (the direct RSS feeds), so no place marker is needed.
    """
    c = classify(title, summary, province=province, sa_source=sa_source)
    if not c["is_pi"]:
        return stream, False, {}
    m = mode(pi_mode)
    meta = {
        "pi_category": c["category"],
        "pi_category_label": CATEGORIES[c["category"]],
        "pi_owners": c["owners"],
        "pi_kind": c["kind"],
        "pi_basis": basis(c),
        "pi_matched": c["matched"],
        "pi_reason": c["reason"],
        "pi_mode": m,
    }
    if c["role"] == "context":
        meta["pi_route"] = "context"
        return stream, False, meta

    infra_event = bool(infra_meta) and infra_meta.get("infra_role") not in (None, "context")
    text = f"{title}. {summary}"
    killing = bool(_DEATH.search(text))
    overlap = "INFRASTRUCTURE" if (infra_event or stream == "INFRASTRUCTURE") else (
        "CRIME_INTEL" if killing else None)
    if not (infra_meta or {}).get("location_candidate"):
        # Batch 3: a place named only as where an arrest or court step
        # happened is flagged for review, not used (place_signals).
        loc, review = _place.estimate(text, province=province)
        if loc:
            meta["location_candidate"] = loc
        if review:
            meta.update(review)
    if overlap is None:
        meta["pi_route"] = "standalone"
        return STREAM, True, meta
    meta["pi_home_stream"] = overlap
    if m == "escalate":
        meta["pi_route"] = "escalated"
        return STREAM, True, meta
    meta["pi_route"] = "escalation_candidate"
    return overlap, True, meta


# Searches for the Generalized News Monitor PI preset. National first; each
# names a category in words reporters use, so the classifier, not the query,
# decides the stream.
PI_NATIONAL_TERMS = (
    "Hawks arrest corruption", "Hawks syndicate arrested", "tender fraud arrested",
    "money laundering Hawks", "SIU proclamation", "Investigating Directorate IDAC",
    "construction mafia arrested", "extortion syndicate arrested", "cash-in-transit heist",
    "drug syndicate bust South Africa", "human trafficking syndicate South Africa",
    "firearms smuggling syndicate", "kidnapping for ransom syndicate",
    "councillor shot dead", "political killings task team", "whistleblower killed",
    "taxi boss shot dead", "sabotage Eskom", "sabotage Transnet", "economic sabotage",
    "critical infrastructure sabotage arrested", "illegal mining syndicate arrested",
    "zama zama kingpin", "Operation Vala Umgodi", "Crime Intelligence",
    "Madlanga commission",
)
PI_PROVINCE_TERMS = ("Hawks", "corruption arrested", "syndicate arrested", "extortion",
                     "councillor shot", "illegal mining", "sabotage", "cash-in-transit")


def pi_queries(province: Optional[str] = None) -> list[str]:
    """National PI searches, plus province-named ones when a province is given."""
    q = list(PI_NATIONAL_TERMS)
    if province:
        q = [f"{province} {t}" for t in PI_PROVINCE_TERMS] + q
    return q
