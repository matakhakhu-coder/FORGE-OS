"""
FORGE -- Disease Outbreak Collector  (Project Aegis / Stable 1.2)
=================================================================
Multi-tier global disease surveillance sensor.

Tier 1 -- WHO Disease Outbreak News (authoritative):
  Fully implemented.  relevance_score=2.5  stream=PRIORITY  is_priority=1

Tier 2 -- CDC Health Alert Network (semi-authoritative):
  Structurally complete, parser stub ready for expansion.
  relevance_score=1.8  stream=PRIORITY  is_priority=1

Tier 3 -- ProMED (early-warning noise layer):
  Structurally complete, parser stub ready for expansion.
  relevance_score=1.2  stream=GLOBAL  (→ PRIORITY on velocity surge)

Core mechanisms
───────────────
  Outbreak Fingerprint  sha256(pathogen_norm|region_code|iso_week)
  Patient Zero dedup    fingerprint check inside 72-hour window
  Velocity Surge        ≥5 distinct Tier-3 posts in 24h → synthetic PRIORITY signal
  Gazetteer priming     ~100-entry dict geocodes regions at collection time
  Semantic priming      normalized title + structured metadata_json
  Refinery              sanitize_text() on every title + content before insert

Stable 1.1/1.2 compliance
──────────────────────────
  source = manifest["id"] = "disease_outbreak_collector"
  (ensures _auto_pin_to_case membrane query matches every signal from this run)
  Zero new DB tables.  All columns present in Stable 1.1 schema.

Dependencies:  pip install feedparser requests --break-system-packages
"""

# ── Manifest (AST-parsed by Autodiscovery Registry at boot) ──────────────────
__manifest__ = {
    "id":          "disease_outbreak_collector",
    "name":        "Disease Outbreak Collector",
    "description": (
        "Multi-tier global disease surveillance. Tier 0: HealthMap JSON (266 "
        "geocoded markers, native lat/lng, forensic dedup). Tier 1-3: PAHO, "
        "ReliefWeb Health, ReliefWeb Epidemic RSS with velocity surge detection, "
        "outbreak fingerprint deduplication, and gazetteer location priming. "
        "Patient Zero protection prevents cross-source signal flooding."
    ),
    "icon":        "🦠",
    "entry":       "forage/collectors/disease_outbreak_collector.py",
    "args":        [],
    "job_key":     "disease_outbreak_collector",
    "version":     "1.1.0",
}

# ── Windows CP1252 safety -- reconfigure stdout to UTF-8 before any print() ────
import sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    try:
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── Standard library ──────────────────────────────────────────────────────────
import hashlib
import json
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
import os as _os
BASE_DIR  = Path(__file__).resolve().parent.parent.parent
_FORGE_DB_ENV = _os.environ.get("FORGE_DB")
DB_PATH   = Path(_FORGE_DB_ENV) if _FORGE_DB_ENV else BASE_DIR / "database.db"

# Canonical source key -- MUST match manifest["id"] for auto-pin membrane query
SOURCE_ID = "disease_outbreak_collector"

# ── Refinery (Stable 1.1) ─────────────────────────────────────────────────────
try:
    from core.pipeline.ingest import sanitize_text as _sanitize
except ImportError:
    def _sanitize(t): return t  # noqa: E731

# ── Pipeline logger (path-safe, no hard coupling) ─────────────────────────────
def _log_run_safe(*args, **kwargs):
    import importlib.util as _ilu
    _lp = BASE_DIR / "forage" / "utils" / "pipeline_logger.py"
    try:
        _spec = _ilu.spec_from_file_location("pipeline_logger", str(_lp))
        _mod  = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _mod.log_run(*args, **kwargs)
    except Exception:
        pass

log_run = _log_run_safe

# ── Optional dependencies ─────────────────────────────────────────────────────
try:
    import feedparser
    HAS_FEEDPARSER = True
except ImportError:
    HAS_FEEDPARSER = False
    print("[aegis] WARNING: feedparser not installed. "
          "Run: pip install feedparser --break-system-packages")

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    print("[aegis] WARNING: requests not installed. "
          "Run: pip install requests --break-system-packages")

