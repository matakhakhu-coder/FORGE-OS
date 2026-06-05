#!/usr/bin/env python3
from __future__ import annotations
"""
tools/generate_op_record.py
============================
Generates docs/OP_RECORD_PRINT.html from current DB state.
Produces a self-contained printable intelligence brief with
embedded D3.js entity graph, narration, and print CSS.

Usage:
    python tools/generate_op_record.py
    python tools/generate_op_record.py --out docs/my_brief.html
"""

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH  = Path(__file__).resolve().parent.parent / "database.db"
OUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "OP_RECORD_PRINT.html"


def load_data() -> dict:
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Actors with at least one relationship
    actors = c.execute("""
        SELECT DISTINCT a.actor_id, a.name, a.type,
               ROUND(a.confidence_score, 3) as confidence_score,
               a.description
        FROM actors a
        WHERE a.confidence_score IS NOT NULL
          AND a.actor_id IN (
              SELECT subject_actor_id FROM entity_relationships
              UNION
              SELECT object_actor_id  FROM entity_relationships
          )
        ORDER BY a.confidence_score DESC
    """).fetchall()

    edges = c.execute("""
        SELECT er.relation_type, ROUND(er.confidence,3) as confidence,
               er.extraction_method,
               a.actor_id as source_id, a.name as source_name,
               b.actor_id as target_id, b.name as target_name
        FROM entity_relationships er
        JOIN actors a ON er.subject_actor_id = a.actor_id
        JOIN actors b ON er.object_actor_id  = b.actor_id
        ORDER BY er.extraction_method DESC, er.confidence DESC
    """).fetchall()

    cases = c.execute("""
        SELECT c.case_id, c.name, c.hypothesis,
               COUNT(cs.signal_id) as n_signals,
               ROUND(AVG(s.gravity_score),3) as avg_g,
               ROUND(MAX(s.gravity_score),3) as max_g
        FROM cases c
        LEFT JOIN case_signals cs ON c.case_id = cs.case_id
        LEFT JOIN signals s ON cs.signal_id = s.signal_id
        WHERE c.status = 'active' AND c.case_id IN (7,8,9,10)
        GROUP BY c.case_id
    """).fetchall()

    totals = c.execute("""
        SELECT
            COUNT(*) as total_signals,
            SUM(CASE WHEN gravity_score IS NULL THEN 1 ELSE 0 END) as unscored,
            COUNT(DISTINCT source) as sources
        FROM signals
    """).fetchone()

    rel_counts = c.execute("""
        SELECT extraction_method, COUNT(*) as n
        FROM entity_relationships GROUP BY extraction_method
    """).fetchall()

    conn.close()

    return {
        "actors":     [dict(r) for r in actors],
        "edges":      [dict(r) for r in edges],
        "cases":      [dict(r) for r in cases],
        "totals":     dict(totals),
        "rel_counts": {r["extraction_method"]: r["n"] for r in rel_counts},
        "generated":  datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


def render(data: dict) -> str:
    actors_json = json.dumps(data["actors"], ensure_ascii=False)
    edges_json  = json.dumps(data["edges"],  ensure_ascii=False)
    cases       = data["cases"]
    totals      = data["totals"]
    generated   = data["generated"]
    verified    = data["rel_counts"].get("manual", 0)
    candidate   = sum(v for k, v in data["rel_counts"].items() if k != "manual")

    case_labels = json.dumps([c["name"][:40] + "..." if len(c["name"]) > 40 else c["name"] for c in cases])
    case_avgg   = json.dumps([c["avg_g"] or 0 for c in cases])
    case_maxg   = json.dumps([c["max_g"] or 0 for c in cases])
    case_sigs   = json.dumps([c["n_signals"] or 0 for c in cases])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>FORGE — Operation Record 2026-06-03</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.8.5/d3.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
/* ── Base ───────────────────────────────────────────────────────── */
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
:root {{
  --ink:        #1a1a2e;
  --ink-mid:    #2d2d4e;
  --ink-soft:   #4a4a6a;
  --accent:     #b8621a;
  --accent2:    #1a5c8a;
  --bg:         #fdfcf8;
  --bg-alt:     #f4f0e8;
  --border:     #d4cfc4;
  --verified:   #1a6b3a;
  --candidate:  #7a6a1a;
  --font-body:  'Georgia', serif;
  --font-mono:  'Courier New', monospace;
  --font-sans:  'Helvetica Neue', Arial, sans-serif;
}}
body {{
  font-family: var(--font-body);
  background: var(--bg);
  color: var(--ink);
  font-size: 11pt;
  line-height: 1.65;
}}

/* ── Layout ─────────────────────────────────────────────────────── */
.page {{
  max-width: 860px;
  margin: 0 auto;
  padding: 40px 48px;
}}
.two-col {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
  margin: 16px 0;
}}

