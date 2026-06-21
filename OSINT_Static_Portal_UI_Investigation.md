# ZA-DIVERGENT UI/UX Implementation Specification

Sprint-ready design spec for the ZA-DIVERGENT static intelligence bulletin.
Every recommendation targets a named template file and uses the existing design token system (`site.css :root`).
All patterns are implementable in static HTML + vanilla JS + CDN libraries. No Node.js. No npm. No backend.

---

## 1. Comparator Landscape

Structural audit of five investigative portals, distilled to patterns that transfer to a static OSINT bulletin.

### 1.1 ICIJ Offshore Leaks Database

**Feed/Landing:** Search-first — a single input queries a Neo4j-backed graph exported to serialized CSV. No editorial feed; the landing page is a search box with dataset tabs (Pandora Papers, Panama Papers, etc.).

**Entity Profile:** Graph-to-sidebar pivot. Clicking an entity node opens a slide-out sidebar displaying name, ICIJ ID, country code, and linked nodes — without navigating away from the graph canvas. This is the key pattern: *context without navigation loss*.

**Case/Investigation:** No case pages — ICIJ organizes by dataset (leak), not by investigation thread. Cross-referencing is entity-to-entity via relationship edges.

**Interactivity:** W3C-compliant reconciliation API for OpenRefine integration. Client-side graph rendering. The public interface is a static export of pre-computed graph data.

**Transferable pattern:** The sidebar context panel on node click. ZA-DIVERGENT's `graph.html` already implements this (`#ig-panel`). Strengthen it with a "View full profile →" link to the actor's static HTML page.

### 1.2 OCCRP Aleph

**Feed/Landing:** High-density data tables using the FollowTheMoney (FTM) schema. Entities inherit properties from a class hierarchy (Company → LegalEntity). The landing page is a search + filter interface with dataset overlays.

**Entity Profile:** Multi-valued attribute directories — alternate names, registration numbers, cross-referenced matches from other datasets displayed in tabular format.

**Case/Investigation:** Dataset-scoped — each dataset (leak, registry) is a collection. Investigations are implicit in the grouping of entities across datasets.

**Interactivity:** `alephclient` CLI for export. Client-side filtering and tabular display. The web interface is a React SPA backed by an API — the interactivity patterns require a backend and do not transfer directly.

**Transferable pattern:** The attribute-directory layout for entity profiles. ZA-DIVERGENT's actor profile infobox (`ap-infobox`) already follows this pattern. Consider adding alternate names/aliases as a row if FORGE's `actors.socint_profile` JSON contains them.

### 1.3 Bellingcat

**Feed/Landing:** Editorial magazine layout — article cards with featured images, tags, and date. No signal feed; content is longform articles organized by tag and region.

**Entity Profile:** No dedicated entity pages. Actors are referenced within articles.

**Case/Investigation:** Articles serve as investigation pages. Methodology is embedded inline — verification steps shown as numbered sections with annotated screenshots and coordinate grids.

**Interactivity:** Open-source Auto Archiver tool for evidence preservation. The website itself is a standard CMS (WordPress). Verification guidelines are static content.

**Transferable pattern:** Inline methodology disclosure. Bellingcat's verification sections — showing *how* evidence was evaluated — map directly to ZA-DIVERGENT's "Methodology" section on actor profiles. Expand this pattern to case detail pages.

### 1.4 The Sentry

**Feed/Landing:** Report-driven — the landing page lists published investigations as PDF-linked cards with titles, dates, and regional tags.

**Entity Profile:** Relational database-backed profiles. The organization transitioned from manual Word documents to a graph database specifically because keeping entity profiles synchronized across hundreds of pages was unsustainable with flat text.

**Case/Investigation:** Split layout — narrative text on the left, interactive network diagram on the right. As the user scrolls through the report, the network diagram highlights the specific entities being discussed. This scroll-synchronized narrative-to-graph pattern is the most distinctive UI innovation in the comparator set.

**Interactivity:** Custom graph visualizations embedded in reports. No public-facing search or entity lookup tool.

**Transferable pattern:** Scroll-synchronized narrative + graph. This is a future enhancement for ZA-DIVERGENT case detail pages — embed a mini Cytoscape graph in the case detail sidebar that highlights actors as the reader scrolls through the evidence list.

### 1.5 Lighthouse Reports

**Feed/Landing:** Investigation catalog — cards with investigation titles, descriptions, and methodology links. Dual-track content: narrative investigation + technical methodology guide as separate but linked pages.

**Case/Investigation:** The narrative track tells the human story; the methodology track explains the algorithmic/data analysis. This separation keeps the public-facing article readable while providing full technical detail for expert readers.

**Transferable pattern:** The dual-track model maps to ZA-DIVERGENT's existing article + case detail separation. Articles are the narrative track; case detail pages are the evidence/methodology track. Ensure cross-linking is prominent in both directions.

### 1.6 Convergent Patterns (3+ portals)

