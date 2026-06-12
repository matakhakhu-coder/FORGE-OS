# FORGE × ZA-DIVERGENT — Gemini Context Handoff
**Classification:** INTERNAL WORKING DOCUMENT  
**Purpose:** Onboard Gemini as planning/articulation layer. Claude executes directives; Gemini plans and refines them.  
**Date:** 2026-06-08

---

## 1. Who You Are in This Setup

You are the **articulation layer** between the developer and Claude Code. The workflow is:

```
Developer <-> Gemini (planning, design, feature scoping, critique)
                    |
                    v  directive prompt
              Claude Code (execution, file edits, DB ops, deploy)
```

When the developer brings an idea to you, your output should be a **precise directive prompt** Claude can execute without ambiguity — file paths named, schema confirmed, edge cases flagged, expected outputs stated.

---

## 2. FORGE — The Core System

### Identity
**FORGE** (Foundational Open Research & Graph Engine) is a local-first, analyst-grade OSINT intelligence operating system. Primary focus: South African domestic & regional open-source intelligence. It is NOT a dashboard — it is a system that builds a living intelligence graph from open-source signals.

### Stack
- **Runtime:** Python 3.13 · Flask · SQLite (WAL mode, single file `database.db`)
- **Frontend:** Jinja2 · Leaflet.js · D3.js · Chart.js · HTMX
- **Hard constraint:** Zero Node.js at build or runtime. Zero npm. Zero webpack. Ever.
- **Serves:** `localhost:5000`

### What It Does
1. **Collects** raw signals from collectors in `forage/collectors/` (RSS, GDELT, ACLED, PDFs, SAFLII, CIPC) and `flux/collectors/x_pulse.py` (X/Twitter SOCINT)
2. **Ingests** signals through `core/pipeline/ingest.py` → NER extraction, gravity scoring, entity materialisation, relationship extraction, case auto-pin, escalation check
3. **Stores** everything in `database.db` — tables: `signals`, `actors`, `events`, `cases`, `entity_relationships`, `case_signals`, `case_actors`, `socint_signals`, etc.
4. **Visualises** via Flask routes: Intel Graph, Actor Dossiers, Case Board, Signal Monitor, Map (Leaflet), Document Briefs

### Key Schema Facts
```
signals:       external_id (dedup key), title, summary, gravity_score, stream, timestamp, source, publish_to_site (bool)
actors:        actor_id, name, type, confidence_score, description, source_type
events:        title, stream, gravity_score
cases:         case_id, title, description, hypothesis, status
entity_relationships: subject_actor_id, object_actor_id, relation_type, confidence, extraction_method
case_signals:  case_id → signal_id
case_actors:   case_id → actor_id
articles:      slug (unique), title, subtitle, body_md, published_at, publish_to_site
```

### Gravity Score
Two scorers exist (different purposes):
- `forage/engines/gravity_engine.py → score_signal()`: urgency/importance ∈ [0.0, 1.0] written to `signals.gravity_score`
- `core/gravity.py → score_item()`: CT-1 Contextual Tunneling — relevance to analyst's active case (actor ×0.50, location ×0.30, keyword ×0.20)

### Current Stable: `Stable 1.1.3`
- 1,130+ signals scored
- 5 active cases (Cases 7–10 are SA-relevant: Limpopo fraud, Magaqa murder, Eskom diesel, KZN HAWKS)
- 13 actors in relationship graph, 19 edges
- Open high-priority debt: spaCy not tuned for SA entities (HAWKS, SIU, NPA → MISC), 6,458 PDFs need OCR pass, SAFLII bridge gap on Case Alpha (CoE = 0.28)

---

## 3. ZA-DIVERGENT — The Public Site

### What It Is
ZA-DIVERGENT is a **static public intelligence bulletin** generated from FORGE's local DB. It is NOT a live app — it is a snapshot baked at publish time. The DB never leaves the local machine.

**Live URL:** https://forge-os-alpha.vercel.app  
**Stack:** Static HTML (Jinja2 templates → `dist/`), Cytoscape.js, Leaflet.js, Chart.js  
**Deploy:** `python tools/publish.py --deploy` → git commit dist/ → git push → Vercel deploy hook POST → Vercel deploys in ~30s