/* ── Classification banner ───────────────────────────────────────── */
.classify-banner {{
  font-family: var(--font-mono);
  font-size: 7.5pt;
  letter-spacing: 0.22em;
  text-align: center;
  text-transform: uppercase;
  color: var(--accent);
  border: 1px solid var(--accent);
  padding: 4px 12px;
  margin-bottom: 32px;
}}

/* ── Header ─────────────────────────────────────────────────────── */
.doc-header {{
  border-bottom: 2px solid var(--ink);
  padding-bottom: 20px;
  margin-bottom: 28px;
}}
.doc-wordmark {{
  font-family: var(--font-mono);
  font-size: 9pt;
  letter-spacing: 0.28em;
  text-transform: uppercase;
  color: var(--ink-soft);
}}
.doc-title {{
  font-family: var(--font-sans);
  font-size: 22pt;
  font-weight: 700;
  color: var(--ink);
  margin: 6px 0 2px;
  letter-spacing: -0.02em;
}}
.doc-subtitle {{
  font-family: var(--font-sans);
  font-size: 11pt;
  color: var(--ink-soft);
  font-weight: 400;
}}
.doc-meta {{
  margin-top: 10px;
  font-family: var(--font-mono);
  font-size: 7.5pt;
  color: var(--ink-soft);
  letter-spacing: 0.06em;
}}
.doc-meta span {{ margin-right: 20px; }}

/* ── Section headings ────────────────────────────────────────────── */
.section-rule {{
  border: none;
  border-top: 1px solid var(--border);
  margin: 32px 0 20px;
}}
h2 {{
  font-family: var(--font-sans);
  font-size: 13pt;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: var(--ink);
  margin-bottom: 4px;
}}
h2 .section-num {{
  color: var(--ink-soft);
  font-weight: 400;
  margin-right: 8px;
}}
h3 {{
  font-family: var(--font-sans);
  font-size: 11pt;
  font-weight: 700;
  color: var(--ink);
  margin: 20px 0 6px;
}}
h3.thread-title {{
  font-size: 12pt;
  border-left: 3px solid var(--accent);
  padding-left: 10px;
  margin: 24px 0 10px;
}}

/* ── Body text ───────────────────────────────────────────────────── */
p {{
  margin-bottom: 10px;
  text-align: justify;
}}
.lede {{
  font-size: 12pt;
  line-height: 1.75;
  color: var(--ink-mid);
  margin-bottom: 16px;
}}
.analyst-note {{
  font-family: var(--font-sans);
  font-size: 9.5pt;
  background: var(--bg-alt);
  border-left: 3px solid var(--accent2);
  padding: 10px 14px;
  margin: 14px 0;
  color: var(--ink-mid);
  line-height: 1.5;
}}
.analyst-note strong {{ color: var(--ink); }}

