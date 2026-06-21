# Collector Expansion Assessment — Maltego Gap Analysis

**Date:** 2026-06-21
**Context:** Evaluating which new collectors are feasible, necessary, and worth building to close the Maltego "120+ connectors" gap.

**Honest framing:** Maltego's 120+ transforms are mostly API wrappers for paid data services (Pipl, Recorded Future, VirusTotal Pro, etc.). Many require expensive API keys ($500-$5000/year). FORGE's strength is zero-dependency collectors that query free public data. The goal isn't 120 collectors — it's having the RIGHT collectors for SA OSINT.

---

## Currently Built (17 collectors)

| Collector | Source type | Status |
|---|---|---|
| `civic_intel_collector` | SA investigative news (amaBhungane, Daily Maverick, News24, GroundUp, TimesLive) | Active, high-value |
| `civic_intel_collector_us` | US news sources | Active |
| `rss_collector` | Generic RSS feeds | Active |
| `saflii_collector` | SA court records (via Google News RSS proxy) | Active, Tier 2 |
| `cipc_collector` | SA company registry | Active |
| `acled_collector` | Armed Conflict Location & Event Data | Active |
| `gdelt_collector` | Global Database of Events, Language, and Tone | Active |
| `disease_outbreak_collector` | WHO/CDC/ProMED health alerts | Active |
| `earthquake_collector` | USGS earthquake data | Active |
| `usgs_collector` | USGS geological data | Active |
| `firms_collector` | NASA FIRMS satellite fire detection | Active |
| `ndbc_collector` | NOAA marine buoy data | Active |
| `dork_collector` | Google dork search results | Active |
| `pdf_infiltrator` | PDF document extraction/OCR | Active |
| `bi196_collector` | SA Government Gazette | Active |
| `x_pulse` (FLUX) | X/Twitter collection | Active |
| `content_enricher` | URL content fetch + re-score | Active |

## Proposed Collectors — Honest Assessment

### BUILD (feasible, free, high value for SA OSINT)

| Collector | API cost | Effort | Value | Notes |
|---|---|---|---|---|
| `ofac_collector` | Free | 2 hours | HIGH | OFAC SDN sanctions list — downloadable CSV. Cross-reference SA actors against international sanctions. Direct value for financial crime cases. |
| `interpol_collector` | Free | 3 hours | HIGH | INTERPOL Red/Yellow Notices — public search API. Cross-reference actors against wanted persons. |
| `pmg_collector` | Free | 3 hours | HIGH | Parliamentary Monitoring Group (pmg.org.za) — SA parliamentary questions, committee reports, hansard. Gold for corruption/governance cases. |
| `google_news_collector` | Free | 2 hours | MEDIUM | Generalize the SAFLII Google News RSS pattern to search for any actor/case keyword. Already proven in SAFLII collector. |
| `fpb_collector` | Free | 3 hours | HIGH (for CSAM) | Film and Publications Board enforcement actions, classifications, takedown notices. Directly relevant to Case #16. |
| `ncmec_collector` | Free | 2 hours | MEDIUM | NCMEC CyberTipline public reports and statistics. Not individual case data, but trend data for CSAM investigations. |
| `sanctions_collector` | Free | 2 hours | MEDIUM | SA FICA sanctions list + UN Security Council consolidated list. Downloadable, structured data. |

### DON'T BUILD (blocked, expensive, or low value)

| Proposed | Why not |
|---|---|
| `facebook` | Meta API requires app review. Won't be granted for OSINT scraping. Public posts accessible via web search instead. |
| `instagram` | Same Meta API restrictions. |
| `linkedin` | Aggressively blocks scraping. Legal risk (hiQ v LinkedIn precedent). Professional network, not corruption evidence source. |
| `shodan` | Requires API key ($59/mo for developer tier). Only relevant to cyber cases (like the MEGA breach). Build when needed. |
| `censys` | Similar to Shodan — API key required, niche use case. |
| `github` | Free API, but irrelevant to SA corruption/CSAM. Code repos don't contain intelligence signals for this domain. |
| `bing_news` | Redundant with Google News RSS. Same content, different proxy. |
| `ransa` / voter data | Not publicly accessible in SA. |
| `newsapi` | Free tier limited to 100 requests/day and 30-day historical. Civic_intel_collector already covers SA news better. |

### DEFER (feasible but low priority)

| Collector | Notes |
|---|---|
| `cabinet_decisions` | SA cabinet decisions are published on gov.za. Could scrape but low volume (weekly). |
| `treasury_collector` | National Treasury procurement data. High value for tender fraud cases but complex to parse. |
| `sars_collector` | SARS public rulings and penalties. Useful but niche. |

---

## The Real Maltego Gap

Maltego's advantage isn't the NUMBER of transforms — it's the interactive pivot. "Click person → find email → find domain → find server → find other users on that server." That chain requires PAID API services (Pipl for person→email, DomainTools for domain→registrant, Shodan for server→services).

FORGE's approach is different: collect signals from free public sources, score them automatically, and let the analyst connect the dots via the entity graph. The collectors don't need to replicate Maltego's pivot chain — they need to capture the signals that make the SA OSINT landscape visible.

**The honest target is not 120 collectors. It's 25-30 well-chosen collectors that cover:**
1. SA news (done)
2. SA courts (done)
3. SA government/parliament (PMG — to build)
4. International sanctions (OFAC — to build)
5. International wanted (INTERPOL — to build)
6. SA regulatory (FPB — to build for CSAM)
7. Social media (X — done, Facebook/Instagram — blocked)
8. Conflict/crisis (ACLED, GDELT — done)
9. Environmental (FIRMS, USGS, NDBC — done)
10. Health (disease_outbreak — done)

---

## Recommended Build Order (next sprint)

| Priority | Collector | Time | Case value |
|---|---|---|---|
| 1 | `fpb_collector` | 3h | Case #16 (CSAM), future content regulation cases |
| 2 | `interpol_collector` | 3h | Cross-reference any actor against INTERPOL notices |
| 3 | `ofac_collector` | 2h | Financial crime cases (Eskom, tender fraud) |
| 4 | `pmg_collector` | 3h | Parliamentary questions on corruption, governance |
| 5 | `google_news_collector` | 2h | Generalized actor/case news monitoring |
