# Collector Expansion Plan — 8 New Collectors

**Date:** 2026-06-21
**Status:** Pre-implementation
**Target:** 17 → 25 collectors (complete SA OSINT landscape coverage)

Each collector follows the existing FORGE pattern:
- `__manifest__` dict at module level (autodiscovered by `_load_collector_registry()`)
- `INSERT OR IGNORE` on `external_id` (idempotent, safe to re-run)
- Auto-gravity scoring at write time
- Auto-linking to cases via `case_signals` where applicable
- Auto-geo-tagging where location data exists
- `--dry-run` flag for testing
- `try/finally: conn.close()` on all DB connections
- `timeout=60` on all `sqlite3.connect()` calls

---

## Sprint 1 — Highest Priority (Case #16 + financial crime)

### Collector 1: `fpb_collector.py` — Film and Publications Board

**Data source:** fpb.org.za/enforcement/
**Access method:** HTTP fetch + PDF parsing
**Authentication:** None (public website)
**Rate limit:** 1 request per 5 seconds (courteous)

**What it collects:**
- Enforcement committee hearing schedules (PDF download links)
- Enforcement rulings (PDF documents)
- Classification decisions
- Sanctions imposed (fines, suspensions, prosecution referrals)

**Data format:** HTML page listing PDF links. Each PDF contains hearing details — dates, respondent names, charges, outcomes.

**Breakpoints and mitigations:**
| Breakpoint | Risk | Mitigation |
|---|---|---|
| PDFs require parsing | Medium | Use FORGE's existing `pdf_infiltrator.py` pattern — `PyPDF2` or `pdfplumber` if installed, fallback to title-only extraction from the HTML listing |
| Site structure changes | Low | Fetch the enforcement page, extract `<a href="*.pdf">` links with regex — minimal DOM dependency |
| No structured data | Medium | Extract respondent names from PDF titles/filenames, dates from listing, store as signals with `source='fpb'` |

**Schema mapping:**
```
signal.source       = "fpb"
signal.external_id  = "fpb:" + sha1(pdf_url)[:16]
signal.title        = PDF title or hearing description
signal.content      = Extracted text (first 500 chars)
signal.stream       = "CRIME_INTEL"
signal.lat/lng      = -25.7479, 28.2293 (FPB HQ, Pretoria)
metadata_json       = {source_url, hearing_date, respondent_names[], decision_type}
```

**Gravity scoring:**
- Base 0.20 (regulatory body = institutional authority)
- +0.15 if ruling/sanction (not just a scheduled hearing)
- +0.15 if respondent name matches a FORGE actor
- Cap: 0.55

**Effort:** 3 hours
**Dependencies:** None new (stdlib HTTP + regex)

---

### Collector 2: `interpol_collector.py` — INTERPOL Red/Yellow Notices

**Data source:** `ws-public.interpol.int/notices/v1/red` (REST API)
**Access method:** JSON REST API
**Authentication:** None (public API)
**Rate limit:** Unknown — use 1 request per 3 seconds

**What it collects:**
- Red Notices (wanted for prosecution or to serve a sentence)
- Yellow Notices (missing persons) — endpoint: `/notices/v1/yellow`
- UN Notices — endpoint: `/notices/v1/un`

**API confirmed working** (via WebFetch — 6,444 Red Notices returned):
```
GET https://ws-public.interpol.int/notices/v1/red?resultPerPage=20&page=1
Response: {
  total: 6444,
  _embedded: { notices: [
    { forename, name, date_of_birth, nationalities: ["ZA"], entity_id: "2024/12345",
      _links: { self: {href}, images: {href}, thumbnail: {href} }
    }
  ]}
}
```

**Breakpoints and mitigations:**
| Breakpoint | Risk | Mitigation |
|---|---|---|
| API blocked from SA IPs (Akamai CDN) | HIGH | Confirmed: `curl` from SA returns 403. WebFetch (US-based) works. Fallback: scrape the HTML search at interpol.int/How-we-work/Notices/Red-Notices/View-Red-Notices with nationality=ZA filter |
| API returns only summary (no charges) | Low | Follow `_links.self.href` for each notice to get full detail. Rate-limit to 1 per 5 seconds. |
| 160 results max per search | Low | Paginate with `page` parameter. Or filter by `nationality=ZA` to get only SA-relevant notices. |