| Pattern | Portals | ZA-DIVERGENT mapping |
|---|---|---|
| Context sidebar on entity interaction | ICIJ, The Sentry, OCCRP | `graph.html` → `#ig-panel` (exists) |
| Methodology disclosure near content | Bellingcat, Lighthouse, The Sentry | Actor profile methodology section (exists); add to case detail |
| Entity cross-referencing via relationships | ICIJ, OCCRP, The Sentry | Actor → case → signal chain (exists in templates) |
| Graph as navigational interface, not decoration | ICIJ, The Sentry | `graph.html` → add click-through to actor profiles |
| Chronological evidence as primary case structure | Bellingcat, The Sentry, Lighthouse | Case detail signal list (exists); upgrade to timeline rail |

---

## 2. Cross-Page Features

### 2.1 Gravity Score: Significance Band System

**Problem:** The current label "G 0.47" is misread by non-analyst readers as "47% certain" or "47% true." Research on fact-checking label comprehension (CHI 2025) found only 36.2% accuracy when non-specialists interpret numeric confidence labels. The label must communicate *editorial importance*, not probability.

**Source research:** ClaimBuster (UT Arlington) frames its scores as "check-worthiness" rather than truth/confidence. The Trust Project's "Type of Work" indicator teaches labeling the *category of measurement*, not just the number. Full Fact (UK) confirmed that numeric scores require calibrated explanations while simple named labels are "clear and instantly understood."

**Implementation — replace raw score with named bands:**

| Score range | Label | CSS class | Color token |
|---|---|---|---|
| 0.00 – 0.19 | Routine | `sig-routine` | `var(--text-dim)` |
| 0.20 – 0.34 | Notable | `sig-notable` | `var(--infra)` |
| 0.35 – 0.54 | Significant | `sig-significant` | `var(--accent)` |
| 0.55 – 0.74 | High Importance | `sig-high` | `var(--crime)` |
| 0.75 – 1.00 | Critical | `sig-critical` | `#e74c3c` |

Note: The 0.35 threshold aligns with FORGE's escalation engine (MONITOR ≥ 0.35, ESCALATE ≥ 0.55).

**Display format:** `SIGNIFICANCE: Notable` with the filled bar beneath, replacing `G 0.47`.

**Tooltip on hover:** "This score reflects editorial weight assigned by the FORGE analytical pipeline based on source reliability, recency, and corroboration." Addresses the Trust Project's "Methods" indicator.

**Template changes:**

In `publisher/templates/index.html` — replace the gravity display block:
```html
{# Current: G 0.47 with bar #}
{# New: Named significance band with bar #}
{% set sig_label = 'Critical' if item.gravity_score >= 0.75
   else ('High Importance' if item.gravity_score >= 0.55
   else ('Significant' if item.gravity_score >= 0.35
   else ('Notable' if item.gravity_score >= 0.20
   else 'Routine'))) %}
{% set sig_cls = 'sig-critical' if item.gravity_score >= 0.75
   else ('sig-high' if item.gravity_score >= 0.55
   else ('sig-significant' if item.gravity_score >= 0.35
   else ('sig-notable' if item.gravity_score >= 0.20
   else 'sig-routine'))) %}

<div class="card__gravity {{ sig_cls }}" title="Editorial weight assigned by the FORGE analytical pipeline based on source reliability, recency, and corroboration.">
  <span class="mono card__gravity-label">{{ sig_label }}</span>
  <div class="gravity-bar">
    <div class="gravity-bar__fill gravity-bar__fill--{{ sig_cls }}"
         style="width:{{ item.gravity_pct }}%"></div>
  </div>
</div>
```

**CSS additions to `publisher/static/css/site.css`:**
```css
.card__gravity-label {
  font-size: 0.58rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.sig-routine .card__gravity-label  { color: var(--text-dim); }
.sig-notable .card__gravity-label  { color: var(--infra); }
.sig-significant .card__gravity-label { color: var(--accent); }
.sig-high .card__gravity-label     { color: var(--crime); }
.sig-critical .card__gravity-label { color: #e74c3c; }

.gravity-bar__fill--sig-routine    { background: var(--text-dim); }
.gravity-bar__fill--sig-notable    { background: var(--infra); }
.gravity-bar__fill--sig-significant { background: var(--accent); }
.gravity-bar__fill--sig-high       { background: var(--crime); }
.gravity-bar__fill--sig-critical   { background: #e74c3c; }
```

**Apply the same band system** to: case detail CoE display (rename "Evidence Weight" to "EVIDENCE CONFIDENCE"), case list cards, and map popup gravity badges.

### 2.2 "New Since Last Visit" Indicators

**Research basis:** NNGroup distinguishes between *indicators* (passive, ambient) and *notifications* (active interruptions). For "new since last visit," indicators are correct — non-intrusive visual markers that communicate status without demanding attention. No major journalism portal (Guardian, ProPublica) uses this pattern on their public sites because they have hundreds of daily articles. ZA-DIVERGENT's curated signal set (tens, not hundreds) makes freshness tracking valuable.