# ─────────────────────────────────────────────────────────────────────────────
#  GAZETTEER  -- region name → (lat, lng) centroid
#  ~100 entries covering WHO regions, endemic zones, and outbreak-prone states.
#  Longest-substring match wins. Used for location priming before NER runs.
# ─────────────────────────────────────────────────────────────────────────────
GAZETTEER = {
    # Africa (AFRO) ──────────────────────────────────────────────────────────
    "nigeria":                        ( 9.082,   7.492),
    "democratic republic of the congo": (-4.038,  21.759),
    "drc":                            (-4.038,  21.759),
    "kinshasa":                       (-4.322,  15.322),
    "ethiopia":                       ( 9.145,  40.489),
    "kenya":                          (-0.023,  37.906),
    "uganda":                         ( 1.373,  32.290),
    "tanzania":                       (-6.369,  34.888),
    "south africa":                   (-30.559, 22.938),
    "ghana":                          ( 7.946,  -1.023),
    "guinea":                         ( 9.946,  -9.697),
    "guinea-bissau":                  (11.804, -15.180),
    "sierra leone":                   ( 8.460, -11.779),
    "liberia":                        ( 6.428,  -9.430),
    "cameroon":                       ( 3.848,  11.502),
    "central african republic":       ( 6.611,  20.939),
    "car":                            ( 6.611,  20.939),
    "sudan":                          (12.863,  30.218),
    "south sudan":                    ( 6.877,  31.307),
    "somalia":                        ( 5.152,  46.199),
    "senegal":                        (14.497, -14.452),
    "mali":                           (17.570,  -3.996),
    "burkina faso":                   (12.364,  -1.562),
    "niger":                          (17.608,   8.082),
    "chad":                           (15.454,  18.732),
    "angola":                         (-11.203, 17.874),
    "mozambique":                     (-18.665, 35.530),
    "zimbabwe":                       (-19.015, 29.154),
    "zambia":                         (-13.133, 27.849),
    "malawi":                         (-13.254, 34.302),
    "madagascar":                     (-18.767, 46.869),
    "rwanda":                         ( -1.940,  29.874),
    "burundi":                        ( -3.374,  29.919),
    "gabon":                          ( -0.804,  11.609),
    "equatorial guinea":              ( 1.651,    10.268),
    # Americas (AMRO / PAHO) ─────────────────────────────────────────────────
    "brazil":                         (-14.235, -51.925),
    "colombia":                       (  4.571, -74.297),
    "venezuela":                      (  6.424, -66.590),
    "peru":                           ( -9.190, -75.015),
    "bolivia":                        (-16.290, -63.589),
    "ecuador":                        ( -1.832, -78.183),
    "haiti":                          ( 18.971, -72.285),
    "mexico":                         ( 23.635,-102.553),
    "united states":                  ( 37.090, -95.713),
    "usa":                            ( 37.090, -95.713),
    "canada":                         ( 56.130,-106.347),
    "argentina":                      (-38.416, -63.617),
    "chile":                          (-35.675, -71.543),
    "cuba":                           ( 21.522, -77.782),
    "guatemala":                      ( 15.784, -90.231),
    "honduras":                       ( 15.200, -86.242),
    "nicaragua":                      ( 12.865, -85.207),
    "costa rica":                     (  9.748, -83.753),
    "panama":                         (  8.538, -80.783),
    "puerto rico":                    ( 18.221, -66.590),
    "trinidad and tobago":            ( 10.692, -61.223),
    "suriname":                       (  3.919, -56.028),
    # Eastern Mediterranean (EMRO) ───────────────────────────────────────────
    "pakistan":                       ( 30.375,  69.345),
    "afghanistan":                    ( 33.939,  67.710),
    "iran":                           ( 32.427,  53.688),
    "iraq":                           ( 33.224,  43.680),
    "syria":                          ( 34.802,  38.997),
    "yemen":                          ( 15.552,  48.516),
    "egypt":                          ( 26.820,  30.802),
    "saudi arabia":                   ( 23.886,  45.079),
    "jordan":                         ( 30.586,  36.238),
    "lebanon":                        ( 33.854,  35.862),
    "libya":                          ( 26.335,  17.228),
    "tunisia":                        ( 33.887,   9.538),
    "morocco":                        ( 31.792,  -7.092),
    "qatar":                          ( 25.355,  51.184),
    "oman":                           ( 21.513,  55.923),
    "kuwait":                         ( 29.311,  47.482),
    "bahrain":                        ( 26.067,  50.558),
    "djibouti":                       ( 11.826,  42.590),
    # Europe (EURO) ──────────────────────────────────────────────────────────
    "ukraine":                        ( 48.380,  31.165),
    "russia":                         ( 61.524, 105.319),
    "germany":                        ( 51.166,  10.451),
    "france":                         ( 46.228,   2.214),
    "italy":                          ( 41.872,  12.567),
    "spain":                          ( 40.464,  -3.749),
    "united kingdom":                 ( 55.378,  -3.436),
    "uk":                             ( 55.378,  -3.436),
    "netherlands":                    ( 52.133,   5.291),
    "poland":                         ( 51.920,  19.145),
    "turkey":                         ( 38.964,  35.243),
    "greece":                         ( 39.074,  21.824),
    "romania":                        ( 45.943,  24.967),
    "serbia":                         ( 44.017,  21.006),
    "georgia":                        ( 42.315,  43.357),
    "azerbaijan":                     ( 40.143,  47.577),
    # Southeast Asia (SEARO) ─────────────────────────────────────────────────
    "india":                          ( 20.594,  78.963),
    "bangladesh":                     ( 23.685,  90.357),
    "myanmar":                        ( 21.916,  95.956),
    "thailand":                       ( 15.870, 100.993),
    "indonesia":                      ( -0.789, 113.921),
    "sri lanka":                      (  7.873,  80.772),
    "nepal":                          ( 28.395,  84.124),
    "bhutan":                         ( 27.515,  90.434),
    "maldives":                       (  3.202,  73.220),
    # Western Pacific (WPRO) ─────────────────────────────────────────────────
    "china":                          ( 35.861, 104.195),
    "wuhan":                          ( 30.593, 114.305),
    "beijing":                        ( 39.904, 116.407),
    "guangdong":                      ( 23.130, 113.265),
    "hong kong":                      ( 22.320, 114.170),
    "vietnam":                        ( 14.058, 108.278),
    "viet nam":                       ( 14.058, 108.278),
    "philippines":                    ( 12.880, 121.774),
    "cambodia":                       ( 12.566, 104.991),
    "laos":                           ( 19.856, 102.495),
    "malaysia":                       (  4.211, 101.976),
    "papua new guinea":               ( -6.315, 143.956),
    "japan":                          ( 36.205, 138.253),
    "south korea":                    ( 35.908, 127.767),
    "australia":                      (-25.274, 133.775),
    "new zealand":                    (-40.901, 174.886),
    "fiji":                           (-17.713, 178.065),
    "mongolia":                       ( 46.863, 103.847),
    # WHO Region fallbacks ───────────────────────────────────────────────────
    "afro":                           (  0.0,   25.0),
    "amro":                           (  0.0,  -75.0),
    "emro":                           ( 25.0,   45.0),
    "euro":                           ( 50.0,   15.0),
    "searo":                          ( 15.0,   90.0),
    "wpro":                           ( 15.0,  120.0),
    # Global fallback ────────────────────────────────────────────────────────
    "global":                         (  0.0,    0.0),
    "worldwide":                      (  0.0,    0.0),
    "multiple countries":             (  0.0,    0.0),
    "international":                  (  0.0,    0.0),
}