### Architecture
```
publisher/
  templates/          ← Jinja2 HTML (UNTRACKED in git — local only)
    base.html         ← navigation, design system tokens
    timeline.html     ← home page (index.html): chronological signal + article cards
    cases.html        ← case grid with CoE gauges
    case_detail.html  ← per-case: signals, actors, CoE, print brief
    graph.html        ← Cytoscape.js intel graph (pre-baked JSON)
    map.html          ← Leaflet.js with jitter for co-located markers
    article.html      ← analyst article reader
  static/
    css/site.css      ← design system (UNTRACKED)
    js/site.js        ← (UNTRACKED)

tools/publish.py      ← THE publisher: queries DB → renders templates → dist/
dist/                 ← committed to git (what Vercel serves)
vercel.json           ← routes dist/** as static
```

### Design System
```css
/* Colours */
--bg-void:      #05080b   /* canvas black */
--bg-base:      #080d12
--bg-surface:   #0c1219
--text-primary: #cfdce8
--text-secondary: #7a95aa
--accent:       #c8943a   /* gold */
--accent-bright: #e0aa4a

/* Fonts */
--font-display: 'Syne'         /* headers */
--font-mono:    'IBM Plex Mono' /* data, labels */
--font-body:    'IBM Plex Sans' /* body copy */
```

Aesthetic: **classified document · dark-first · editorial precision**. Not a consumer site. Feels like a restricted intelligence brief.

### Current Pages & Content

| Page | File | Content |
|---|---|---|
| Timeline (home) | index.html | 15 items — signals + articles, year/month grouped |
| Cases | cases.html | 4 SA cases (7=Limpopo fraud, 8=Magaqa murder, 9=Eskom diesel, 10=KZN HAWKS) |
| Case detail | cases/[slug].html | CoE scorecard, signals, actors, print-to-PDF brief |
| Graph | graph.html | 13 actors, 10+ edges, Cytoscape.js Tokyo Night palette |
| Map | map.html | 8 geo-tagged signals, Pretoria jitter |
| Articles | articles/[slug].html | 7 analyst-written articles |

**8 published signals.** All wired to articles via `SIGNAL_ARTICLE_MAP` in `tools/publish.py`.

**7 articles:**
1. `madlanga-hawks-limpopo-municipal-fraud`
2. `emfuleni-martha-rantsofu-murder-investigation`
3. `operation-magaqa-anatomy-of-a-cover-up`
4. `kzn-hawks-three-signals-one-pattern`
5. `saps-police-minister-crime-syndicate-allegation`
6. `saps-murder-investigation-sabotage-pattern`
7. `eskom-r21bn-diesel-fraud-mavuso`

### Intel Graph — Current State (post-2026-06-08 redesign)
- **Node style:** Dark fill + bright border ring per actor type (FORGE design language)
- **Node types/colours:** person=#e0803a, institution=#3a6ed4, government=#40a060, political_party=#c040a0, media=#40b0c0
- **Edge colours (Tokyo Night):** INVESTIGATES/INVESTIGATED_BY=#f7768e, LEADS=#7aa2f7, CONTRACTED/EMPLOYED_BY=#9ece6a, FUNDED_BY=#e0803a, AFFILIATED_WITH=#bb9af7
- **Labels:** Always-visible, 8px IBM Plex Mono, roundrectangle pill background (opacity 0.6), dims at low zoom (`min-zoomed-font-size: 6`)
- **Initial zoom:** Fits all nodes with 80px padding, caps at 0.85 zoom

### Deploy Mechanics
```python
# In tools/publish.py
VERCEL_DEPLOY_HOOK = "https://api.vercel.com/v1/integrations/deploy/prj_ibD3AwjGwVKVA5tA43Evg93d5Xzf/QTHKub0Cbl"
PUBLISHED_CASE_IDS = (7, 8, 9, 10)
```
Every `--deploy` run: builds dist/ → git commit → git push → POST to deploy hook → Vercel deploys.

### Known Gaps / Roadmap