**Implementation:**

Store `za_divergent_last_visit` timestamp in localStorage. On page load, compare each signal/article's `published_at` against the stored timestamp. Display a small "NEW" text badge adjacent to the signal date. Update the stored timestamp after a 5-second delay (so the current session's items register as "seen").

**Template change in `publisher/templates/index.html`:**

Add `data-published="{{ item.published_iso }}"` to each card div. The JS reads this attribute.

**JS addition to `publisher/static/js/site.js`:**
```js
(function () {
  var LAST_VISIT_KEY = 'za_divergent_last_visit';
  var prev = localStorage.getItem(LAST_VISIT_KEY);
  var now = Date.now();

  if (prev) {
    var prevTs = parseInt(prev, 10);
    document.querySelectorAll('.js-card[data-published]').forEach(function (el) {
      var pubTs = new Date(el.dataset.published).getTime();
      if (pubTs > prevTs) {
        var badge = document.createElement('span');
        badge.className = 'badge-new';
        badge.textContent = 'NEW';
        var ts = el.querySelector('.card__ts');
        if (ts) ts.appendChild(badge);
      }
    });
  }

  setTimeout(function () {
    localStorage.setItem(LAST_VISIT_KEY, now.toString());
  }, 5000);
})();
```

**CSS:**
```css
.badge-new {
  display: inline-block;
  font-size: 0.5rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  padding: 1px 5px;
  border-radius: var(--r-sm);
  background: var(--accent);
  color: var(--bg-base);
  margin-left: var(--sp-2);
  vertical-align: middle;
}
```

### 2.3 localStorage Watchlist

**Implementation pattern:** Store `za_divergent_watchlist` in localStorage as `{ signals: [...], actors: [...] }`. Each entry stores `{ id, title, type, addedAt, url }` — enough metadata to render a watchlist page without re-querying.

**Toggle element:** Unicode star (☆ empty U+2606, ★ filled U+2605) — no icon library dependency.

**Affected templates:** `index.html` (signal cards), `entities.html` (actor cards), `actor_profile.html` (profile header).

**New page:** `dist/watchlist.html` — reads from localStorage and renders bookmarked items as a linked list. Generated by `tools/publish.py` as a static shell with no DB dependency (all content comes from localStorage at runtime).

**JS module (`publisher/static/js/watchlist.js`):**
```js
var WL_KEY = 'za_divergent_watchlist';

function getWatchlist() {
  try { return JSON.parse(localStorage.getItem(WL_KEY)) || { signals: [], actors: [] }; }
  catch (e) { return { signals: [], actors: [] }; }
}

function toggleWatchlistItem(type, item) {
  var wl = getWatchlist();
  var list = wl[type] || [];
  var idx = -1;
  for (var i = 0; i < list.length; i++) {
    if (list[i].id === item.id) { idx = i; break; }
  }
  if (idx > -1) list.splice(idx, 1);
  else list.push({ id: item.id, title: item.title, type: item.type, addedAt: new Date().toISOString(), url: item.url });
  wl[type] = list;
  localStorage.setItem(WL_KEY, JSON.stringify(wl));
  return idx === -1;
}

function isWatchlisted(type, id) {
  var wl = getWatchlist();
  var list = wl[type] || [];
  for (var i = 0; i < list.length; i++) {
    if (list[i].id === id) return true;
  }
  return false;
}
```

**CSS:**
```css
.wl-toggle {
  cursor: pointer;
  font-size: 1.1rem;
  color: var(--text-dim);
  background: none;
  border: none;
  padding: 2px 4px;
  transition: color var(--t-fast);
}
.wl-toggle.active { color: var(--accent-bright); }
.wl-toggle:hover  { color: var(--accent); }
```

### 2.4 Client-Side Search (MiniSearch)

**Selection rationale:** MiniSearch (~16KB minified) over Lunr.js (larger index, immutable after creation) and Fuse.js (linear scan, degrades on larger datasets). MiniSearch supports dynamic updates and is available via CDN.

**Build step — Python, not Node.js:**

The search index must be built in `tools/publish.py` during the `_build_dist()` phase. Python generates the JSON index file; the browser loads it via CDN MiniSearch.

```python
# In tools/publish.py — add to _build_dist()
import json

def _build_search_index(signals, articles, actors):
    """Build a MiniSearch-compatible JSON index for client-side search."""
    documents = []
    for s in signals:
        documents.append({
            "id": s["signal_id"],
            "title": s["title"],
            "kind": "signal",
            "url": "index.html",  # signals are on the feed page
        })
    for a in articles:
        documents.append({
            "id": a["slug"],
            "title": a["title"],
            "kind": "article",
            "url": f"articles/{a['slug']}.html",
        })
    for act in actors:
        documents.append({
            "id": str(act["actor_id"]),
            "title": act["name"],
            "kind": "actor",
            "url": f"entities/{act['slug']}.html",
        })
    (DIST / "search-index.json").write_text(json.dumps(documents), encoding="utf-8")
```