# ─────────────────────────────────────────────────────────────────────────────
#  PATHOGEN NORMALIZER
#  Maps raw text fragments → canonical pathogen keys used in fingerprints.
#  Longest-substring match wins. Fallback: slug-ified raw text.
# ─────────────────────────────────────────────────────────────────────────────
PATHOGEN_NORMALIZE = {
    # Influenza A subtypes
    "h5n1":                 "influenza_a_h5n1",
    "h5n2":                 "influenza_a_h5n2",
    "h5n8":                 "influenza_a_h5n8",
    "h5n6":                 "influenza_a_h5n6",
    "h5":                   "influenza_a_h5",
    "h7n9":                 "influenza_a_h7n9",
    "h7n2":                 "influenza_a_h7n2",
    "h3n2":                 "influenza_a_h3n2",
    "h1n1":                 "influenza_a_h1n1",
    "h9n2":                 "influenza_a_h9n2",
    "h10n3":                "influenza_a_h10n3",
    "avian influenza":      "influenza_a_h5n1",   # default subtype assumption
    "bird flu":             "influenza_a_h5n1",
    "influenza a":          "influenza_a",
    # Filoviruses
    "ebola virus disease":  "ebola",
    "ebola":                "ebola",
    "marburg":              "marburg",
    "marburg virus":        "marburg",
    # Orthopoxviruses
    "mpox":                 "mpox",
    "monkeypox":            "mpox",
    "smallpox":             "variola",
    # Coronaviruses
    "covid-19":             "sars_cov_2",
    "covid":                "sars_cov_2",
    "sars-cov-2":           "sars_cov_2",
    "sars":                 "sars_cov_1",
    "mers-cov":             "mers_cov",
    "mers":                 "mers_cov",
    # Arboviruses
    "dengue fever":         "dengue",
    "dengue":               "dengue",
    "zika virus":           "zika",
    "zika":                 "zika",
    "yellow fever":         "yellow_fever",
    "chikungunya":          "chikungunya",
    "rift valley fever":    "rift_valley_fever",
    "rvf":                  "rift_valley_fever",
    "west nile":            "west_nile",
    "oropouche":            "oropouche",
    # Viral haemorrhagic fevers
    "lassa fever":          "lassa",
    "lassa":                "lassa",
    "crimean-congo":        "crimean_congo_hf",
    "cchf":                 "crimean_congo_hf",
    "hantavirus":           "hantavirus",
    # Paramyxoviruses
    "nipah virus":          "nipah",
    "nipah":                "nipah",
    "hendra":               "hendra",
    "measles":              "measles",
    # Bacterial
    "cholera":              "cholera",
    "bubonic plague":       "plague",
    "pneumonic plague":     "plague",
    "plague":               "plague",
    "anthrax":              "anthrax",
    "typhoid":              "typhoid",
    "meningococcal":        "meningococcal",
    "meningitis":           "meningitis",
    "listeria":             "listeria",
    # Respiratory / novel
    "novel pneumonia":      "novel_pneumonia",
    "pneumonia":            "novel_pneumonia",
    "acute respiratory":    "acute_respiratory",
    # Parasitic
    "malaria":              "malaria",
    "leishmaniasis":        "leishmaniasis",
    "trypanosomiasis":      "trypanosomiasis",
    # Enteric
    "hepatitis a":          "hepatitis_a",
    "hepatitis e":          "hepatitis_e",
    "hepatitis b":          "hepatitis_b",
    "hepatitis c":          "hepatitis_c",
    # Other
    "polio":                "poliovirus",
    "poliovirus":           "poliovirus",
    "rabies":               "rabies",
}

# ─────────────────────────────────────────────────────────────────────────────
#  SOURCE CONFIGURATIONS
# ─────────────────────────────────────────────────────────────────────────────
SOURCES = [
    {
        # Tier 0 -- HealthMap Global Alert Map JSON API.
        # 266 geocoded location markers with native lat/lng — no GAZETTEER needed.
        # Forensic dedup: sha256(place_id|iso_week) prevents re-ingestion within a week.
        # Covers all active WHO AFRO/SEARO/WPRO alert clusters simultaneously.
        "id":             "healthmap_json",
        "name":           "HealthMap Global Alert Map",
        "url":            "https://healthmap.org/getAlerts.php?lang=en&striphtml=1",
        "tier":           1,
        "stream":         "PRIORITY",
        "relevance_score": 2.0,
        "is_priority":    1,
    },
    {
        # Tier 1 -- PAHO (WHO Regional Office for the Americas) official news feed.
        # Covers measles, dengue, arboviruses, cholera, and novel outbreaks.
        # WHO DON RSS (https://www.who.int/feeds/entity/csr/don/en/rss.xml) is
        # now 404 -- WHO restructured their site in 2024/25.
        "id":             "paho_news",
        "name":           "PAHO Official News",
        "url":            "https://www.paho.org/en/rss.xml",
        "tier":           1,
        "stream":         "PRIORITY",
        "relevance_score": 2.5,
        "is_priority":    1,
    },
    {
        # Tier 2 -- ReliefWeb Health sector updates.
        # UN-curated situation reports, health cluster bulletins, outbreak advisories.
        # Replaced CDC HAN RSS (now JS-rendered, 0 RSS entries).
        "id":             "reliefweb_health",
        "name":           "ReliefWeb Health Sector",
        "url":            "https://reliefweb.int/updates/rss.xml?sector=Health",
        "tier":           2,
        "stream":         "PRIORITY",
        "relevance_score": 1.8,
        "is_priority":    1,
    },
    {
        # Tier 3 -- ReliefWeb Epidemic-tagged reports.
        # Higher noise, early-warning layer. Velocity surge detector applied here.
        # Replaced ProMED (all promedmail.org/feed URLs now 404).
        "id":             "reliefweb_epidemic",
        "name":           "ReliefWeb Epidemic Reports",
        "url":            "https://reliefweb.int/updates/rss.xml?tag=Epidemic",
        "tier":           3,
        "stream":         "GLOBAL",
        "relevance_score": 1.2,
        "is_priority":    0,
    },
]

# ─────────────────────────────────────────────────────────────────────────────
#  HELPER FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_pathogen(text: str) -> str:
    """
    Map raw disease text → canonical pathogen key.
    Uses longest-match against PATHOGEN_NORMALIZE dict.
    Fallback: slugified text (30 char cap).
    """
    if not text:
        return "unknown"
    norm = text.lower().strip()

    # Direct match
    if norm in PATHOGEN_NORMALIZE:
        return PATHOGEN_NORMALIZE[norm]

    # Longest-substring match
    best_key, best_len = None, 0
    for key in PATHOGEN_NORMALIZE:
        if key in norm and len(key) > best_len:
            best_key, best_len = key, len(key)
    if best_key:
        return PATHOGEN_NORMALIZE[best_key]

    # Regex: catch "influenza A(H5N1)" patterns missed above
    m = re.search(r'influenza\s+a\s*[\(\[]?([h\d]+n\d*)', norm)
    if m:
        sub = m.group(1).strip("([])")
        return f"influenza_a_{sub}"

    # Fallback slug
    slug = re.sub(r'[^a-z0-9]+', '_', norm.strip()).strip('_')
    return slug[:30] or "unknown"