**SA-specific approach:**
1. Query `?nationality=ZA` to get SA nationals on Red Notice list
2. For each actor in FORGE, query `?forename={first}&name={last}` to check if they have notices
3. Cross-reference FORGE actors against INTERPOL notices

**Schema mapping:**
```
signal.source       = "interpol"
signal.external_id  = "interpol:" + entity_id
signal.title        = "{forename} {name} — INTERPOL Red Notice"
signal.content      = JSON summary of notice details
signal.stream       = "CRIME_INTEL"
signal.lat/lng      = Country centroid based on nationalities[0]
metadata_json       = {entity_id, date_of_birth, nationalities, notice_url, image_url}
```

**Gravity scoring:**
- 0.75 flat (INTERPOL notice = highest-authority law enforcement signal)
- Actor name match in FORGE: create `signal_actors` link automatically

**Effort:** 3 hours (4 if HTML fallback needed)
**Dependencies:** None (stdlib `urllib.request` + `json`)

**Critical note:** The API is GeoIP-blocked from SA. Two solutions:
1. Run the collector from a non-SA environment (CI/CD, cloud function)
2. Use the HTML search page as fallback (same data, different format)
3. Accept that this collector may only work from certain networks

---

### Collector 3: `ofac_collector.py` — US Sanctions (OFAC SDN List)

**Data source:** sanctionssearch.ofac.treas.gov + treasury.gov/ofac/downloads/
**Access method:** CSV download (bulk) or HTML search (per-actor)
**Authentication:** None
**Rate limit:** None for CSV download; 1 per 3 seconds for web search

**What it collects:**
- Specially Designated Nationals list — individuals and entities under US sanctions
- Cross-reference FORGE actors against SDN list
- Programs: terrorism, narcotics, cyber, human rights abuse, corruption

**Two collection strategies:**

**Strategy A (bulk):** Download full SDN CSV, parse locally, match against FORGE actors
- CSV URL: `https://www.treasury.gov/ofac/downloads/sdn.csv`
- ~12,000 entries, ~3MB
- Fields: ent_num, SDN_Name, SDN_Type, Program, Title, Call_Sign, Vess_type, Tonnage, GRT, Vess_flag, Vess_owner, Remarks
- Process: download → parse → for each FORGE actor, check if name appears in SDN_Name column

**Strategy B (per-actor):** Use the search interface at sanctionssearch.ofac.treas.gov
- Query each FORGE actor name individually
- Better precision (fuzzy matching built-in) but slower

**Recommendation:** Strategy A (bulk download + local matching). Download once per pipeline run, cache the CSV, match against all actors. Zero API calls per actor.

**Breakpoints and mitigations:**
| Breakpoint | Risk | Mitigation |
|---|---|---|
| CSV download times out | Low | Retry with longer timeout (60s). Cache locally at `data/sdn.csv`. Only re-download if older than 24h. |
| Name matching false positives | Medium | Use fuzzy matching with minimum threshold (80%). Flag matches as `confidence_score=0.60` requiring analyst review. |
| File format changes | Very low | OFAC CSV format has been stable for 15+ years. |

**Schema mapping:**
```
signal.source       = "ofac"
signal.external_id  = "ofac:" + ent_num
signal.title        = "{SDN_Name} — OFAC SDN ({Program})"
signal.content      = Remarks + Program details
signal.stream       = "CRIME_INTEL"
signal.lat/lng      = None (country-level from Remarks parsing)
metadata_json       = {ent_num, sdn_type, program, remarks, match_actor_id, match_score}
```

**Gravity scoring:**
- 0.70 (OFAC = US federal law enforcement authority)
- +0.10 if actor name exact match (vs fuzzy)
- Cap: 0.85

**Effort:** 2 hours
**Dependencies:** None (stdlib `csv` + `urllib.request`)

---

## Sprint 2 — Governance and Parliamentary Intelligence

### Collector 4: `pmg_collector.py` — Parliamentary Monitoring Group

**Data source:** api.pmg.org.za (REST API)
**Access method:** JSON REST API
**Authentication:** None (public, confirmed working)
**Rate limit:** Unspecified — use 1 per 2 seconds