**Client-side loading:**
```html
<script src="https://cdn.jsdelivr.net/npm/minisearch@7.1.1/dist/umd/index.min.js"
        crossorigin="anonymous"
        referrerpolicy="no-referrer"></script>
```

```js
var searchIndex = null;

fetch('search-index.json')
  .then(function (r) { return r.json(); })
  .then(function (docs) {
    searchIndex = new MiniSearch({
      fields: ['title'],
      storeFields: ['title', 'kind', 'url']
    });
    searchIndex.addAll(docs);
  });

function doSearch(query) {
  if (!searchIndex) return [];
  return searchIndex.search(query, { fuzzy: 0.2, prefix: true });
}
```

Note: The Python build step writes a flat document array, not a pre-built MiniSearch index. The browser-side MiniSearch instance indexes the documents on load. For ZA-DIVERGENT's current scale (~100–500 documents), this indexing takes <50ms and avoids coupling the build step to MiniSearch's internal serialization format.

### 2.5 Print CSS for Intelligence Briefs

**Source:** CSS Paged Media Module Level 3 + Chromium fixed-footer fallback for `counter(pages)` limitation.

**Implementation — add to `publisher/static/css/site.css`:**

```css
@page {
  size: A4 portrait;
  margin: 20mm 20mm 25mm 20mm;
}
@page :left  { margin-left: 25mm; margin-right: 15mm; }
@page :right { margin-left: 15mm; margin-right: 25mm; }
@page :first { margin-top: 40mm; }

@media print {
  html, body {
    background: #fff !important;
    color: #0f172a !important;
    font-family: 'IBM Plex Sans', Georgia, serif;
    font-size: 10.5pt;
    line-height: 1.5;
  }

  .site-header, .site-nav, .class-banner, .site-footer,
  .stream-bar, .ig-toolbar, .map-bar, .bl-filters,
  button, .wl-toggle, .badge-new { display: none !important; }

  main { width: 100% !important; margin: 0 !important; padding: 0 !important; }

  h1, h2, .cd-section { break-before: avoid; }
  h1, h2, h3, h4 { break-after: avoid; page-break-after: avoid; }
  .card, .cd-signal, .ap-event, tr {
    break-inside: avoid;
    page-break-inside: avoid;
  }

  a[href]:after {
    content: " (" attr(href) ")";
    font-size: 8.5pt;
    color: #475569;
    font-family: 'IBM Plex Mono', monospace;
  }
  a[href^="javascript:"]:after,
  a[href^="#"]:after { content: ""; }

  /* Fixed footer fallback — Chromium repeats fixed elements on every printed page */
  .print-footer {
    display: block !important;
    position: fixed;
    bottom: 0; left: 0; right: 0;
    height: 36px;
    border-top: 1px solid #cbd5e1;
    padding-top: 6px;
    background: #fff !important;
    z-index: 9999;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 8pt;
    color: #64748b;
    display: flex;
    justify-content: space-between;
  }
  .print-footer__class { color: #b45309; }
}
```

**Template addition** — add a hidden print footer to `base.html`:
```html
<div class="print-footer" style="display:none;">
  <span class="print-footer__class">OPEN SOURCE INTELLIGENCE — PUBLIC BULLETIN</span>
  <span>ZA-DIVERGENT · FORGE OSINT ENGINE · {{ generated_at }}</span>
</div>
```

### 2.6 Correction and Update Notices

**Research basis:** Bellingcat's editorial standards append corrections at the bottom of articles with date and description. ProPublica's Code of Ethics requires corrections "fully, quickly and ungrudgingly." Industry convention distinguishes three notice types.

**Implementation — three notice types:**

| Type | Placement | Visual | Use case |
|---|---|---|---|
| **Correction** | Top of article/profile, above body | Red left border, red-tinted background | Factual error was present, now fixed |
| **Update** | Top of article/profile, above body | Blue left border, blue-tinted background | New information added, original not wrong |
| **Last updated** | Bottom of every page, minimal | Dim text, top border | Routine timestamp |

**Template pattern (add to `article.html` and `actor_profile.html`):**
```html
{% if notices %}
{% for notice in notices %}
<div class="notice notice--{{ notice.type }}">
  <strong>{{ notice.type | title }} ({{ notice.date }}):</strong> {{ notice.text }}
</div>
{% endfor %}
{% endif %}
```

**CSS:**
```css
.notice {
  padding: var(--sp-3) var(--sp-4);
  margin-bottom: var(--sp-5);
  font-size: 0.82rem;
  line-height: 1.6;
  border-radius: var(--r-sm);
}
.notice strong {
  font-family: var(--font-mono);
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
.notice--correction {
  border-left: 3px solid var(--crime);
  background: var(--crime-bg);
}
.notice--update {
  border-left: 3px solid var(--infra);
  background: var(--infra-bg);
}
.page-last-updated {
  font-family: var(--font-mono);
  font-size: 0.65rem;
  color: var(--text-dim);
  margin-top: var(--sp-6);
  padding-top: var(--sp-3);
  border-top: 1px solid var(--border-dim);
}
```

