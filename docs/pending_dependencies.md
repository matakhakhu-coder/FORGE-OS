# FORGE — Pending & Optional Dependencies

> Last updated: 2026-06-04
> Scope: all collectors, processors, and core modules across the FORGE stack.
> A dependency is **PENDING** when it is needed for full functionality but has not yet been configured in this environment.

---

## 0. Homerun Play

Everything in this section can be done in one sitting. Two free API registrations unlock the two highest-value pending layers.

### Step 1 — BI-196 gazette PDF layer ✅ COMPLETE

Layer 3 is wired and operational. `bi196_collector` v1.3.0 now crawls
`opengazettes.org.za` (42,000+ gazette PDFs, 1958–2021, free, no authentication).
Legal Gazette A/C issues are downloaded, text-extracted via pdfplumber, and
scanned for BI-196 surname-change blocks.

`LAWS_AFRICA_TOKEN` is set (free tier = Cape Town by-laws only). The token is
preserved — if the account is upgraded to a paid plan, the collector will
automatically gain access to post-2021 national gazette content via the
laws.africa API.

To run the PDF pass:
```powershell
python forage/collectors/bi196_collector.py --gazette-pdfs-only
```

---

### Step 2 — ACLED API key (10 min) → full conflict intelligence

What it unlocks: `acled_collector` pulls every armed conflict, protest, and violence event in SA and the region directly from the ACLED database (the gold-standard conflict dataset). Without this, conflict signals come only from RSS/news — ACLED provides structured event data with coordinates, actor names, fatality counts.

```
1. Go to: https://developer.acleddata.com/
2. Register (free — select "Academic/Research" or "Non-profit" tier).
   Note: ACLED may take 24–48 h to approve the account.
3. Once approved, go to your dashboard → API Access → copy Key and Email.
4. Open FORGE/.env and set:
       ACLED_KEY=<your key>
       ACLED_EMAIL=<your registered email>
5. Run:
       python forage/collectors/acled_collector.py
```

Expected output: SA conflict events (protests, riots, armed clashes) ingested as structured signals with gravity_score and coordinates. ACLED-sourced signals feed the EntityEngine with high-confidence actor names directly from event metadata.

---

### Step 3 — verify all layers end-to-end

Once both keys are set, this single command runs the full BI-196 intelligence stack:

```powershell
python forage/collectors/bi196_collector.py
```

All four layers fire: RSS scan → gazette PDF crawl → actor cross-reference.

---

### Current state as of 2026-06-04

| Dependency | Status | Unblocked by |
|---|---|---|
| `FORGE_SECRET_KEY` | ✅ Set (generated 2026-06-04) | Done |
| `FORGE_ADMIN_PASSWORD` | ✅ Set (generated 2026-06-04) | Done |
| `LAWS_AFRICA_TOKEN` | ✅ Set — free tier (Cape Town by-laws only) | Set. Layer 3 rerouted to `opengazettes.org.za` |
| bi196 Layer 3 gazette PDFs | ✅ Wired — `opengazettes.org.za` (1958–2021, no auth) | Done |
| `ACLED_KEY` + `ACLED_EMAIL` | ⏳ Pending | Step 2 above |
| `X_BEARER_TOKEN` | — Optional | Only if guest_api X mode needed |

---

## 1. API Credentials (external sign-up required)

These unlock entire collector layers. Without them the collector runs in a degraded mode or skips entirely — no crash, just a logged warning.

| Status | Variable | Value | Collector / Layer | Sign-up URL |
|---|---|---|---|---|
| ✅ set (limited) | `LAWS_AFRICA_TOKEN` | set | `bi196_collector` — free tier gives Cape Town by-laws only; national gazette requires paid plan. Layer 3 now uses `opengazettes.org.za` (free, no auth). Token kept for future upgrade. | https://edit.laws.africa/accounts/login/ |
| **PENDING** | `ACLED_KEY` | not set | `acled_collector` — Armed conflict event data (free tier: 10k rows/month) | https://developer.acleddata.com/ |
| **PENDING** | `ACLED_EMAIL` | not set | `acled_collector` — Required alongside `ACLED_KEY` (paired auth) | same as above |
| optional | `X_BEARER_TOKEN` | not set | `flux/collectors/x_pulse.py` — X API `guest_api` mode only; default `nitter` mode works without it | https://developer.x.com/ |

**How to activate:**
```powershell
# Set for current session
$env:LAWS_AFRICA_TOKEN = "your_token_here"
$env:ACLED_KEY         = "your_key_here"
$env:ACLED_EMAIL       = "your_email_here"

# Or add to .env (python-dotenv loads this at app startup)
# LAWS_AFRICA_TOKEN=...
# ACLED_KEY=...
# ACLED_EMAIL=...
```

---

## 2. Security-Sensitive Defaults (harden before any networked deployment)

These work out of the box with weak defaults. Safe for local-only use; must be replaced before exposing FORGE to a network.

| Variable | Current Default | Risk | Fix |
|---|---|---|---|
| `FORGE_ADMIN_PASSWORD` | `"forge-admin"` | Admin panel access | Set a strong random password in `.env` |
| `FORGE_SECRET_KEY` | `"forge-dev-secret"` | Flask session forgery | Set a 32+ char random key in `.env` |

---

## 3. Optional pip Packages (runtime-gated)

All packages below are already in `requirements.txt` — they install with `pip install -r requirements.txt`. Listed here because they gate specific pipeline stages and their absence produces silent degradation rather than an error.