**API confirmed working:** 34,669 committee meetings in the database.
```
GET https://api.pmg.org.za/committee-meeting/?format=json&page_size=20
Response: {
  count: 34669,
  next: "...&page=2",
  results: [{
    id, date, title, type: "committee-meeting",
    summary, committee: { id, name, house: { name, sphere } },
    url
  }]
}
```

**Additional endpoints to explore:**
- `/bill/` — Bills before Parliament
- `/question_reply/` — Parliamentary questions and minister replies
- `/hansard/` — Parliamentary debates (Hansard)
- `/tabled_committee_report/` — Committee reports

**What it collects:**
- Committee meetings discussing corruption, governance, crime (keyword-filtered)
- Parliamentary questions about actors in FORGE cases
- Committee reports on entities under investigation

**Collection strategy:**
1. On each run, query `/committee-meeting/?format=json&page_size=50` with keyword filter
2. Keywords derived from FORGE case names + actor names
3. For each matching meeting, store as a signal with committee context
4. Auto-link to cases where actor names appear in the meeting title/summary

**Breakpoints and mitigations:**
| Breakpoint | Risk | Mitigation |
|---|---|---|
| API rate-limited or blocked | Low | API is public and has served 34K+ records. Use modest page_size (20-50). |
| Keyword matching too broad | Medium | Filter by committee type: focus on Security & Justice, Finance, SCOPA, Public Accounts, and Police Portfolio committees. |
| Large result sets | Low | Paginate. Only process meetings from last 90 days on each run. |

**Schema mapping:**
```
signal.source       = "pmg"
signal.external_id  = "pmg:" + str(meeting_id)
signal.title        = meeting title
signal.content      = summary (HTML stripped)
signal.stream       = "INFRASTRUCTURE" (governance) or "CRIME_INTEL" (justice committees)
signal.lat/lng      = -33.9258, 18.4232 (Parliament, Cape Town) or -25.7479, 28.1876 (NCOP, Pretoria)
metadata_json       = {meeting_id, committee_name, house, date, pmg_url}
```

**Gravity scoring:**
- Base 0.25 (parliamentary proceedings = institutional record)
- +0.15 if title contains a FORGE actor name
- +0.10 if committee is Security & Justice, SCOPA, or Police Portfolio
- Cap: 0.55

**Effort:** 3 hours
**Dependencies:** None (stdlib `urllib.request` + `json`)

---

### Collector 5: `google_news_collector.py` — Generalized News Monitor

**Data source:** Google News RSS (same pattern as SAFLII collector)
**Access method:** RSS XML
**Authentication:** None
**Rate limit:** 1 per 3 seconds

**What it does:** Generalize the SAFLII collector's proven Google News RSS pattern to search for ANY actor or case keyword — not just `site:saflii.org`.

**The existing pattern (from saflii_collector.py):**
```
https://news.google.com/rss/search?q={query}&hl=en-ZA&gl=ZA&ceid=ZA:en
```

Remove the `site:saflii.org` prefix → search for actor names across ALL SA news sources.

**Collection strategy:**
1. Load actors from active cases (same as SAFLII collector)
2. For each actor, query Google News RSS without site filter
3. Filter results to SA sources (iol.co.za, news24.com, timeslive.co.za, dailymaverick.co.za, etc.)
4. Dedup against existing signals via `external_id = "gnews:" + sha1(url)[:16]`

**Breakpoints and mitigations:**
| Breakpoint | Risk | Mitigation |
|---|---|---|
| Overlap with civic_intel_collector | HIGH | Dedup by URL. civic_intel_collector runs first and claims signals by external_id. google_news_collector only picks up what civic_intel missed. |
| Google rate-limits RSS | Medium | Max 15 actors per run, 3-second delay. Same limits as SAFLII collector. |
| Non-SA results | Low | Filter by domain whitelist OR by `ceid=ZA:en` (Google News region filter). |

**Effort:** 2 hours (90% reuse from saflii_collector pattern)
**Dependencies:** None

---

## Sprint 3 — Financial Intelligence and Regulatory

### Collector 6: `treasury_collector.py` — SA National Treasury Procurement

**Data source:** etenders.treasury.gov.za (if accessible) or treasury.gov.za tender bulletins
**Access method:** HTML scrape or PDF download
**Authentication:** None
**Rate limit:** 1 per 5 seconds