**Data source:** Add a `notices` list to `tools/publish.py` template context, populated from a `NOTICES` dict in `publish.py` keyed by article slug or actor ID.

---

## 3. Page-by-Page Specification

### 3.1 Timeline / Feed — `publisher/templates/index.html`

**Current state:** Stream-filtered card feed. Pills filter by CRIME_INTEL / INFRASTRUCTURE / PRIORITY / GLOBAL. Signal cards show title, source, stream badge, gravity bar ("G 0.47"), optional summary, optional geo. Article cards show title, author, tags, "Read analysis →" link.

**Changes:**

| Change | Priority | Effort |
|---|---|---|
| Replace "G 0.47" with significance band label | HIGH | Template + CSS |
| Add `data-published` attribute for "new since last visit" | HIGH | Template only |
| Add watchlist star toggle to signal and article cards | MEDIUM | Template + JS |
| Add MiniSearch search input to stream bar | MEDIUM | Template + JS |
| Add watchlist nav link to `base.html` header | LOW | Template only |

### 3.2 Cases List — `publisher/templates/cases.html`

**Current state:** Grid of case cards with name, status badge, description, CoE bar ("Evidence Weight: 0.28"), signal/actor counts, "View case →" link. Stats bar shows totals.

**Changes:**

| Change | Priority | Effort |
|---|---|---|
| Replace CoE raw number with named band (same system as gravity) | HIGH | Template + CSS |
| Rename "Evidence Weight" to "EVIDENCE CONFIDENCE" for consistency | LOW | Template only |

**CoE band mapping** (same thresholds as gravity, but the label column reads differently):

| CoE range | Label |
|---|---|
| 0.00 – 0.19 | Preliminary |
| 0.20 – 0.34 | Developing |
| 0.35 – 0.54 | Established |
| 0.55 – 0.74 | Strong |
| 0.75 – 1.00 | Corroborated |

### 3.3 Case Detail — `publisher/templates/case_detail.html`

**Current state:** Classification banner, case header with status badge, CoE score card, chronological signal evidence list (each with gravity bar), linked actors list, print brief button.

**Changes:**

| Change | Priority | Effort |
|---|---|---|
| Upgrade signal evidence list to vertical timeline rail | HIGH | Template + CSS |
| Add source reliability indicator (3-tier) to each evidence item | HIGH | Template + CSS + publish.py |
| Add methodology section (adapted from actor profile pattern) | MEDIUM | Template only |
| Add correction/update notice slot at top | MEDIUM | Template only |
| Add print footer with classification banner | MEDIUM | Template + CSS |
| Replace CoE raw number with named band | LOW | Template + CSS |

**Evidence timeline rail — CSS:**
```css
.cd-evidence-timeline {
  position: relative;
  padding-left: 28px;
}
.cd-evidence-timeline::before {
  content: '';
  position: absolute;
  left: 9px;
  top: 0; bottom: 0;
  width: 2px;
  background: var(--border-dim);
}
.cd-evidence-item {
  position: relative;
  margin-bottom: var(--sp-5);
  padding-left: var(--sp-5);
}
.cd-evidence-item::before {
  content: '';
  position: absolute;
  left: -23px;
  top: 6px;
  width: 8px; height: 8px;
  border-radius: 50%;
  border: 2px solid var(--border-dim);
  background: var(--bg-base);
}
.cd-evidence-item[data-reliability="verified"]::before {
  background: #27ae60; border-color: #27ae60;
}
.cd-evidence-item[data-reliability="reported"]::before {
  background: var(--accent); border-color: var(--accent);
}
.cd-evidence-item[data-reliability="unverified"]::before {
  background: var(--text-dim); border-color: var(--text-dim);
}
```

**Source reliability — 3-tier model (civilian adaptation of Admiralty Code):**

| Tier | Label | Dot color | Criteria |
|---|---|---|---|
| Verified | Verified Source | Green (`#27ae60`) | Named investigative outlet, court document, official gazette |
| Reported | Reported | Amber (`var(--accent)`) | News wire, single-source media report |
| Unverified | Unverified | Grey (`var(--text-dim)`) | Social media, anonymous tip, unattributed claim |

**Data source:** Add a `reliability` field to the signal context in `tools/publish.py`, derived from `signals.source` mapping. Investigative sources in `_INVESTIGATIVE_SOURCES` map to "verified"; standard news to "reported"; everything else to "unverified."

### 3.4 Entity Graph — `publisher/templates/graph.html`

**Current state:** Cytoscape.js, 23 nodes, 22 edges. Node colors by type (person=orange, institution=blue, government=green, etc.). Hover labels. Click opens sidebar panel with type, confidence, relationships. Search input. Legend. Manual edges solid, auto-extracted dotted.

**Changes:**