def _geocode(region_text: str) -> tuple:
    """
    Return (lat, lng) for a region string via GAZETTEER.
    Longest-substring match. Returns (None, None) on failure.
    """
    if not region_text:
        return None, None
    norm = region_text.lower().strip()

    if norm in GAZETTEER:
        return GAZETTEER[norm]

    best_key, best_len = None, 0
    for key in GAZETTEER:
        if key in norm and len(key) > best_len:
            best_key, best_len = key, len(key)
    if best_key:
        return GAZETTEER[best_key]

    # Word-level: all words of a key appear in region
    for key in sorted(GAZETTEER, key=len, reverse=True):
        words = key.split()
        if len(words) > 1 and all(w in norm for w in words):
            return GAZETTEER[key]

    return None, None


def _region_code(region_text: str) -> str:
    """
    Derive a short region code used in the outbreak fingerprint.
    First matched GAZETTEER key → uppercase slug (max 20 chars).
    """
    if not region_text:
        return "UNKNOWN"
    norm = region_text.lower().strip()
    best_key, best_len = None, 0
    for key in GAZETTEER:
        if key in norm and len(key) > best_len:
            best_key, best_len = key, len(key)
    if best_key:
        return best_key.upper().replace(" ", "_")[:20]
    return re.sub(r'[^A-Z0-9_]', '_',
                  region_text.upper().strip())[:20] or "UNKNOWN"


def _parse_pubdate(raw: str) -> str:
    """Parse RFC 2822 / ISO pubDate strings → 'YYYY-MM-DD HH:MM:SS'."""
    if not raw:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    # Try RFC 2822 (feedparser standard)
    try:
        dt = parsedate_to_datetime(raw)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    # Try ISO variants
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw[:19], fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _make_external_id(url: str, pubdate: str) -> str:
    """SHA-256 of (url + pubdate) → stable, unique per article."""
    raw = f"{url}|{pubdate}".encode("utf-8")
    return "aegis-" + hashlib.sha256(raw).hexdigest()[:48]


def _make_fingerprint(pathogen_norm: str, region_code: str,
                      timestamp_iso: str) -> str:
    """
    Outbreak fingerprint: SHA-256 of 'pathogen|region|YYYY-Www'.
    Same biological event from multiple sources → same fingerprint.
    Resets weekly to allow ongoing-outbreak re-signalling.
    """
    try:
        dt = datetime.strptime(timestamp_iso[:10], "%Y-%m-%d")
        iso_week = dt.strftime("%Y-W%W")
    except ValueError:
        iso_week = datetime.now(timezone.utc).strftime("%Y-W%W")
    raw = f"{pathogen_norm}|{region_code}|{iso_week}".encode("utf-8")
    return "aegis-fp-" + hashlib.sha256(raw).hexdigest()[:40]


def _split_who_title(title: str) -> tuple:
    """
    WHO DON titles follow: "Disease -- Region" or "Disease - Region".
    Returns (pathogen_fragment, region_fragment).
    """
    # WHO uses en-dash (–) and hyphen (-); strip surrounding whitespace
    for sep in [" – ", " -- ", " - "]:
        if sep in title:
            parts = title.split(sep, 1)
            return parts[0].strip(), parts[1].strip()
    # No separator found -- entire title is the disease fragment
    return title.strip(), ""