**Status:** The eTenders portal returned 404 on direct URL test. May have moved or restructured. Alternative: National Treasury publishes quarterly procurement bulletins as PDFs.

**What it collects:**
- Awarded tenders above R500,000
- Contractor names, contract values, department names
- Cross-reference against FORGE actors (especially for tender fraud cases: Matlala, Eskom)

**Breakpoints and mitigations:**
| Breakpoint | Risk | Mitigation |
|---|---|---|
| Portal moved or restructured | HIGH | Need to re-discover the current URL. If PDF-only, use pdf_infiltrator pattern. |
| Data is in PDF tables | Medium | Parse with `tabula-py` or regex extraction from text. |
| Large volume | Low | Filter by keyword (actor names) at extraction time. |

**Effort:** 4 hours (includes discovery of current data location)
**Dependencies:** May need `tabula-py` for PDF table extraction (optional — fallback to regex)

---

### Collector 7: `sanctions_sa_collector.py` — SA FIC/FICA + UN Sanctions

**Data source:** Financial Intelligence Centre (FIC) targeted financial sanctions list + UN Security Council consolidated list
**Access method:** FIC website (fic.gov.za) + UN XML download
**Authentication:** None
**Rate limit:** N/A (bulk downloads)

**What it collects:**
- SA-designated sanctioned individuals and entities (terror financing, money laundering)
- UN Security Council sanctions (consolidated list — downloadable XML)
- Cross-reference FORGE actors against both lists

**UN consolidated list:**
```
https://scsanctions.un.org/resources/xml/en/consolidated.xml
```
- Well-structured XML with individual/entity records
- Fields: name, aliases, date_of_birth, nationality, designation_date, narrative

**FIC status:** The fic.gov.za targeted financial sanctions page returned 404. The list may be published under a different URL or require FICA portal access.

**Breakpoints and mitigations:**
| Breakpoint | Risk | Mitigation |
|---|---|---|
| FIC page moved | Medium | Fall back to UN consolidated list only (covers most of the same designations). Search for updated FIC URL. |
| UN XML large file | Low | ~5MB. Download once per run, cache locally. Parse with `xml.etree.ElementTree` (stdlib). |
| Name matching complexity | Medium | Same fuzzy matching approach as OFAC collector. |

**Effort:** 2 hours (UN list) + 1 hour (FIC if accessible)
**Dependencies:** None (stdlib XML parser)

---

### Collector 8: `courts_roll_collector.py` — SA Court Daily Rolls

**Data source:** High Court websites publishing daily rolls as PDFs
**Access method:** HTTP fetch + PDF parsing
**Authentication:** None
**Rate limit:** 1 per 5 seconds

**Courts with public rolls:**
- Gauteng Division, Pretoria: judiciary.org.za or justice.gov.za
- Gauteng Division, Johannesburg: same
- Western Cape Division, Cape Town: westerncape.gov.za/judiciary
- KwaZulu-Natal: kzn.gov.za

**What it collects:**
- Who is appearing in court tomorrow/this week
- Case numbers, court room assignments, presiding officers
- Cross-reference against FORGE actors

**This is the only PREDICTIVE collector.** All others capture what HAS happened. Court rolls capture what's ABOUT to happen.

**Breakpoints and mitigations:**
| Breakpoint | Risk | Mitigation |
|---|---|---|
| Rolls published as PDFs | HIGH | PDF parsing required. Rolls are typically simple text tables — regex extraction viable. |
| Different courts, different formats | HIGH | Each court publishes rolls differently. Start with ONE court (Gauteng/Pretoria — most relevant to FORGE cases). Add others incrementally. |
| Rolls change daily | Medium | Run daily. Cache previous rolls to detect changes. |
| Court website downtime | Medium | Retry with 24h grace. Court rolls are time-sensitive — yesterday's roll has no intelligence value. |

**Effort:** 4 hours (Gauteng Pretoria only — add other courts at 1-2 hours each)
**Dependencies:** May need `pdfplumber` for structured PDF table extraction

---

## Implementation Order