| Change | Priority | Effort |
|---|---|---|
| Add "View profile →" link in sidebar panel for actors with published profiles | HIGH | JS only |
| Add double-tap / double-click navigation to actor profile page | MEDIUM | JS only |
| Migrate Cytoscape CDN from cdnjs to jsDelivr with SRI + no-referrer | MEDIUM | Template only |

**Click-through to actor profile — JS addition to `graph.html`:**

In the `openPanel()` function, add a profile link after the relationship list:
```js
// Inside openPanel(), after the rels loop:
var actorId = data.id.replace('n', '');
var profileLink = document.createElement('a');
profileLink.href = 'entities/' + actorId + '.html';
profileLink.className = 'ig-panel__profile-link';
profileLink.textContent = 'View full profile →';
relsEl.parentElement.appendChild(profileLink);
```

Note: This requires the actor slug/ID to match between graph node IDs and the `entities/[id].html` filename. Verify that `publish.py` uses the same ID for both.

**Double-click navigation — JS:**
```js
var tappedBefore = null;
var tappedTimeout = null;

cy.on('tap', 'node', function (evt) {
  var node = evt.target;
  if (tappedTimeout && tappedBefore === node) {
    clearTimeout(tappedTimeout);
    tappedBefore = null;
    // Double-tap: navigate to profile
    var actorId = node.data('id').replace('n', '');
    window.location.href = 'entities/' + actorId + '.html';
  } else {
    tappedBefore = node;
    tappedTimeout = setTimeout(function () {
      // Single tap: open sidebar panel (existing behavior)
      openPanel(node, node.data());
      fadeTo(node);
      tappedBefore = null;
    }, 250);
  }
});
```

**CDN migration:**
```html
<!-- Replace cdnjs with jsDelivr + SRI + no-referrer -->
<script src="https://cdn.jsdelivr.net/npm/cytoscape@3.28.1/dist/cytoscape.min.js"
        integrity="sha384-..."
        crossorigin="anonymous"
        referrerpolicy="no-referrer"></script>
```

### 3.5 Geo Map — `publisher/templates/map.html`

**Current state:** Leaflet.js on CARTO dark tiles. Signals as circle markers. Cluster badges for stacked signals with spiderfy on click. Popups show stream, source, title, gravity badge, date, "Read analysis →" link.

**Changes:**

| Change | Priority | Effort |
|---|---|---|
| Update gravity badge in popups to use significance band label | MEDIUM | JS only |
| Migrate Leaflet CDN from unpkg to jsDelivr with SRI + no-referrer | MEDIUM | Template only |
| Add watchlist star to map popups | LOW | JS only |

### 3.6 Entities Directory — `publisher/templates/entities.html`

**Current state:** Grid of actor cards with photo/placeholder, type badge, name, description excerpt. Filter pills by actor type. Links to `entities/[id].html`.

**Changes:**

| Change | Priority | Effort |
|---|---|---|
| Add watchlist star to each entity card | MEDIUM | Template + JS |
| Upgrade photo placeholder to use type-colored background with dual initials | MEDIUM | Template + CSS + JS |
| Add MiniSearch filtering to supplement pill filters | LOW | JS |

**Photo placeholder upgrade — type-colored backgrounds:**

Current: single initial in a grey box. New: dual initials (first + last name) in a type-colored box.

```css
.bl-card__media--placeholder[data-type="person"]          { background: #1a3a5c; color: #5b8db8; }
.bl-card__media--placeholder[data-type="institution"]     { background: #1a2030; color: #5a7088; }
.bl-card__media--placeholder[data-type="government"]      { background: #0a2a18; color: #40906a; }
.bl-card__media--placeholder[data-type="political_party"] { background: #2a1030; color: #9050a0; }
.bl-card__media--placeholder[data-type="media"]           { background: #2a2010; color: #b09040; }
.bl-card__media--placeholder[data-type="organization"]    { background: #0a2a24; color: #30a088; }
```

**Template change:**
```html
<span class="bl-card__media--placeholder" data-type="{{ a.type }}">
  {{ a.initials }}
</span>
```

**publish.py change:** Add `initials` field to actor context:
```python
def _actor_initials(name):
    parts = name.strip().split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return parts[0][0].upper() if parts else "?"
```

**Research basis:** UX Planet research found generic silhouettes are the worst placeholder option — "if you have two users of the same sex, their placeholder avatars look identical." Initials with deterministic colors (Gmail, Dropbox pattern) allow individual differentiation in lists. For an OSINT site where actors are identified entities, initials correctly convey "we know who this is but have no photo" — unlike silhouettes which imply "identity unknown."

### 3.7 Actor Profile — `publisher/templates/actor_profile.html`

**Current state:** Two-column layout. Main: description, known events, source signals, methodology. Sidebar: photo/initial placeholder, type badge, linked cases, first/last observed, signal count, primary stream, named relationships.

**Changes:**