| Package | Gates | Fallback behaviour |
|---|---|---|
| `pdfplumber` | `bi196_collector` layer 3 PDF text extraction; `pdf_infiltrator` primary extraction path | Skipped with warning |
| `pytesseract` | OCR bridge in `pdf_infiltrator` and `artifact_processor` for scanned/image-based PDFs | OCR skipped; only text-layer PDFs processed |
| `pdf2image` | Required by `pytesseract` bridge for PDF→image conversion | OCR disabled |
| `PyMuPDF` (`fitz`) | `artifact_processor` primary PDF extraction (preferred over pypdf) | Falls back to `PyPDF2` |
| `bs4` (beautifulsoup4) | HTML scraping in `civic_intel_collector`, `pdf_infiltrator`, `dork_collector` | Scraping targets skipped |
| `feedparser` | RSS parsing in `civic_intel_collector`, `dork_collector`, `disease_outbreak_collector` | RSS sources skipped; warning logged |
| `networkx` | Stylometric community detection in `flux/processors/resonance.py` | C-SOCINT cluster step skipped |
| `openai-whisper` | Audio transcription in `artifact_processor` | Audio transcription unavailable |
| `PIL` (Pillow) | EXIF extraction + forensics in `artifact_processor`, `forensic_processor` | EXIF and thumbnail generation skipped |
| `pikepdf` | PDF detonation (suspicious PDF defusing) in `forge_security/detonator.py` | Detonator raises `ImportError` if called |
| `psutil` | Memory monitoring in `artifact_processor` during heavy PDF runs | Monitoring skipped; no hard limit on memory |

---

## 4. Environment Variables — Full Reference

### 4a. Required at runtime (no default — must be set or collector is skipped)

| Variable | Used by | Purpose |
|---|---|---|
| `ACLED_KEY` | `acled_collector` | ACLED API key |
| `ACLED_EMAIL` | `acled_collector` | ACLED account email (paired with key) |

### 4b. Optional with defaults

| Variable | Default | Used by | Purpose |
|---|---|---|---|
| `FORGE_DB` | `<project_root>/database.db` | ~25 files | Override database path |
| `FORGE_ADMIN_PASSWORD` | `"forge-admin"` | `app.py` | Admin panel password |
| `FORGE_SECRET_KEY` | `"forge-dev-secret"` | `app.py` | Flask session signing key |
| `LAWS_AFRICA_TOKEN` | `""` | `bi196_collector` | Laws.Africa gazette API token |
| `X_PULSE_MODE` | `"nitter"` | `flux/collectors/x_pulse.py` | X collector mode: `nitter` or `guest_api` |
| `X_BEARER_TOKEN` | `""` | `flux/collectors/x_pulse.py` | X v2 API bearer token (guest_api mode only) |
| `X_PULSE_TARGETS` | see source | `flux/collectors/x_pulse.py` | Comma-separated `@handle,#hashtag,$CASHTAG` targets |
| `FLUX_DISCOVERY_MODE` | `"false"` | `flux/collectors/x_pulse.py` | Enable butterfly/latent-seed discovery mode |
| `FLUX_MAX_DISCOVERY_DEPTH` | `2` | `flux/collectors/x_pulse.py` | Max expansion depth for discovery (0–2) |
| `FLUX_DISCOVERY_TOP_N` | `3` | `flux/collectors/x_pulse.py` | Latent seeds appended per pulse |
| `FLUX_RESONANCE_THRESHOLD` | `0.65` | `flux/processors/resonance.py` | Stylometric similarity gate (0.0–1.0) |
| `FLUX_GRAPH_THRESHOLD` | `0.70` | `flux/processors/resonance.py` | Threshold for writing to `entity_relationships` |
| `FLUX_CO_THRESHOLD` | `0.30` | `flux/processors/discovery.py` | Co-occurrence threshold for actor clustering |
| `FLUX_VELOCITY_THRESH` | `2.0` | `flux/processors/discovery.py` | Event velocity escalation threshold |
| `FLUX_MIN_VELOCITY_COUNT` | `3` | `flux/processors/discovery.py` | Minimum event count for velocity scoring |
| `FORAGE_PRIORITY_KEYWORDS` | `""` | `rss_collector` | Comma-separated keywords to flag signals as priority |
| `FORAGE_FIRMS_FEED` | `"MODIS_NRT"` | `firms_collector` | Active fire feed: `MODIS_NRT` or `VIIRS_SNPP` |
| `FORAGE_FIRMS_DAYS` | `1` | `firms_collector` | Lookback window (days) |
| `FORAGE_FIRMS_MIN_FRP` | `0` | `firms_collector` | Minimum Fire Radiative Power threshold |
| `FORAGE_USGS_FEED` | `"2.5_day"` | `usgs_collector` | USGS earthquake feed slug |
| `FORAGE_USGS_MIN_MAG` | `2.5` | `usgs_collector` | Minimum magnitude to collect |
| `FORAGE_USGS_PRIORITY_MAG` | `6.0` | `usgs_collector` | Minimum magnitude to flag as priority |
| `NDBC_STATIONS` | `""` | `ndbc_collector` | Comma-separated NDBC station IDs (e.g. `"41049,13008"`) |

---

## 5. De Facto Required Packages

These are listed as optional in code (try/except) but the pipeline is non-functional without them. Treat as hard requirements.

| Package | Why de facto required |
|---|---|
| `spacy` + `en_core_web_sm` | NER processor — entity extraction is central to actor materialisation |
| `requests` | X pulse collector exits with `sys.exit(1)` if missing |
| `bleach` | Security sanitizer raises `ImportError` at module load — blocks all ingestion |

---

## 6. Changelog

| Date | Change |
|---|---|
| 2026-06-04 | Initial document. Added `LAWS_AFRICA_TOKEN` as PENDING following BI-196 gazette collector build (bi196_collector v1.2.0). Full codebase audit for all remaining env vars and optional packages. |