| Sprint | Collector | Effort | API status | Risk |
|---|---|---|---|---|
| **1** | `interpol_collector` | 3h | API confirmed, GeoIP-blocked from SA | Medium |
| **1** | `ofac_collector` | 2h | CSV download confirmed | Low |
| **1** | `fpb_collector` | 3h | Website confirmed, PDF-based | Medium |
| **2** | `pmg_collector` | 3h | API confirmed (34K records) | Low |
| **2** | `google_news_collector` | 2h | RSS confirmed (reuse SAFLII pattern) | Low |
| **3** | `sanctions_sa_collector` | 3h | UN XML confirmed, FIC page missing | Medium |
| **3** | `treasury_collector` | 4h | Portal location uncertain | High |
| **3** | `courts_roll_collector` | 4h | PDF parsing, multi-format | High |

**Total estimated effort:** 24 hours across 3 sprints

---

## What Breaks and How to Prevent It

### Database contention
**Risk:** 8 new collectors all writing to `database.db` concurrently.
**Mitigation:** All collectors use `timeout=60` on `sqlite3.connect()`. WAL mode (already enabled) allows concurrent reads during writes. Collectors run sequentially via `mega_ingest.py`, not in parallel.

### Signal volume explosion
**Risk:** 8 new collectors could generate thousands of signals, overwhelming the timeline.
**Mitigation:** The article gate in `_build_timeline_data()` already prevents unreviewed signals from appearing on the public timeline. New collector signals need analyst articles before they surface. Case detail pages show all signals regardless.

### Gravity score inflation
**Risk:** New collectors auto-scoring at 0.60-0.75 could dilute the significance of existing high-gravity signals.
**Mitigation:** Cap all new collectors at 0.55 except INTERPOL (0.75 — law enforcement authority) and OFAC (0.70 — US federal authority). Analyst can manually boost post-review.

### Collector registry bloat
**Risk:** 25 collectors in `forage/collectors/` makes the registry harder to manage.
**Mitigation:** Group by category in `mega_ingest.py` run order. Add a `category` field to `__manifest__` (legal, financial, governance, social, environmental) for future filtering.

### External API changes
**Risk:** Any external API can change without notice.
**Mitigation:** Every collector has `try/except` around the fetch. Failure logs a warning and returns `{signals_written: 0}`. The pipeline never crashes from a single collector failure — this is existing architecture (`subprocess.Popen` isolation).

---

## Post-Expansion Architecture

```
forage/collectors/ (25 collectors)
├── SA News & Media (3)
│   ├── civic_intel_collector.py      ← SA investigative news
│   ├── civic_intel_collector_us.py   ← US news
│   └── google_news_collector.py      ← Generalized actor news monitoring [NEW]
├── Legal & Courts (3)
│   ├── saflii_collector.py           ← SA court records (Tier 2)
│   ├── fpb_collector.py              ← Film & Publications Board [NEW]
│   └── courts_roll_collector.py      ← Daily court rolls (predictive) [NEW]
├── Government & Governance (3)
│   ├── bi196_collector.py            ← SA Government Gazette
│   ├── pmg_collector.py              ← Parliamentary Monitoring Group [NEW]
│   └── cipc_collector.py             ← Company registry
├── Financial & Sanctions (3)
│   ├── ofac_collector.py             ← US OFAC SDN list [NEW]
│   ├── sanctions_sa_collector.py     ← SA FIC + UN sanctions [NEW]
│   └── treasury_collector.py         ← SA National Treasury procurement [NEW]
├── International (2)
│   ├── interpol_collector.py         ← INTERPOL Red/Yellow Notices [NEW]
│   ├── acled_collector.py            ← Armed Conflict Location Data
├── Environmental & Crisis (4)
│   ├── gdelt_collector.py            ← Global events
│   ├── earthquake_collector.py       ← USGS seismic
│   ├── firms_collector.py            ← NASA satellite fire
│   └── ndbc_collector.py             ← Marine buoy
├── Health (1)
│   └── disease_outbreak_collector.py ← WHO/CDC/ProMED
├── Technical (3)
│   ├── rss_collector.py              ← Generic RSS
│   ├── dork_collector.py             ← Google dork search
│   └── pdf_infiltrator.py           ← PDF document extraction
└── FLUX SOCINT (1)
    └── x_pulse.py                    ← X/Twitter
```