| Change | Priority | Effort |
|---|---|---|
| Add watchlist star to profile header | MEDIUM | Template + JS |
| Upgrade placeholder to type-colored dual initials (same as directory) | MEDIUM | Template + CSS |
| Add correction/update notice slot above description | MEDIUM | Template only |
| Add "No photo on record" caption below placeholder on full profile | LOW | Template + CSS |
| Expand methodology section to name specific sources backing each claim | LOW | Template + publish.py |

**"No photo" caption (profile page only, not cards):**
```css
.ap-infobox__no-photo {
  font-family: var(--font-mono);
  font-size: 0.52rem;
  color: var(--text-ghost);
  text-align: center;
  margin-top: var(--sp-1);
  letter-spacing: 0.06em;
}
```

### 3.8 Analyst Article — `publisher/templates/article.html`

**Current state:** Classification inline banner, article header (title, summary, author, date, tags), markdown body, footer.

**Changes:**

| Change | Priority | Effort |
|---|---|---|
| Add correction/update notice slot between header and body | MEDIUM | Template only |
| Add "Last updated" line to footer | LOW | Template only |
| Add "Related signals" sidebar or bottom section linking to case detail | LOW | Template + publish.py |

---

## 4. Trust and Credibility UX

### 4.1 Classification Banner

**Research basis:** Stanford Web Credibility Research (B.J. Fogg) found 46.1% of consumers assess website credibility partly through visual design. The US Web Design System (USWDS) uses an official government banner ("An official website of the United States government") on every .gov site specifically because research showed it increases trust.

**Assessment:** ZA-DIVERGENT's "OPEN SOURCE INTELLIGENCE — PUBLIC BULLETIN" banner borrows the authority of government classification formatting while explicitly declaring openness. The word "PUBLIC" is the design's strength — it inverts the classification convention. This inversion signals institutional rigor without implying restricted access.

**Recommendation:** Keep the banner. It works. Three constraints:
1. The banner text must always say "PUBLIC" or "OPEN SOURCE" — never use language that could be read as restricted classification.
2. Keep the monospace font (IBM Plex Mono) and thin height (current `padding: var(--sp-2) var(--sp-4)` ≈ 24px is correct).
3. Keep it visually subordinate to actual content — if it dominates, it tips from trust signal to aesthetic gimmick.

**Optional enhancement:** Add a hover tooltip: "All material on this site is derived from publicly available sources."

### 4.2 Methodology Disclosure

**Convergent pattern:** Bellingcat, Lighthouse Reports, and The Sentry all include methodology sections. Bellingcat embeds them inline; Lighthouse separates them as linked documents; The Sentry uses scroll-synchronized visuals.

**Current state:** Actor profiles have a methodology section at the bottom. Case detail pages and articles do not.

**Recommendation:** Add a brief methodology section to case detail pages and articles:

```html
<div class="methodology-section">
  <div class="methodology-section__title">Methodology</div>
  <div class="methodology-section__body">
    This investigation is produced by the FORGE OSINT engine from {{ signal_count }} open-source
    signals collected between {{ first_signal_date }} and {{ last_signal_date }}. Sources include
    {{ source_list }}. Inclusion reflects analytical relevance to the investigation and does not
    constitute a legal finding. All source material is publicly available.
  </div>
</div>
```

### 4.3 Photo Placeholder Standards

**Research summary (UX Planet, Setproduct, Oracle Alta UI):**

| Approach | Signal conveyed | Use when |
|---|---|---|
| Silhouette | "Identity unknown" | Law enforcement fugitive lists, truly anonymous subjects |
| Single initial | "Known entity, no photo" | Minimal space (small avatars in lists) |
| Dual initials + type color | "Known entity, no photo, categorized" | Profile cards, directory grids, sidebar infoboxes |
| Photo | "Visually confirmed" | Photo available and verified |

ZA-DIVERGENT actors are identified entities — silhouettes would incorrectly imply anonymity. Use dual initials with type-colored backgrounds (implemented in sections 3.6 and 3.7).

---

## 5. CDN and Performance Budget

### 5.1 CDN Provider Selection

| Metric | jsDelivr | unpkg | cdnjs |
|---|---|---|---|
| Load balancing | Multi-CDN (Cloudflare, Fastly, Bunny) | Single-origin proxy | Single-CDN (Cloudflare) |
| Failover | Automatic multi-provider | None | Community mirrors |
| Privacy | Minimal logs, GDPR-compliant | Basic registry tracking | Standard Cloudflare logs |
| Minification | On-the-fly | Serves unmodified npm packages | Requires pre-minified |

**Selection: jsDelivr** for all CDN dependencies. Multi-CDN failover reduces TTFB globally; GDPR-compliant logging is appropriate for a site whose audience may include journalists in sensitive environments.

**Security headers on all CDN script tags:**
```html
<script src="https://cdn.jsdelivr.net/npm/[package]"
        integrity="sha384-..."
        crossorigin="anonymous"
        referrerpolicy="no-referrer"></script>
```