/* ── Confidence + finding tags ───────────────────────────────────── */
.tag {{
  display: inline-block;
  font-family: var(--font-mono);
  font-size: 6.5pt;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  padding: 2px 7px;
  border-radius: 2px;
  margin-right: 4px;
  vertical-align: middle;
}}
.tag-high      {{ background: #d4edda; color: #155724; border: 1px solid #c3e6cb; }}
.tag-medium    {{ background: #fff3cd; color: #856404; border: 1px solid #ffeeba; }}
.tag-low       {{ background: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }}
.tag-verified  {{ background: #d1ecf1; color: #0c5460; border: 1px solid #bee5eb; }}
.tag-candidate {{ background: #fefefe; color: var(--ink-soft); border: 1px solid var(--border); }}
.tag-demoted   {{ background: #e2e3e5; color: #383d41; border: 1px solid #d6d8db; text-decoration: line-through; }}

/* ── Finding blocks ──────────────────────────────────────────────── */
.finding {{
  border: 1px solid var(--border);
  border-left: 4px solid var(--accent);
  padding: 12px 16px;
  margin: 12px 0;
  background: #fff;
}}
.finding.demoted {{ border-left-color: #ccc; opacity: 0.6; }}
.finding-label {{
  font-family: var(--font-mono);
  font-size: 7.5pt;
  letter-spacing: 0.12em;
  color: var(--ink-soft);
  text-transform: uppercase;
  margin-bottom: 4px;
}}
.finding-text {{
  font-size: 10.5pt;
  color: var(--ink-mid);
  margin: 0;
}}

/* ── Graph container ─────────────────────────────────────────────── */
.graph-wrap {{
  width: 100%;
  background: var(--bg-alt);
  border: 1px solid var(--border);
  border-radius: 4px;
  margin: 20px 0;
  overflow: hidden;
  position: relative;
}}
#forge-graph {{
  width: 100%;
  height: 480px;
  display: block;
}}
.graph-legend {{
  display: flex;
  gap: 20px;
  flex-wrap: wrap;
  font-family: var(--font-mono);
  font-size: 7.5pt;
  color: var(--ink-soft);
  padding: 8px 14px;
  border-top: 1px solid var(--border);
  background: #fff;
  letter-spacing: 0.06em;
}}
.legend-item {{ display: flex; align-items: center; gap: 5px; }}
.legend-dot  {{
  width: 9px; height: 9px; border-radius: 50%;
  border: 1.5px solid rgba(0,0,0,0.15);
  flex-shrink: 0;
}}
.legend-line {{ width: 22px; height: 2px; flex-shrink: 0; }}

/* ── Metrics table ───────────────────────────────────────────────── */
.metrics-grid {{
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 1px;
  background: var(--border);
  border: 1px solid var(--border);
  margin: 16px 0;
}}
.metric-cell {{
  background: var(--bg);
  padding: 10px 12px;
  text-align: center;
}}
.metric-val {{
  font-family: var(--font-sans);
  font-size: 18pt;
  font-weight: 700;
  color: var(--ink);
  line-height: 1.1;
}}
.metric-label {{
  font-family: var(--font-mono);
  font-size: 6.5pt;
  letter-spacing: 0.1em;
  color: var(--ink-soft);
  text-transform: uppercase;
  margin-top: 2px;
}}

/* ── Chart ───────────────────────────────────────────────────────── */
.chart-wrap {{ margin: 16px 0; height: 200px; position: relative; }}

/* ── Footer ──────────────────────────────────────────────────────── */
.doc-footer {{
  margin-top: 48px;
  padding-top: 16px;
  border-top: 1px solid var(--border);
  font-family: var(--font-mono);
  font-size: 7pt;
  color: var(--ink-soft);
  letter-spacing: 0.06em;
  display: flex;
  justify-content: space-between;
}}

/* ── Print overrides ─────────────────────────────────────────────── */
@media print {{
  body {{ font-size: 10pt; }}
  .page {{ padding: 20px 30px; max-width: 100%; }}
  .no-print {{ display: none !important; }}
  h2, h3.thread-title {{ page-break-after: avoid; }}
  .finding, .analyst-note {{ page-break-inside: avoid; }}
  #forge-graph {{ height: 400px; }}
  .graph-wrap {{ page-break-inside: avoid; }}
  .two-col {{ page-break-inside: avoid; }}
  @page {{
    margin: 18mm 15mm;
    @bottom-center {{ content: "FORGE — ANALYST WORKING COPY — NOT FOR DISTRIBUTION"; font-size: 7pt; }}
    @bottom-right  {{ content: counter(page); font-size: 7pt; }}
  }}
}}
</style>
</head>
<body>
<div class="page">

<!-- Classification -->
<div class="classify-banner">ANALYST WORKING COPY — NOT FOR DISTRIBUTION — FORGE v1.1.3</div>

<!-- Header -->
<header class="doc-header">
  <div class="doc-wordmark">FORGE · Foundational Open Research &amp; Graph Engine</div>
  <div class="doc-title">Operation Record</div>
  <div class="doc-subtitle">Magaqa &nbsp;·&nbsp; Eskom &nbsp;·&nbsp; KZN HAWKS &nbsp;·&nbsp; Adversarial Review</div>
  <div class="doc-meta">
    <span>Sealed: {generated}</span>
    <span>Build: Stable 1.1.3</span>
    <span>Corpus: {totals['total_signals']:,} signals</span>
    <span>Graph: {verified} verified · {candidate} candidate edges</span>
  </div>
</header>

<!-- Preface -->
<p class="lede">
  What follows is a point-in-time record of three open intelligence threads maintained by
  FORGE as of June 2026. These threads are not unrelated. They share a geography, a
  period, and a pattern: institutions designed to hold power accountable appear, in varying
  degrees, to be compromised by the power they are meant to hold. The analysis that produced
  this record was subjected to independent review — findings were re-derived from raw signals
  without reference to prior conclusions, and only those that independently converged were
  retained at high confidence. What did not survive that test is marked plainly.
</p>
<p>
  The graph on the following page represents the current state of the entity relationship
  model — the people, institutions, and operational connections that FORGE has been able to
  verify or provisionally identify. Solid edges survived hostile review. Dotted edges are
  candidates that have not yet done so. The graph is incomplete. That is not a failure of
  the system. An incomplete graph that knows its own limits is more useful than a complete
  one that does not.
</p>

<hr class="section-rule">

<!-- Graph section -->
<h2><span class="section-num">I.</span>The Entity Graph</h2>
<p>Nodes are coloured by actor type. Edge weight represents confidence.
   Solid lines are analyst-verified relationships. Dashed lines are
   machine-extracted candidates awaiting promotion or rejection.</p>

<div class="graph-wrap">
  <svg id="forge-graph"></svg>
  <div class="graph-legend">
    <div class="legend-item"><div class="legend-dot" style="background:#c0392b"></div> Person</div>
    <div class="legend-item"><div class="legend-dot" style="background:#2980b9"></div> Institution</div>
    <div class="legend-item"><div class="legend-dot" style="background:#8e44ad"></div> Political Party</div>
    <div class="legend-item"><div class="legend-dot" style="background:#27ae60"></div> Government</div>
    <div class="legend-item"><div class="legend-dot" style="background:#7f8c8d"></div> Other</div>
    <div style="margin-left:auto; display:flex; gap:20px;">
      <div class="legend-item"><div class="legend-line" style="background:#1a6b3a"></div> Verified</div>
      <div class="legend-item"><div class="legend-line" style="background:#aaa; border-top: 2px dashed #aaa; height:0"></div> Candidate</div>
    </div>
  </div>
</div>

<hr class="section-rule">

<!-- Corpus metrics -->
<h2><span class="section-num">II.</span>Corpus State</h2>
<div class="metrics-grid">
  <div class="metric-cell">
    <div class="metric-val">{totals['total_signals']:,}</div>
    <div class="metric-label">Total Signals</div>
  </div>
  <div class="metric-cell">
    <div class="metric-val">{totals['sources']}</div>
    <div class="metric-label">Source Channels</div>
  </div>
  <div class="metric-cell">
    <div class="metric-val">{verified + candidate}</div>
    <div class="metric-label">Graph Edges</div>
  </div>
  <div class="metric-cell">
    <div class="metric-val">{verified}</div>
    <div class="metric-label">Verified Edges</div>
  </div>
</div>

<div class="two-col">
  <div>
    <h3>Case Signal Density</h3>
    <div class="chart-wrap">
      <canvas id="gravity-chart"></canvas>
    </div>
  </div>
  <div>
    <h3>Active Cases</h3>
    {"".join(f'''
    <div style="margin:8px 0; padding:8px 10px; background:#fff; border:1px solid var(--border); border-left:3px solid var(--accent);">
      <div style="font-family:var(--font-mono);font-size:7pt;color:var(--ink-soft);text-transform:uppercase;letter-spacing:0.1em;">Case {c["case_id"]}</div>
      <div style="font-family:var(--font-sans);font-size:9pt;font-weight:600;color:var(--ink);margin:2px 0;">{c["name"][:55] + "..." if len(c["name"]) > 55 else c["name"]}</div>
      <div style="font-family:var(--font-mono);font-size:7.5pt;color:var(--ink-soft);">
        {c["n_signals"]} signals &nbsp;·&nbsp; avg gravity {c["avg_g"]} &nbsp;·&nbsp; max {c["max_g"]}
      </div>
    </div>''' for c in cases)}
  </div>
</div>

<hr class="section-rule" style="page-break-before:always;">

<!-- Section III: Findings -->
<h2><span class="section-num">III.</span>The Threads</h2>

<!-- MAGAQA -->
<h3 class="thread-title">Operation Magaqa — Political Interference</h3>

<p>
  Sindiso Magaqa was thirty-two years old when he was shot in Richards Bay in July 2017.
  He was the Secretary-General of the ANC Youth League at the time — a position that placed
  him near the centre of some of the most contested political terrain in the country. He died
  from his injuries in August 2017. The investigation into his murder has been open for
  nine years.
</p>
<p>
  In May 2026, a Member of Parliament named Fadiel Adams was arrested in Cape Town. The
  charge was alleged interference in the Magaqa murder probe. What followed over the next
  five days is documented in five signals from three independent news sources: police
  searched Adams' home in Mitchells Plain; Adams obtained emergency court relief and was
  arrested within hours of that order; he was transported to KwaZulu-Natal under operational
  silence. This is not a simple story. A sitting MP does not seek emergency court relief
  against an arrest, and then lose it within hours, unless something specific is at risk of
  being disclosed.
</p>

<div class="analyst-note">
  <strong>What the corpus shows:</strong> A 5-signal, 3-source operational sequence
  confirming the arrest, transfer, and operational silence. The core fact is
  <span class="tag tag-high">High Confidence</span>.
  The underlying question — who ordered the assassination of Sindiso Magaqa in 2017 — is
  not answerable from the current corpus.
</div>

<div class="finding">
  <div class="finding-label">Finding M-I &nbsp;<span class="tag tag-high">Converged</span></div>
  <p class="finding-text">Fadiel Adams (NCC MP, Western Cape) arrested May 2026 for alleged
  interference in the Magaqa murder probe. Confirmed by Daily Maverick and News24 across five
  signals. Emergency court bid obtained and overturned same day. KZN transfer under
  operational security. Jurisdiction is KwaZulu-Natal — the locus of the 2017 murder.</p>
</div>

<div class="finding">
  <div class="finding-label">Finding M-II &nbsp;<span class="tag tag-medium">Provisional</span></div>
  <p class="finding-text">SAFLII returned zero court judgments for Sindiso Magaqa.
  Nine years after the murder, no published prosecution judgment exists. This is either
  a case still approaching trial — which Adams' arrest is consistent with — or a case
  that has been sustained in obstruction. The absence of a record is not absence of
  a case. It is the shape of nine years of delay.</p>
</div>

<div class="finding demoted">
  <div class="finding-label">Finding M-III &nbsp;<span class="tag tag-demoted">Demoted — second pass</span></div>
  <p class="finding-text">S v Thebus (338/2001) ZASCA 89 — retrieved on SAFLII search for "Adams."
  No body text. "Adams" is a common SA surname. This signal was elevated in the first pass.
  The second pass found it carries zero evidentiary weight without body text retrieval.
  Removed from case corpus pending full judgment fetch.</p>
</div>

<!-- ESKOM -->
<h3 class="thread-title">Eskom Procurement Fraud — The R21 Billion Contract</h3>

<p>
  In 2024, Minenhle Mavuso was twenty-four years old and running an online wig store called
  Milo Hair Beauté from Cape Town. A super double drawn bob, the store's listing notes, would
  set you back R1,180. In a parallel life, documented by amaBhungane in May 2026, she was
  the named operator of a company that had been awarded a R21-billion diesel supply contract
  with Eskom.
</p>
<p>
  The CIPC database — South Africa's public company registry — returns zero registration
  entries for Minenhle Mavuso. It returns zero for Milo Hair Beauté. This means that
  whatever entity received the R21-billion contract was either unregistered or registered
  under a name that does not appear in public records. An unregistered entity cannot legally
  hold a government procurement contract under the PFMA or PPPFA frameworks.
</p>
<p>
  Mavuso is not the story. She may be the least powerful person in this chain. The story is
  whoever approved her supplier registration. Whoever sat on the Eskom procurement committee
  when this contract was awarded. Whoever signed the award. Those names are not in the
  corpus. The corpus contains only the endpoint. Causal actors exist upstream, and they
  are unidentified.
</p>

<div class="analyst-note">
  <strong>Corpus ceiling:</strong> This case currently rests on one investigative source
  (amaBhungane, high credibility) and one derived finding (CIPC null result).
  <span class="tag tag-medium">Medium Confidence</span>. Two HAWKS Eskom bogus-supplier
  arrest signals from 2023 are in the corpus but could not be independently connected to
  Mavuso's 2024 contract — different period, no named links. They remain contextual.
</div>

<div class="finding">
  <div class="finding-label">Finding E-I &nbsp;<span class="tag tag-medium">Single Source Ceiling</span></div>
  <p class="finding-text">Minenhle Mavuso (24, BA Psychology) named as front company operator
  for R21bn Eskom diesel supply contract (2024). CIPC: zero registrations for Mavuso and
  Milo Hair Beauté. Structural fraud indicator. Authorising officials unidentified.
  Source: amaBhungane (high credibility, single outlet).</p>
</div>

<div class="finding">
  <div class="finding-label">Finding E-II &nbsp;<span class="tag tag-medium">Open Requirement</span></div>
  <p class="finding-text">Eskom procurement officials during 2024 diesel tender period have
  not been identified or named in the corpus. The ABB state capture reparations signal
  (R2.5bn, 2022) establishes that Eskom's procurement function was previously compromised
  at scale. Whether the same networks are operating in the post-Zondo period is
  unconfirmed from available evidence.</p>
</div>

<!-- KZN HAWKS -->
<h3 class="thread-title">KZN HAWKS Institutional Integrity</h3>

<p>
  The Directorate for Priority Crime Investigation exists, in theory, to investigate
  priority crime. In KwaZulu-Natal, it appears that the directorate has itself become
  a site of priority crime. What the corpus documents is not an isolated incident.
  It is a sequence.
</p>
<p>
  Someone broke into a HAWKS facility in KwaZulu-Natal repeatedly, before 541 kilograms
  of cocaine were stolen. The cocaine had been previously seized in a bust where HAWKS
  officials failed to follow protocol. Before the theft, a paper trail was fabricated.
  Internal HAWKS sources told journalists it was an inside job. The KZN HAWKS boss was
  called before a commission of inquiry and identified as someone who should have
  taken a lie detector test. The polygraph was not administered.
</p>
<p>
  Three independent publications — TimesLIVE, the Mail &amp; Guardian, and News24 — covered
  the same commission testimony on the same day in May 2026. That is what genuine
  multi-source convergence looks like: three journalists in the same room, writing
  the same story, none of them knowing the others would. The Democratic Alliance tabled
  an urgent debate motion on KZN police corruption in July 2025. The matter has reached
  political visibility.
</p>
<p>
  What the corpus does not answer is who is buying the cocaine that left the HAWKS office.
  The beneficiary network is the unknown variable. Without it, this case describes a pattern
  of institutional decay. With it, it describes a criminal enterprise with law enforcement
  as its logistics infrastructure.
</p>

<div class="analyst-note">
  <strong>Strongest finding in the corpus.</strong> Three independent outlets, same
  commission testimony, same day. Fabricated documentation + premeditated facility access
  = planned extraction, not opportunistic theft.
  <span class="tag tag-high">High Confidence</span> on the existence of the pattern.
  Beneficiary network: <span class="tag tag-low">Unknown</span>.
</div>

<div class="finding">
  <div class="finding-label">Finding K-I &nbsp;<span class="tag tag-high">Independently Converged</span></div>
  <p class="finding-text">Three-outlet convergence (TimesLIVE, Mail &amp; Guardian, News24)
  on same commission testimony (May 2026): paper trail fabricated before theft, repeated
  facility break-ins, HAWKS protocol failure on seizure. Four-stage operational sequence
  confirmed across independent sources. This is institutional capture, not isolated
  misconduct.</p>
</div>

<div class="finding">
  <div class="finding-label">Finding K-II &nbsp;<span class="tag tag-medium">Probable Convergence</span></div>
  <p class="finding-text">2022 TimesLIVE report ("Sniffing cops: cocaine theft could be
  inside job") references cocaine theft from HAWKS office — probable same event as 2026
  commission testimony, given 541kg figure. If confirmed same event, this extends the
  documented timeline to 4 years of unresolved institutional compromise.</p>
</div>

<div class="finding demoted">
  <div class="finding-label">Finding K-III &nbsp;<span class="tag tag-demoted">Isolated from Case — second pass</span></div>
  <p class="finding-text">2020 HAWKS members arrested for theft (TimesLIVE). Six-year gap,
  no cocaine reference, no case number connection. First pass included this as a pattern
  signal. Second pass found no independent connection. Removed from case corpus. May be
  contextually relevant at stream level but should not be presented as structural evidence
  for Case 10.</p>
</div>

<hr class="section-rule">

<!-- Section IV: Methodology -->
<h2><span class="section-num">IV.</span>The Methodology of Doubt</h2>

<p>
  This document applied a two-pass adversarial review to all analytical findings before
  publication. The process works as follows: after an initial analytical pass that derives
  findings from the signal corpus, a second pass re-derives findings independently — working
  only from raw signal content, without reference to prior conclusions. Findings that appear
  in both passes at similar confidence levels are treated as converged. Findings that appear
  in only one pass, or that appear at significantly different confidence levels, are demoted.
</p>
<p>
  This process is not a guarantee of accuracy. It is a constraint against a specific failure
  mode: an analyst — human or automated — that reviews its own work and finds it convincing.
  The risk is not that a finding is wrong. The risk is that a wrong finding survives
  because nothing was designed to challenge it. Independent convergence is not proof.
  It is the minimum condition for treating a finding as intelligence rather than hypothesis.
</p>

<div class="analyst-note">
  <strong>What was demoted in this review:</strong> S v Thebus (stripped SAFLII title, common
  surname, no body text). Mkwanazi v Minister of Police (different name spelling, no body
  text). 2020 HAWKS theft (six-year gap, no cocaine reference). CIPC finding signal
  (self-generated — a system cannot independently corroborate itself). Two 2023 Eskom
  HAWKS arrests treated as pattern evidence (one event, two outlets, different period).
</div>

<hr class="section-rule">

<!-- Section V: What the graph does not yet show -->
<h2><span class="section-num">V.</span>What the Graph Does Not Yet Show</h2>
<p>The following questions cannot be answered from the current corpus.
   They are ranked by the degree to which answering them would change the
   analytical picture.</p>

<div class="two-col">
  <div>
    <h3>Critical gaps</h3>
    <div class="finding" style="border-left-color:var(--accent2);">
      <div class="finding-label">Magaqa</div>
      <p class="finding-text">Who ordered the 2017 assassination?
      What is Adams' specific role in the probe obstruction?
      Does S v Thebus name Adams as co-accused?</p>
    </div>
    <div class="finding" style="border-left-color:var(--accent2);">
      <div class="finding-label">Eskom</div>
      <p class="finding-text">Who authorised the Mavuso supplier registration?
      Who signed the contract award?
      What is the upstream procurement chain?</p>
    </div>
    <div class="finding" style="border-left-color:var(--accent2);">
      <div class="finding-label">KZN HAWKS</div>
      <p class="finding-text">Who is the KZN HAWKS boss named at the commission?
      Who is the beneficiary network for the diverted narcotics?
      Is the 2022 inside job signal the same event as the 2026 commission testimony?</p>
    </div>
  </div>
  <div>
    <h3>Collection actions required</h3>
    <div class="analyst-note" style="margin-top:0;">
      1. Fetch full text: <em>S v Thebus [2002] ZASCA 89</em> from saflii.org.
      Confirm or eliminate Adams as co-accused.<br><br>
      2. Identity of KZN HAWKS boss at commission.
      Run: <em>news24.com KZN Hawks boss cocaine commission</em>.<br><br>
      3. Eskom procurement officials, 2024 diesel tender.
      Run: <em>site:eskom.co.za tender diesel 2024</em>.<br><br>
      4. Confirm whether Mavuso's company appears under
      alternative registration — possible nominee directorship.<br><br>
      5. Run content enricher on SAFLII signals to get body text
      before reassessing SAFLII anchors.
    </div>
  </div>
</div>

<hr class="section-rule">

<!-- Footer -->
<footer class="doc-footer">
  <span>FORGE · Foundational Open Research &amp; Graph Engine · Stable 1.1.3</span>
  <span>Sealed {generated} · Analyst Working Copy · Not for Distribution</span>
</footer>

</div><!-- /page -->

<!-- D3 Graph -->
<script>
(function() {{
  const ACTORS = {actors_json};
  const EDGES  = {edges_json};

  const TYPE_COLOUR = {{
    person:          '#c0392b',
    institution:     '#2980b9',
    political_party: '#8e44ad',
    government:      '#27ae60',
    movement:        '#e67e22',
    media:           '#16a085',
    paramilitary:    '#c0392b',
    other:           '#7f8c8d',
  }};

  function nodeColour(type) {{ return TYPE_COLOUR[type] || '#7f8c8d'; }}

  const svg    = d3.select('#forge-graph');
  const width  = svg.node().getBoundingClientRect().width || 760;
  const height = +svg.attr('height') || 480;
  svg.attr('viewBox', `0 0 ${{width}} ${{height}}`);

  // Defs: arrowhead markers
  const defs = svg.append('defs');
  ['verified','candidate'].forEach(type => {{
    defs.append('marker')
      .attr('id', `arrow-${{type}}`)
      .attr('viewBox', '0 -4 8 8')
      .attr('refX', 20).attr('refY', 0)
      .attr('markerWidth', 5).attr('markerHeight', 5)
      .attr('orient', 'auto')
      .append('path')
        .attr('d', 'M0,-4L8,0L0,4')
        .attr('fill', type === 'verified' ? '#1a6b3a' : '#aaa');
  }});

  const nodeMap = {{}};
  ACTORS.forEach(a => {{ nodeMap[a.actor_id] = a; }});

  const nodes = ACTORS.map(a => ({{ ...a, id: a.actor_id }}));
  const links = EDGES
    .filter(e => nodeMap[e.source_id] && nodeMap[e.target_id])
    .map(e => ({{
      source:   e.source_id,
      target:   e.target_id,
      relation: e.relation_type,
      conf:     e.confidence,
      verified: e.extraction_method === 'manual',
    }}));

  const sim = d3.forceSimulation(nodes)
    .force('link',   d3.forceLink(links).id(d => d.id).distance(120).strength(0.6))
    .force('charge', d3.forceManyBody().strength(-280))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('coll',   d3.forceCollide(32))
    .stop();

  for (let i = 0; i < 300; ++i) sim.tick();

  // Clamp to bounds
  nodes.forEach(n => {{
    n.x = Math.max(48, Math.min(width  - 48, n.x));
    n.y = Math.max(24, Math.min(height - 24, n.y));
  }});

  const g = svg.append('g');

  // Edges
  const link = g.selectAll('.link')
    .data(links).enter().append('line')
    .attr('class', 'link')
    .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
    .attr('x2', d => d.target.x).attr('y2', d => d.target.y)
    .attr('stroke',       d => d.verified ? '#1a6b3a' : '#bbb')
    .attr('stroke-width', d => 0.8 + d.conf * 2.2)
    .attr('stroke-dasharray', d => d.verified ? null : '5 4')
    .attr('opacity',      d => d.verified ? 0.85 : 0.55)
    .attr('marker-end',   d => `url(#arrow-${{d.verified ? 'verified' : 'candidate'}})`);

  // Edge labels (verified only, hide if too small)
  g.selectAll('.edge-label')
    .data(links.filter(d => d.verified && d.conf > 0.3))
    .enter().append('text')
    .attr('class', 'edge-label')
    .attr('x', d => (d.source.x + d.target.x) / 2)
    .attr('y', d => (d.source.y + d.target.y) / 2 - 4)
    .attr('text-anchor', 'middle')
    .attr('font-family', 'Courier New, monospace')
    .attr('font-size', '6px')
    .attr('fill', '#1a6b3a')
    .attr('opacity', 0.75)
    .text(d => d.relation);

  // Node circles
  const node = g.selectAll('.node')
    .data(nodes).enter().append('g').attr('class', 'node')
    .attr('transform', d => `translate(${{d.x}},${{d.y}})`);

  node.append('circle')
    .attr('r', d => 7 + (d.confidence_score || 0.3) * 10)
    .attr('fill',         d => nodeColour(d.type))
    .attr('fill-opacity', 0.18)
    .attr('stroke',       d => nodeColour(d.type))
    .attr('stroke-width', 1.8);

  // Node labels
  node.append('text')
    .attr('dy', d => -(9 + (d.confidence_score || 0.3) * 10) - 3)
    .attr('text-anchor', 'middle')
    .attr('font-family', 'Helvetica Neue, Arial, sans-serif')
    .attr('font-size',   '8px')
    .attr('font-weight', d => d.confidence_score >= 0.6 ? '700' : '400')
    .attr('fill', '#1a1a2e')
    .text(d => d.name.length > 22 ? d.name.slice(0, 20) + '…' : d.name);

  // Confidence sub-label
  node.append('text')
    .attr('dy', d => (9 + (d.confidence_score || 0.3) * 10) + 12)
    .attr('text-anchor', 'middle')
    .attr('font-family', 'Courier New, monospace')
    .attr('font-size', '6px')
    .attr('fill', '#7f8c8d')
    .text(d => `${{d.type}} · ${{(d.confidence_score || 0).toFixed(2)}}`);

}})();
</script>

<!-- Chart.js bar chart -->
<script>
(function() {{
  const ctx = document.getElementById('gravity-chart');
  if (!ctx) return;
  new Chart(ctx, {{
    type: 'bar',
    data: {{
      labels: {case_labels},
      datasets: [
        {{
          label: 'Avg Gravity',
          data: {case_avgg},
          backgroundColor: 'rgba(26,92,138,0.6)',
          borderColor: '#1a5c8a',
          borderWidth: 1,
        }},
        {{
          label: 'Max Gravity',
          data: {case_maxg},
          backgroundColor: 'rgba(184,98,26,0.4)',
          borderColor: '#b8621a',
          borderWidth: 1,
        }},
      ]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      plugins: {{ legend: {{ labels: {{ font: {{ size: 9, family: 'Courier New' }} }} }} }},
      scales: {{
        y: {{
          min: 0, max: 1,
          ticks: {{ font: {{ size: 8, family: 'Courier New' }}, stepSize: 0.25 }},
          grid:  {{ color: '#e8e4dc' }}
        }},
        x: {{ ticks: {{ font: {{ size: 7, family: 'Courier New' }} }} }}
      }}
    }}
  }});
}})();
</script>

</body>
</html>"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=str, default=str(OUT_PATH))
    args = parser.parse_args()

    print("Loading data from database...")
    data = load_data()
    print(f"  Actors: {len(data['actors'])}  Edges: {len(data['edges'])}  Cases: {len(data['cases'])}")

    print("Rendering document...")
    html = render(data)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"Written: {out}")
    print(f"Open in browser and Ctrl+P to print.")


if __name__ == "__main__":
    main()