| Priority | Feature | Notes |
|---|---|---|
| HIGH | **Watchlists** | localStorage star on any actor/signal. No backend needed — pure JS. |
| HIGH | **RSS feed.xml** | `dist/feed.xml` for alert subscriptions. Add to publish.py. |
| MEDIUM | **Telegram push alerts** | publish.py fires Telegram bot message on new signals via Bot API |
| MEDIUM | **publish_to_site column** | `PUBLISHED_CASE_IDS` is currently hardcoded (7,8,9,10). Should be a DB flag. |
| LOW | **More signals published** | Only 8 of 1,130+ signals published. Need curation pass. |
| LOW | **Actor detail pages** | `actors/[slug].html` — dossier-style, links from Graph panel and Timeline |

---

## 4. How to Write Directives for Claude

Claude Code operates on the FORGE repo at `C:\Users\matam\Projects\FORGE`. It has full file read/write/exec access.

**A good directive includes:**
1. **What to change** — file path, function name, template name
2. **Why** — enough context that Claude can resolve ambiguities correctly
3. **Expected output** — what the result should look like / what command to run to verify
4. **Constraints** — don't break X, don't touch Y, must be idempotent

**A bad directive:** "Add watchlists to the site"  
**A good directive:** "Add a localStorage-based watchlist to ZA-DIVERGENT. A star icon (☆/★) should appear on each actor node in `graph.html` panel and on each timeline card in `timeline.html`. Clicking it toggles the actor/signal in `localStorage['zad_watchlist']`. A new `watchlist.html` page should list all watched items with remove buttons. Wire it into `base.html` nav after Map. Add the watchlist page to `_build_dist()` in `tools/publish.py`. No backend — all client-side JS."

**Key architectural rules Claude enforces:**
- Zero Node.js/npm
- `from __future__ import annotations` always line 2
- `datetime.now(timezone.utc)` never `utcnow()`
- All `sqlite3.connect()` calls need `timeout=60` and `try/finally conn.close()`
- Schema changes need `python app.py --migrate` after
- `publisher/` templates are UNTRACKED in git — only `dist/` is committed

---

## 5. Minimum Files to Send Gemini for Full Context

If you want to give Gemini raw files instead of this document, the **3-file minimum** is:

| File | What it covers |
|---|---|
| `CLAUDE.md` | FORGE architecture, stack, signal lifecycle, FMS, all architectural decisions, critical code rules, tech debt ledger (~80% of FORGE context) |
| `tools/publish.py` | ZA-DIVERGENT publisher — all build logic, SIGNAL_ARTICLE_MAP, template calls, deploy hook, PUBLISHED_CASE_IDS |
| `memory/project_zadivergent_state.md` | ZA-DIVERGENT state snapshot — pages, content, gaps, deploy architecture |

**Add these for deeper fidelity:**

| File | What it adds |
|---|---|
| `FORGE_OS_MANIFEST.md` | Strategic vision, collector architecture, FLUX SOCINT detail |
| `publisher/templates/base.html` | Exact nav structure, design tokens, font stack |
| `publisher/static/css/site.css` | Full design system tokens |
| `publisher/templates/timeline.html` | How timeline cards are structured |
| `publisher/templates/case_detail.html` | Case brief layout, CoE scoring, print CSS |
| `publisher/templates/graph.html` | Graph JS, color maps, hover/panel behaviour |

**You don't need to send:** `database.db`, migration scripts, `forage/collectors/`, Flask route code, `forge_modules/` (unless building new FORGE features, not site features).

---

## 6. Session Summary (what was built 2026-06-08)

This session built out ZA-DIVERGENT from skeleton to a viable public product:

1. **Graph** — Cytoscape.js, Tokyo Night palette, dark-fill+border-ring nodes, always-visible labels with pill backgrounds, correct edge colors for all 9 relation types, 0.82 opacity edges
2. **Map** — 8-dot jitter algorithm (all signals share Pretoria coords)
3. **Cases** — grid of 4 SA cases with CoE gauge; per-case detail pages with print-to-PDF
4. **Timeline** — every card has "Read analysis →" wired via SIGNAL_ARTICLE_MAP
5. **4 new analyst articles** seeded directly into DB via `tools/_seed_articles_2.py`
6. **Deploy pipeline** — Vercel deploy hook hardwired into publish.py; one command deploys
7. **Feed removed** — Timeline is now the home page (index.html)

---

*This document was generated from session context to enable Gemini onboarding. Keep in sync with `memory/project_zadivergent_state.md` and `CLAUDE.md`.*