`referrerpolicy="no-referrer"` prevents the browser from sending the current URL path to the CDN, preventing investigation context from leaking in referrer logs.

### 5.2 Dependency Budget

| Library | Current CDN | Migrate to | Size (min) | Used on |
|---|---|---|---|---|
| Cytoscape.js 3.28.1 | cdnjs | jsDelivr | ~290KB | graph.html |
| Leaflet.js 1.9.4 | unpkg | jsDelivr | ~42KB | map.html |
| MiniSearch 7.1.1 | (new) | jsDelivr | ~16KB | all pages (search) |
| **Total** | | | **~348KB** | |

No additional libraries required. Chart.js is loaded from Tailwind CDN in the document brief engine but is not used on ZA-DIVERGENT pages.

---

## 6. Sprint Backlog

Ordered by priority, then by dependency chain. Each item references the section where it is specified.

### Sprint 1 — Core UX (HIGH priority)

| # | Task | Section | Templates affected |
|---|---|---|---|
| 1 | Implement significance band system (replace "G 0.47") | 2.1 | index.html, cases.html, case_detail.html, map.html |
| 2 | Add `data-published` attribute + "new since last visit" JS | 2.2 | index.html, site.js |
| 3 | Upgrade case detail evidence list to vertical timeline rail | 3.3 | case_detail.html, site.css |
| 4 | Add source reliability tier to evidence items | 3.3 | case_detail.html, publish.py |
| 5 | Add "View full profile →" link in graph sidebar panel | 3.4 | graph.html |
| 6 | Add print CSS to site.css + print footer to base.html | 2.5 | site.css, base.html |

### Sprint 2 — Interactivity (MEDIUM priority)

| # | Task | Section | Templates affected |
|---|---|---|---|
| 7 | Implement localStorage watchlist module | 2.3 | watchlist.js (new), index.html, entities.html, actor_profile.html |
| 8 | Generate watchlist.html shell page in publish.py | 2.3 | publish.py, watchlist.html (new) |
| 9 | Build search index in publish.py (Python) | 2.4 | publish.py |
| 10 | Add MiniSearch client-side search to header/stream bar | 2.4 | base.html or index.html, site.js |
| 11 | Add correction/update notice slots | 2.6 | article.html, actor_profile.html, case_detail.html |
| 12 | Upgrade photo placeholders to type-colored dual initials | 3.6, 3.7 | entities.html, actor_profile.html, publish.py |
| 13 | Migrate CDN references to jsDelivr + SRI + no-referrer | 5.1 | graph.html, map.html |
| 14 | Add methodology section to case detail and articles | 4.2 | case_detail.html, article.html |

### Sprint 3 — Polish (LOW priority)

| # | Task | Section | Templates affected |
|---|---|---|---|
| 15 | Add double-click graph navigation to actor profiles | 3.4 | graph.html |
| 16 | Rename "Evidence Weight" to "EVIDENCE CONFIDENCE" | 3.2 | cases.html, case_detail.html |
| 17 | Add "No photo on record" caption to profile placeholder | 3.7 | actor_profile.html |
| 18 | Add "Last updated" line to article and profile footers | 3.8 | article.html, actor_profile.html |
| 19 | Add watchlist star to map popups | 3.5 | map.html |
| 20 | Add "Related signals" section to article pages | 3.8 | article.html, publish.py |

---

## Sources

**Comparator portals:**
- ICIJ Offshore Leaks Database — offshoreleaks.icij.org
- OCCRP Aleph — aleph.occrp.org
- Bellingcat Investigation Hub — bellingcat.com
- The Sentry — thesentry.org (Medium: "A brief history of our data visualization")
- Lighthouse Reports — lighthousereports.com

**Credibility and trust research:**
- Stanford Web Credibility Research / B.J. Fogg — Prominence-Interpretation Theory
- The Trust Project — 8 Trust Indicators (thetrustproject.org)
- US Web Design System (USWDS) — government banner component research
- CHI 2025 — Fact-Checkers' Requirements for Explainable Automated Fact-Checking (confidence-competence gap)
- ClaimBuster (UT Arlington) — claim spotter score framing
- Full Fact (UK) — verdict label calibration

**Design patterns:**
- NNGroup — Indicators, Validations, and Notifications (indicator vs notification distinction)
- UX Planet — 6 Ideas for Creating Better Avatar Placeholders (Nick Babich)
- Setproduct — Badge UI Design (badge type taxonomy)
- HMRC Design System — Notification Badge Pattern

**Technical standards:**
- CSS Paged Media Module Level 3 (W3C)
- CSS Fragmentation Module Level 3 (W3C)
- Cytoscape.js documentation — event handling, cxtmenu extension
- MiniSearch documentation — client-side full-text search

**Journalism standards:**
- Bellingcat Editorial Standards and Practices — correction policy
- ProPublica Code of Ethics — correction standards
- Admiralty Code / NATO AJP-2.1 — source reliability rating system
- GIJN — investigative tools and evidence presentation