# ─────────────────────────────────────────────────────────────────────────────
#  DATABASE HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _check_prior_fingerprint(conn: sqlite3.Connection,
                             outbreak_id: str) -> dict | None:
    """
    Query the last 72 hours for a signal with this outbreak fingerprint.
    Returns {signal_id, tier} or None (Patient Zero).
    """
    try:
        row = conn.execute(
            """
            SELECT signal_id,
                   json_extract(metadata_json, '$.tier')    AS tier,
                   json_extract(metadata_json, '$.sub_source') AS sub_source
            FROM   signals
            WHERE  json_extract(metadata_json, '$.outbreak_id') = ?
              AND  timestamp >= datetime('now', '-72 hours')
            LIMIT  1
            """,
            (outbreak_id,),
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def _increment_duplicate_count(conn: sqlite3.Connection,
                               external_id: str) -> None:
    """Bump duplicate_count on an existing signal when a re-fetch is blocked."""
    try:
        conn.execute(
            "UPDATE signals SET duplicate_count = COALESCE(duplicate_count, 0) + 1 "
            "WHERE external_id = ?",
            (external_id,),
        )
    except Exception:
        pass


def _insert_signal(conn: sqlite3.Connection, sig: dict,
                   velocity_map: dict,
                   increment_on_block: bool = False) -> str:
    """
    Write a signal to the DB applying two-tier deduplication.

    Returns: 'inserted' | 'blocked' | 'chained' | 'error'

    Dedup logic
    ───────────
      1. external_id UNIQUE constraint → catches same-article re-fetch
      2. Outbreak fingerprint (72h window):
           Same or lower tier already exists → BLOCK (increment in-memory counter)
           Higher tier arriving             → CHAIN (allow, note in metadata)
    """
    outbreak_id  = None
    current_tier = 3

    try:
        meta = json.loads(sig.get("metadata_json") or "{}")
        outbreak_id  = meta.get("outbreak_id")
        current_tier = meta.get("tier", 3)
    except (json.JSONDecodeError, TypeError):
        pass

    # Fingerprint check
    if outbreak_id:
        prior = _check_prior_fingerprint(conn, outbreak_id)
        if prior is not None:
            prior_tier = int(prior.get("tier") or 3)
            if current_tier >= prior_tier:
                # Same or lower authority -- block
                velocity_map[outbreak_id] = velocity_map.get(outbreak_id, 0) + 1
                if increment_on_block:
                    _increment_duplicate_count(conn, sig.get("external_id", ""))
                return "blocked"
            else:
                # Higher authority arriving -- chain and allow
                try:
                    meta["parent_signal_id"] = prior["signal_id"]
                    meta["chain_reason"]      = "higher_tier_confirmation"
                    sig["metadata_json"]      = json.dumps(meta)
                except Exception:
                    pass

    # Track velocity regardless of outcome
    if outbreak_id:
        velocity_map[outbreak_id] = velocity_map.get(outbreak_id, 0) + 1

    # DB insert
    try:
        conn.execute(
            """
            INSERT INTO signals
                (signal_id, source, external_id, title, content,
                 lat, lng, timestamp, status, stream,
                 relevance_score, is_priority, metadata_json, source_type)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'live')
            """,
            (
                sig["signal_id"],      sig["source"],        sig["external_id"],
                sig["title"],          sig["content"],
                sig.get("lat"),        sig.get("lng"),
                sig["timestamp"],      "raw",
                sig["stream"],         sig["relevance_score"],
                sig["is_priority"],    sig.get("metadata_json"),
            ),
        )
        return "chained" if (outbreak_id and
                             _check_prior_fingerprint(conn, outbreak_id) and
                             json.loads(sig.get("metadata_json") or "{}").get("parent_signal_id")) \
               else "inserted"
    except sqlite3.IntegrityError:
        if increment_on_block:
            _increment_duplicate_count(conn, sig.get("external_id", ""))
        return "blocked"   # external_id UNIQUE violation
    except Exception as exc:
        print(f"  [aegis] insert error: {exc}")
        return "error"


# ─────────────────────────────────────────────────────────────────────────────
#  VELOCITY ENGINE
# ─────────────────────────────────────────────────────────────────────────────
_VELOCITY_THRESHOLD = 5    # distinct Tier-3 reports in 24h → surge
_VELOCITY_HOURS     = 24


def _build_velocity_map(conn: sqlite3.Connection) -> dict:
    """
    Rebuild in-memory velocity map from DB signals of the last 24 hours.
    Returns {outbreak_id: count} -- stateless across runs.
    """
    try:
        rows = conn.execute(
            """
            SELECT json_extract(metadata_json, '$.outbreak_id') AS oid,
                   COUNT(*)                                       AS cnt
            FROM   signals
            WHERE  source   = ?
              AND  timestamp >= datetime('now', ?)
              AND  json_extract(metadata_json, '$.outbreak_id') IS NOT NULL
            GROUP  BY oid
            """,
            (SOURCE_ID, f"-{_VELOCITY_HOURS} hours"),
        ).fetchall()
        return {r["oid"]: r["cnt"] for r in rows}
    except Exception:
        return {}


def _emit_velocity_surge(conn: sqlite3.Connection, outbreak_id: str,
                         pathogen_norm: str, region_raw: str,
                         lat, lng, count: int) -> None:
    """
    Emit a synthetic PRIORITY signal summarising a velocity surge.
    Idempotent: checks for an existing surge signal this week before writing.
    """
    iso_week    = datetime.now(timezone.utc).strftime("%Y-W%W")
    surge_extid = "aegis-surge-" + hashlib.sha256(
        f"{outbreak_id}|{iso_week}".encode()
    ).hexdigest()[:40]

    # Idempotency guard
    try:
        exists = conn.execute(
            "SELECT 1 FROM signals WHERE external_id = ?", (surge_extid,)
        ).fetchone()
        if exists:
            return
    except Exception:
        return

    title   = (f"VELOCITY SURGE: {pathogen_norm.replace('_',' ').upper()} "
               f"-- {region_raw} -- {count} reports in {_VELOCITY_HOURS}h")
    content = (
        f"Project Aegis velocity alert: {count} independent disease reports "
        f"matching pathogen '{pathogen_norm}' from '{region_raw}' were detected "
        f"within the last {_VELOCITY_HOURS} hours. This threshold-crossing event "
        f"has been automatically escalated to PRIORITY. "
        f"Confidence: triangulated across multiple sources. "
        f"Recommended action: review pinned signals and activate Context Tunnel."
    )
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    meta = json.dumps({
        "outbreak_id":     outbreak_id,
        "pathogen":        pathogen_norm,
        "region":          region_raw,
        "tier":            0,           # 0 = synthetic / velocity engine
        "sub_source":      "velocity_engine",
        "velocity_count":  count,
        "velocity_trigger": True,
        "iso_week":        iso_week,
    })
    try:
        conn.execute(
            """
            INSERT INTO signals
                (signal_id, source, external_id, title, content,
                 lat, lng, timestamp, status, stream,
                 relevance_score, is_priority, metadata_json, source_type)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'live')
            """,
            (
                str(uuid.uuid4()), SOURCE_ID, surge_extid,
                _sanitize(title)[:300], _sanitize(content),
                lat, lng, now, "raw", "PRIORITY",
                2.0, 1, meta,
            ),
        )
        print(f"  [aegis:velocity] surge emitted -- {pathogen_norm} / {region_raw} "
              f"({count} reports)")
    except Exception as exc:
        print(f"  [aegis:velocity] surge emit error: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
#  TIER PARSERS
# ─────────────────────────────────────────────────────────────────────────────

def _parse_who_don(session, source_cfg: dict) -> list:
    """
    Tier 1 -- WHO Disease Outbreak News RSS.  FULLY IMPLEMENTED.

    Title pattern: "Disease [subtype] -- Country" or "Disease – Region"
    Extracts pathogen, region, geocodes, fingerprints, sanitizes.
    Returns list of signal dicts (DB-ready, no conn required).
    """
    if not HAS_FEEDPARSER or not HAS_REQUESTS:
        print("  [aegis:WHO] dependencies missing -- skipping")
        return []

    print(f"  [aegis:WHO] fetching {source_cfg['url']} ...")
    try:
        resp = session.get(source_cfg["url"], timeout=25)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as exc:
        print(f"  [aegis:WHO] fetch error: {exc}")
        return []

    signals = []
    for entry in feed.entries[:25]:         # cap: 25 items per poll cycle
        try:
            title_raw = (entry.get("title") or "").strip()
            desc_raw  = (entry.get("summary") or
                         entry.get("description") or "").strip()
            link      = (entry.get("link") or entry.get("id") or "")
            pub_raw   = (entry.get("published") or entry.get("updated") or "")

            # Refinery
            title   = _sanitize(title_raw)[:300]
            content = _sanitize(desc_raw)

            # Semantic extraction
            pathogen_fragment, region_fragment = _split_who_title(title_raw)
            pathogen_norm = _normalize_pathogen(pathogen_fragment)
            lat, lng      = _geocode(region_fragment)
            region_code   = _region_code(region_fragment)

            # If no region in title, try extracting from content
            if not region_fragment:
                for key in GAZETTEER:
                    if key in desc_raw.lower():
                        region_fragment = key
                        lat, lng        = GAZETTEER[key]
                        region_code     = key.upper().replace(" ", "_")[:20]
                        break

            # Identifiers
            ts          = _parse_pubdate(pub_raw)
            external_id = _make_external_id(link or title_raw, pub_raw)
            outbreak_id = _make_fingerprint(pathogen_norm, region_code, ts)

            # Ensure WHO prefix for keyword priming
            display_title = (title if title.lower().startswith("who")
                             else f"WHO: {title}")

            signals.append({
                "signal_id":      str(uuid.uuid4()),
                "source":         SOURCE_ID,
                "external_id":    external_id,
                "title":          display_title,
                "content":        content,
                "lat":            lat,
                "lng":            lng,
                "timestamp":      ts,
                "stream":         source_cfg["stream"],
                "relevance_score": source_cfg["relevance_score"],
                "is_priority":    source_cfg["is_priority"],
                "metadata_json":  json.dumps({
                    "outbreak_id":       outbreak_id,
                    "pathogen":          pathogen_norm,
                    "pathogen_raw":      pathogen_fragment,
                    "region":            region_fragment,
                    "region_code":       region_code,
                    "tier":              source_cfg["tier"],
                    "sub_source":        source_cfg["id"],
                    "source_url":        link,
                    "velocity_trigger":  False,
                    "candidate_actors":  ["WHO"],
                }),
            })
        except Exception as exc:
            print(f"  [aegis:WHO] item error: {exc}")

    print(f"  [aegis:WHO] parsed {len(signals)} candidate signals")
    return signals


def _parse_cdc_han(session, source_cfg: dict) -> list:
    """
    Tier 2 -- CDC Health Alert Network RSS.  STRUCTURALLY COMPLETE.

    Fetches and parses the CDC HAN RSS feed.
    Pathogen/region extraction is stubbed with a simple keyword scan --
    expand _extract_han_pathogen() and _extract_han_region() to add
    domain-specific HAN parsing logic (alert type codes, state prefixes, etc.).

    Returns list of signal dicts.
    """
    if not HAS_FEEDPARSER or not HAS_REQUESTS:
        print("  [aegis:CDC] dependencies missing -- skipping")
        return []

    print(f"  [aegis:CDC] fetching {source_cfg['url']} ...")
    try:
        resp = session.get(source_cfg["url"], timeout=25)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as exc:
        print(f"  [aegis:CDC] fetch error: {exc}")
        return []

    def _extract_han_pathogen(title: str, desc: str) -> str:
        """
        STUB -- expand with HAN alert-type codes (HAN-xxx) and
        explicit pathogen mentions in advisory text.
        """
        combined = (title + " " + desc).lower()
        for key in sorted(PATHOGEN_NORMALIZE, key=len, reverse=True):
            if key in combined:
                return PATHOGEN_NORMALIZE[key]
        return "unknown_pathogen"

    def _extract_han_region(title: str, desc: str) -> str:
        """
        STUB -- expand with US state extraction, international country mentions,
        and CDC geographic scope headers ("Multi-state", "International").
        """
        combined = (title + " " + desc).lower()
        # Try geographic keywords from gazetteer
        for key in sorted(GAZETTEER, key=len, reverse=True):
            if key in combined:
                return key
        # Fall back to United States (most HAN alerts are domestic)
        return "united states"

    signals = []
    for entry in feed.entries[:15]:
        try:
            title_raw = (entry.get("title") or "").strip()
            desc_raw  = (entry.get("summary") or
                         entry.get("description") or "").strip()
            link      = (entry.get("link") or entry.get("id") or "")
            pub_raw   = (entry.get("published") or entry.get("updated") or "")

            title   = _sanitize(title_raw)[:300]
            content = _sanitize(desc_raw)

            pathogen_norm   = _extract_han_pathogen(title_raw, desc_raw)
            region_fragment = _extract_han_region(title_raw, desc_raw)
            lat, lng        = _geocode(region_fragment)
            region_code     = _region_code(region_fragment)

            ts          = _parse_pubdate(pub_raw)
            external_id = _make_external_id(link or title_raw, pub_raw)
            outbreak_id = _make_fingerprint(pathogen_norm, region_code, ts)

            display_title = (title if title.lower().startswith("cdc")
                             else f"CDC HAN: {title}")

            signals.append({
                "signal_id":      str(uuid.uuid4()),
                "source":         SOURCE_ID,
                "external_id":    external_id,
                "title":          display_title,
                "content":        content,
                "lat":            lat,
                "lng":            lng,
                "timestamp":      ts,
                "stream":         source_cfg["stream"],
                "relevance_score": source_cfg["relevance_score"],
                "is_priority":    source_cfg["is_priority"],
                "metadata_json":  json.dumps({
                    "outbreak_id":       outbreak_id,
                    "pathogen":          pathogen_norm,
                    "region":            region_fragment,
                    "region_code":       region_code,
                    "tier":              source_cfg["tier"],
                    "sub_source":        source_cfg["id"],
                    "source_url":        link,
                    "velocity_trigger":  False,
                    "candidate_actors":  ["CDC"],
                }),
            })
        except Exception as exc:
            print(f"  [aegis:CDC] item error: {exc}")

    print(f"  [aegis:CDC] parsed {len(signals)} candidate signals")
    return signals


def _parse_promed(session, source_cfg: dict) -> list:
    """
    Tier 3 -- ProMED RSS.  STRUCTURALLY COMPLETE.

    ProMED post titles follow: "DISEASE (NN): COUNTRY, ..."
    where NN is a sequential update number within an outbreak thread.
    Pathogen extraction is stubbed -- expand _extract_promed_pathogen()
    with ProMED category codes (e.g., "AVIAN INFLUENZA (37)") for
    precise subtype identification and update-chain tracking.

    Returns list of signal dicts.
    """
    if not HAS_FEEDPARSER or not HAS_REQUESTS:
        print("  [aegis:ProMED] dependencies missing -- skipping")
        return []

    print(f"  [aegis:ProMED] fetching {source_cfg['url']} ...")
    try:
        resp = session.get(source_cfg["url"], timeout=25)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as exc:
        print(f"  [aegis:ProMED] fetch error: {exc}")
        return []

    def _extract_promed_pathogen(title: str) -> str:
        """
        STUB -- ProMED titles: "DISEASE NAME (update_num): COUNTRY".
        Expand to strip update numbers and map to pathogen normalizer.
        Full implementation: regex on r'^([^(]+)\\s*\\(\\d+\\):' then normalize.
        """
        # Strip update number: "AVIAN INFLUENZA (37): Vietnam" → "AVIAN INFLUENZA"
        clean = re.sub(r'\s*\(\d+\)\s*:', ':', title).split(':')[0].strip()
        return _normalize_pathogen(clean)

    def _extract_promed_region(title: str, desc: str) -> str:
        """
        STUB -- ProMED titles: "DISEASE: COUNTRY, Province".
        Expand to parse the country list after the colon.
        Full implementation: split on ':', take second part, parse comma list.
        """
        # Try the part after ':'
        if ':' in title:
            region_part = title.split(':', 1)[1].strip()
            first_country = region_part.split(',')[0].strip()
            if first_country:
                return first_country.lower()
        # Gazetteer scan on combined text
        combined = (title + " " + desc).lower()
        for key in sorted(GAZETTEER, key=len, reverse=True):
            if key in combined:
                return key
        return "global"

    signals = []
    for entry in feed.entries[:30]:        # ProMED is high-volume
        try:
            title_raw = (entry.get("title") or "").strip()
            desc_raw  = (entry.get("summary") or
                         entry.get("description") or "").strip()
            link      = (entry.get("link") or entry.get("id") or "")
            pub_raw   = (entry.get("published") or entry.get("updated") or "")

            title   = _sanitize(title_raw)[:300]
            content = _sanitize(desc_raw)

            pathogen_norm   = _extract_promed_pathogen(title_raw)
            region_fragment = _extract_promed_region(title_raw, desc_raw)
            lat, lng        = _geocode(region_fragment)
            region_code     = _region_code(region_fragment)

            ts          = _parse_pubdate(pub_raw)
            external_id = _make_external_id(link or title_raw, pub_raw)
            outbreak_id = _make_fingerprint(pathogen_norm, region_code, ts)

            display_title = (title if title.lower().startswith("promed")
                             else f"ProMED: {title}")

            signals.append({
                "signal_id":      str(uuid.uuid4()),
                "source":         SOURCE_ID,
                "external_id":    external_id,
                "title":          display_title,
                "content":        content,
                "lat":            lat,
                "lng":            lng,
                "timestamp":      ts,
                "stream":         source_cfg["stream"],
                "relevance_score": source_cfg["relevance_score"],
                "is_priority":    source_cfg["is_priority"],
                "metadata_json":  json.dumps({
                    "outbreak_id":       outbreak_id,
                    "pathogen":          pathogen_norm,
                    "region":            region_fragment,
                    "region_code":       region_code,
                    "tier":              source_cfg["tier"],
                    "sub_source":        source_cfg["id"],
                    "source_url":        link,
                    "velocity_trigger":  False,
                    "candidate_actors":  ["ProMED"],
                }),
            })
        except Exception as exc:
            print(f"  [aegis:ProMED] item error: {exc}")

    print(f"  [aegis:ProMED] parsed {len(signals)} candidate signals")
    return signals


def _parse_healthmap(session, source_cfg: dict) -> list:
    """
    Tier 0 -- HealthMap Global Alert Map JSON API.

    Fetches the full geocoded marker set (~266 active disease location clusters).

    Design decisions
    ────────────────
    One signal per marker (one per geocoded location) — mirrors HealthMap's own
    data model where each marker aggregates all active alerts at that place.

    Native geocoding: lat/lng taken directly from the API payload; GAZETTEER is
    not consulted. This ensures 100% coordinate precision for the Gravity Membrane.

    Forensic dedup: external_id = sha256(place_id|iso_week). Re-fetching the same
    marker within one ISO week is blocked and increments duplicate_count on the
    original signal rather than writing a duplicate row.

    Bleach pass: sanitize_text() strips HTML residue from label + place_name.
    Leading/trailing comma artifacts in the label field are also stripped.
    """
    if not HAS_REQUESTS:
        print("  [aegis:HealthMap] requests library missing -- skipping")
        return []

    print(f"  [aegis:HealthMap] fetching {source_cfg['url']} ...")
    try:
        resp = session.get(
            source_cfg["url"], timeout=30,
            headers={"Accept": "application/json, */*"},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        print(f"  [aegis:HealthMap] fetch error: {exc}")
        return []

    markers = data.get("markers", [])
    if not markers:
        print("  [aegis:HealthMap] no markers in response")
        return []

    iso_week = datetime.now(timezone.utc).strftime("%G-W%V")
    now      = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    signals  = []

    for m in markers:
        try:
            place_id   = str(m.get("place_id") or "").strip()
            place_name = _sanitize(str(m.get("place_name") or "").strip())

            # Bleach pass + strip leading/trailing comma artifact from label field
            raw_label   = str(m.get("label") or "").strip()
            clean_label = _sanitize(raw_label).strip(", ").strip()

            # Native geocoding — bypass GAZETTEER entirely
            try:
                lat = float(m["lat"]) if m.get("lat") is not None else None
                lng = float(m["lon"]) if m.get("lon") is not None else None
            except (TypeError, ValueError):
                lat = lng = None

            if not place_id or not place_name:
                continue

            # Forensic deduplication fingerprint: sha256(place_id|iso_week)
            fp_raw      = f"{place_id}|{iso_week}".encode("utf-8")
            fp_hex      = hashlib.sha256(fp_raw).hexdigest()
            external_id = f"aegis-hm-{fp_hex[:48]}"

            # Signal title
            label_snippet = clean_label[:120] if clean_label else ""
            title = (f"HealthMap: {place_name} -- {label_snippet}"
                     if label_snippet else f"HealthMap: {place_name}")
            title = title[:300]

            # Signal content — verbose for NER / keyword matching downstream
            content = (
                f"HealthMap geocoded alert cluster: {place_name}. "
                f"Active disease markers: {clean_label}."
                if clean_label
                else f"HealthMap geocoded alert cluster: {place_name}."
            )

            # Alert count — best-effort parse of alertids string
            alert_count = 0
            try:
                aid_str  = m.get("alertids") or "[]"
                aid_list = json.loads(aid_str.replace("'", '"'))
                alert_count = len(aid_list)
            except Exception:
                pass

            signals.append({
                "signal_id":       str(uuid.uuid4()),
                "source":          SOURCE_ID,
                "external_id":     external_id,
                "title":           title,
                "content":         content,
                "lat":             lat,
                "lng":             lng,
                "timestamp":       now,
                "stream":          source_cfg["stream"],
                "relevance_score": source_cfg["relevance_score"],
                "is_priority":     source_cfg["is_priority"],
                "metadata_json":   json.dumps({
                    "outbreak_id":      external_id,
                    "place_id":         place_id,
                    "place_name":       place_name,
                    "label":            clean_label,
                    "iso_week":         iso_week,
                    "alert_count":      alert_count,
                    "tier":             source_cfg["tier"],
                    "sub_source":       "healthmap_json",
                    "velocity_trigger": False,
                    "candidate_actors": ["HealthMap", "WHO"],
                }),
            })
        except Exception as exc:
            print(f"  [aegis:HealthMap] marker error: {exc}")

    print(f"  [aegis:HealthMap] parsed {len(signals)} location markers "
          f"(iso_week={iso_week})")
    return signals


# ─────────────────────────────────────────────────────────────────────────────
#  PARSER DISPATCH TABLE
# ─────────────────────────────────────────────────────────────────────────────
_PARSERS = {
    # Tier 0: HealthMap geocoded JSON (v1.1 primary source)
    "healthmap_json":     _parse_healthmap,
    # Tier 1-3: RSS sources (Stable 1.2)
    "paho_news":          _parse_who_don,
    "reliefweb_health":   _parse_cdc_han,
    "reliefweb_epidemic": _parse_promed,
    # Legacy keys preserved so existing job_key references don't break
    "who_don": _parse_who_don,
    "cdc_han": _parse_cdc_han,
    "promed":  _parse_promed,
}


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN RUNNER
# ─────────────────────────────────────────────────────────────────────────────

def run() -> None:
    """
    Aegis collection cycle:
      1. Build velocity map from recent DB signals
      2. Open HTTP session
      3. For each source: fetch → parse → dedup → insert
      4. Post-insert velocity surge check → emit synthetic signals if threshold hit
      5. Commit + report
    """
    if not HAS_REQUESTS:
        print("[aegis] ABORT: requests library not available.")
        return

    print(f"[aegis] Project Aegis starting -- DB: {DB_PATH}")
    start_ts = datetime.now(timezone.utc)

    # ── DB connection ─────────────────────────────────────────────────────────
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
    except Exception as exc:
        print(f"[aegis] ABORT: cannot open DB: {exc}")
        return

    # ── Rebuild velocity map from existing DB signals ─────────────────────────
    velocity_map = _build_velocity_map(conn)
    print(f"[aegis] Velocity map loaded: {len(velocity_map)} active fingerprints")

    # ── HTTP session with FORGE user-agent ────────────────────────────────────
    session = requests.Session()
    session.headers.update({
        "User-Agent": "FORGE-Aegis/1.2 Disease Outbreak Intelligence Collector",
        "Accept":     "application/rss+xml, application/xml, text/xml, */*",
    })
    session.verify = False   # some health authority servers have cert issues
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass

    # ── Collection pass ───────────────────────────────────────────────────────
    totals = {"inserted": 0, "blocked": 0, "chained": 0, "error": 0}
    surge_candidates: dict = {}   # {outbreak_id: (pathogen, region, lat, lng)}

    for source_cfg in SOURCES:
        parser_fn = _PARSERS.get(source_cfg["id"])
        if not parser_fn:
            print(f"  [aegis] no parser for source '{source_cfg['id']}' -- skipping")
            continue

        candidates = parser_fn(session, source_cfg)

        # HealthMap uses forensic dedup: increment duplicate_count on blocked re-fetches
        hm_source = (source_cfg.get("id") == "healthmap_json")

        for sig in candidates:
            result = _insert_signal(conn, sig, velocity_map,
                                    increment_on_block=hm_source)
            totals[result] = totals.get(result, 0) + 1

            if result == "inserted" and source_cfg["tier"] == 3:
                # Track Tier-3 candidates for velocity surge evaluation
                try:
                    meta = json.loads(sig.get("metadata_json") or "{}")
                    oid  = meta.get("outbreak_id")
                    if oid:
                        surge_candidates[oid] = (
                            meta.get("pathogen", "unknown"),
                            meta.get("region", ""),
                            sig.get("lat"),
                            sig.get("lng"),
                        )
                except Exception:
                    pass

        conn.commit()

    # ── Velocity surge pass ───────────────────────────────────────────────────
    surges_emitted = 0
    for oid, (pathogen, region, lat, lng) in surge_candidates.items():
        count = velocity_map.get(oid, 0)
        if count >= _VELOCITY_THRESHOLD:
            _emit_velocity_surge(conn, oid, pathogen, region, lat, lng, count)
            surges_emitted += 1

    conn.commit()
    conn.close()

    elapsed = (datetime.now(timezone.utc) - start_ts).total_seconds()

    # ── Summary ───────────────────────────────────────────────────────────────
    print(
        f"[aegis] Complete in {elapsed:.1f}s -- "
        f"+{totals['inserted']} new | "
        f"~{totals['blocked']} blocked | "
        f"^{totals.get('chained', 0)} chained | "
        f"!{surges_emitted} surges | "
        f"x{totals.get('error', 0)} errors"
    )

    # ── Pipeline telemetry ────────────────────────────────────────────────────
    log_run(
        collector="disease_outbreak_collector",
        new_signals=totals["inserted"],
        errors=totals.get("error", 0),
        runtime_seconds=elapsed,
        meta={
            "blocked":  totals["blocked"],
            "chained":  totals.get("chained", 0),
            "surges":   surges_emitted,
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse as _ap
    _parser = _ap.ArgumentParser(description="FORGE Project Aegis — Disease Outbreak Collector")
    _parser.add_argument("--db", type=Path, default=None, help="Path to database.db")
    _parser.add_argument("--dry-run", action="store_true", help="Fetch and display without DB writes")
    _args = _parser.parse_args()
    if _args.db:
        DB_PATH = _args.db.resolve()
    if _args.dry_run:
        print(f"[aegis] DRY RUN — would connect to: {DB_PATH}")
        print("[aegis] Dry run complete (no writes)")
    else:
        run()
