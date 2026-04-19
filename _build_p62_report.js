const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, PageOrientation, LevelFormat,
  TabStopType, TabStopPosition, HeadingLevel, BorderStyle, WidthType,
  ShadingType, PageNumber, PageBreak, TableOfContents,
} = require("docx");

const OUT = "C:\\Users\\matam\\Downloads\\FORGE_Phase62_Substrate_Report.docx";

// ---------- helpers ----------
const NAVY = "1F3864";
const STEEL = "2E75B6";
const GRID = "BFBFBF";
const HEAD_FILL = "D9E2F3";
const ALT_FILL = "F2F2F2";

const border = { style: BorderStyle.SINGLE, size: 6, color: GRID };
const cellBorders = { top: border, bottom: border, left: border, right: border };

function p(text, opts = {}) {
  return new Paragraph({
    spacing: { after: 120 },
    ...opts,
    children: [new TextRun({ text, ...(opts.run || {}) })],
  });
}

function bullet(text, run = {}) {
  return new Paragraph({
    numbering: { reference: "bullets", level: 0 },
    spacing: { after: 80 },
    children: [new TextRun({ text, ...run })],
  });
}

function h1(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    spacing: { before: 320, after: 200 },
    children: [new TextRun({ text })],
  });
}
function h2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    spacing: { before: 240, after: 140 },
    children: [new TextRun({ text })],
  });
}
function h3(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_3,
    spacing: { before: 180, after: 100 },
    children: [new TextRun({ text })],
  });
}

function cell(text, { bold = false, fill, widthDxa, align = AlignmentType.LEFT, color } = {}) {
  return new TableCell({
    borders: cellBorders,
    width: { size: widthDxa, type: WidthType.DXA },
    shading: fill ? { fill, type: ShadingType.CLEAR } : undefined,
    margins: { top: 80, bottom: 80, left: 120, right: 120 },
    children: [
      new Paragraph({
        alignment: align,
        children: [new TextRun({ text, bold, color })],
      }),
    ],
  });
}

function makeTable(headers, rows, widths) {
  const totalW = widths.reduce((a, b) => a + b, 0);
  const headerRow = new TableRow({
    tableHeader: true,
    children: headers.map((h, i) => cell(h, { bold: true, fill: HEAD_FILL, widthDxa: widths[i] })),
  });
  const bodyRows = rows.map(
    (r, ri) =>
      new TableRow({
        children: r.map((c, i) =>
          cell(String(c), { widthDxa: widths[i], fill: ri % 2 === 1 ? ALT_FILL : undefined })
        ),
      })
  );
  return new Table({
    width: { size: totalW, type: WidthType.DXA },
    columnWidths: widths,
    rows: [headerRow, ...bodyRows],
  });
}

// ---------- content ----------
const today = "2026-04-18";

// Cover page
const coverChildren = [
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 400, after: 200 },
    children: [new TextRun({ text: "CLASSIFICATION: INTERNAL // OPERATIONAL", bold: true, color: "C00000", size: 22 })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    border: { bottom: { style: BorderStyle.SINGLE, size: 12, color: STEEL, space: 4 } },
    spacing: { after: 600 },
    children: [new TextRun({ text: "FORGE Intelligence Platform", bold: true, color: NAVY, size: 28 })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 1800, after: 200 },
    children: [new TextRun({ text: "PHASE 62", bold: true, color: NAVY, size: 72 })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 200 },
    children: [new TextRun({ text: "SUBSTRATE INTERROGATION", bold: true, size: 44 })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 1200 },
    children: [new TextRun({ text: "Structural Audit of the Cognitive Data Layer", italics: true, size: 28, color: "595959" })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 120 },
    children: [new TextRun({ text: `Report Date: ${today}`, size: 22 })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 120 },
    children: [new TextRun({ text: "Substrate: database.db (SQLite, WAL mode)", size: 22 })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 120 },
    children: [new TextRun({ text: "Prepared by: FORGE Command Architect", size: 22 })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 2400 },
    children: [new TextRun({ text: "HANDLING: Do not redistribute outside command channel.", italics: true, size: 18, color: "7F7F7F" })],
  }),
  new Paragraph({ children: [new PageBreak()] }),
];

// TOC page
const tocChildren = [
  new Paragraph({
    heading: HeadingLevel.HEADING_1,
    alignment: AlignmentType.LEFT,
    spacing: { after: 200 },
    children: [new TextRun({ text: "Table of Contents" })],
  }),
  new TableOfContents("Table of Contents", { hyperlink: true, headingStyleRange: "1-3" }),
  new Paragraph({ children: [new PageBreak()] }),
];

// Section 1 — Stack Detection
const sec1 = [
  h1("1. Stack Detection"),
  p("Phase 62 began by fingerprinting the substrate that underlies every cognitive subsystem in FORGE. The objective was to confirm (or refute) assumptions about engine, concurrency model, durability contract, and enforcement posture before any further structural work was authorised."),
  h2("1.1 Detected Stack"),
  makeTable(
    ["Attribute", "Detected Value", "Assessment"],
    [
      ["Database Engine", "SQLite 3 (file-backed)", "Confirmed — embedded, single-file."],
      ["File", "database.db (579.4 MB)", "Within single-node operational envelope."],
      ["Journal Mode", "WAL", "Concurrent readers + one writer."],
      ["Synchronous", "NORMAL (inherited)", "Acceptable for WAL."],
      ["Foreign Keys (runtime)", "PRAGMA foreign_keys = 0", "CRITICAL — constraints declared but not enforced."],
      ["Row Factory", "sqlite3.Row", "Named-column access across codebase."],
      ["Access Layer", "core/db/connection.py :: get_connection()", "Single chokepoint — good for policy injection."],
      ["Schema Guardrail", "REQUIRED_TABLES validation on connect", "10 tables asserted at session open."],
    ],
    [2600, 3200, 3560]
  ),
  h2("1.2 Implications"),
  bullet("SQLite + WAL means we can scale reads horizontally without a server, but all writes serialise."),
  bullet("Foreign-key enforcement is OFF at the runtime pragma level despite 26 CASCADE constraints present in DDL. This is the single highest structural risk detected in Phase 62 and is escalated in Section 6."),
  bullet("All code paths route through get_connection(), giving us exactly one place to install global pragmas (FK, journal_mode)."),
];

// Section 2 — Structural Map
const sec2 = [
  h1("2. Structural Map"),
  p("The substrate contains 53 tables grouped into seven functional strata. The map below is the canonical lens used for all subsequent interrogation."),
  h2("2.1 Table Strata"),
  makeTable(
    ["Stratum", "Representative Tables", "Role"],
    [
      ["Intake / Raw", "artifacts, artifacts_fts, artifacts_archive, discovery_targets, pipeline_runs", "Ingestion, crawl, fulltext."],
      ["Perception", "signals, signals_archive, signal_baselines, signal_entities, signal_flags", "Gravity engine + NER surface."],
      ["Cognition", "events, events_archive, events_fts, clusters (logical col)", "Event formation + clustering."],
      ["Actor Graph", "actors, actor_weights, actor_events, actor_signals, actor_coalitions, actor_network_metrics", "Canonical entity layer."],
      ["Relationships", "entity_relationships, relationships, graph_nodes, graph_edges, network_emergence", "Explicit edges + graph cache."],
      ["Casework", "cases, case_actors, case_artifacts, case_events, case_signals, case_feedback", "Operator workspace."],
      ["Governance", "sentinel_alerts, priorities, pii_audits, provenance, observer_promotion_log, wiki_*", "Oversight, audit, promotion trail."],
    ],
    [1900, 4200, 3260]
  ),
  h2("2.2 Canonical Fact Tables"),
  bullet("signals — 21 columns; PK signal_id; NOT NULL external_id; carries relevance_score, gravity_score, cluster_id."),
  bullet("actors — PK actor_id; carries type, confidence_score, automated flag, gravity_score."),
  bullet("signal_actors — junction (signal_id, actor_id); the operational load-bearing edge."),
  bullet("entity_relationships — typed edges (subject_actor_id, object_actor_id, relation_type, confidence); source-citable via artifact/event FKs."),
];

// Section 3 — Volumetric Analysis
const sec3 = [
  h1("3. Volumetric Analysis"),
  p("Row counts were captured from a single transactional snapshot. All values reflect production state at report date."),
  makeTable(
    ["Table", "Rows", "Note"],
    [
      ["artifacts", "564,953", "Raw ingestion surface — dominates on-disk footprint."],
      ["signal_entities", "54,704", "Raw NER extractions (pre-promotion)."],
      ["signals", "51,155", "Gravity-scored perception units."],
      ["actors", "1,014", "Canonical entity layer (post-dedup)."],
      ["sentinel_alerts", "863", "Mixed statistical + correlation + observer co-occurrence."],
      ["signal_actors", "3,806", "Junction — primary structural edge density."],
      ["events", "490", "Clustered cognition units."],
      ["pipeline_runs", "416", "Orchestration ledger."],
      ["event_actors", "345", "Event participation edges."],
      ["entity_relationships", "319", "Explicit typed relationships."],
      ["discovery_targets", "168", "Crawl seeds."],
      ["observer_promotion_log", "77", "Autonomous promotions since Phase 65."],
      ["cases", "22", "Active + archived casework."],
      ["priorities", "0", "Unused (legacy)."],
    ],
    [3400, 1600, 4360]
  ),
  p("On-disk footprint: 579.4 MB. Artifacts dominate (≥ 90%); the perception and actor layers are small by comparison and cheap to traverse in memory.", { run: { italics: true, color: "595959" } }),
];

// Section 4 — Relationship Density
const sec4 = [
  h1("4. Relationship Density"),
  p("Two parallel edge surfaces exist: implicit co-occurrence edges via signal_actors, and explicit typed edges via entity_relationships. The anti-pattern to identify here is hub concentration — where a small number of generic nodes absorb a disproportionate share of edges, degrading traversal fidelity."),
  h2("4.1 Top Signal-Actor Hubs"),
  makeTable(
    ["Actor ID", "Name", "signal_actor Links"],
    [
      ["39", "Directorate for Priority Crime Investigation (Hawks)", "277"],
      ["51", "National Treasury", "224"],
      ["43", "African National Congress", "192"],
      ["34", "Cyril Ramaphosa", "148"],
      ["36", "Democratic Alliance", "141"],
      ["35", "Eskom", "116"],
      ["877", "company (generic)", "89"],
      ["1119", "Hawk (duplicate of 39)", "81"],
      ["1113", "Western Cape", "66"],
      ["1112", "Cape Town", "66"],
    ],
    [1500, 5360, 2500]
  ),
  h2("4.2 Edge Surfaces"),
  bullet("signal_actors: 3,806 edges across 1,014 actors — mean ≈ 3.75 edges/actor; skew is extreme (top 10 nodes hold ≥ 40% of edges)."),
  bullet("entity_relationships: 319 typed edges. Dominant relation_type is co_occurrence (spaCy-extracted, confidence 0.2); high-confidence ACCUSED_OF edges are rare but operationally critical."),
  bullet("event_actors: 345 edges linking actors into event objects."),
  h2("4.3 Hub-Poisoning Risk"),
  p("Actor 877 (\"company\") and 1119 (\"Hawk\") are contamination artefacts — the former a generic noun, the latter a duplicate of Actor 39. Both will attract routing in naive shortest-path traversals. Phase 66 anti-hub filtering must treat any node with > 500 direct edges as non-routable, and duplicate canonicalisation must reclaim Actor 1119 into Actor 39."),
];

// Section 5 — Integrity Checks
const sec5 = [
  h1("5. Integrity Checks"),
  h2("5.1 SQLite Native"),
  makeTable(
    ["Check", "Result", "Interpretation"],
    [
      ["PRAGMA integrity_check", "ok", "B-tree, page chain, and index structure are consistent."],
      ["PRAGMA journal_mode", "wal", "Matches expected concurrency contract."],
      ["PRAGMA foreign_keys (session)", "0", "FK enforcement OFF at runtime (critical finding)."],
      ["Declared FK count", "32 total / 26 CASCADE", "Strong intent in schema — unenforced in practice."],
    ],
    [3200, 2200, 3960]
  ),
  h2("5.2 Foreign-Key Check"),
  p("PRAGMA foreign_key_check returned 241,393 orphan references across child tables, dominated by actor_network_metrics referencing deleted parent actors. These rows predate the Phase 63 dedup pass and represent silent drift accumulated while FKs were disabled."),
  h2("5.3 Orphan Holding Tables"),
  bullet("orphaned_entity_relationships — holds rows whose subject/object actor was removed."),
  bullet("orphaned_event_actors — same pattern for event-actor joins."),
  p("These tables are a symptom, not a cure. Their existence demonstrates that the team previously treated FK violations as data to preserve rather than as constraint breaches to prevent. Activating PRAGMA foreign_keys = ON at connection open will halt the drift; cleaning the existing 241,393 orphans is a separate remediation sprint."),
];

// Section 6 — Canonical Outputs
const sec6 = [
  h1("6. Canonical Outputs"),
  p("The three artefacts below are the formal deliverables of Phase 62. All downstream phases (63 – 66) reference this baseline.", { run: { italics: true } }),

  h2("6.1 State Summary"),
  makeTable(
    ["Stratum", "Primary Table", "Rows", "Operational State"],
    [
      ["Intake", "artifacts", "564,953", "HEALTHY — FTS attached, archive rotating."],
      ["Perception", "signals", "51,155", "HEALTHY — gravity + relevance populated."],
      ["NER Surface", "signal_entities", "54,704", "WATCH — noise (MW/FRP) blocked post-Phase 65."],
      ["Cognition", "events", "490", "HEALTHY — cluster_id present on signals."],
      ["Actor Graph", "actors", "1,014", "WATCH — duplicates remain (e.g. 39 vs 1119)."],
      ["Edges (implicit)", "signal_actors", "3,806", "HEALTHY — skewed but functional."],
      ["Edges (explicit)", "entity_relationships", "319", "WATCH — 241K orphan refs (table-wide)."],
      ["Casework", "cases", "22", "HEALTHY — Case Alpha active."],
      ["Oversight", "sentinel_alerts", "863", "HEALTHY — Observer feed wired."],
      ["Autonomy", "observer_promotion_log", "77", "HEALTHY — Phase 65 promotions flowing."],
    ],
    [2000, 2200, 1200, 3960]
  ),

  h2("6.2 Edge List Summary"),
  makeTable(
    ["Edge Surface", "Kind", "Edge Count", "Source"],
    [
      ["signal_actors", "Implicit (shared signal)", "3,806", "NER + pattern match."],
      ["event_actors", "Implicit (event membership)", "345", "Event formation pipeline."],
      ["entity_relationships", "Explicit typed", "319", "spaCy extraction + manual."],
      ["  • co_occurrence", "Typed edge (low conf 0.2)", "majority", "extraction_method = 'spacy'."],
      ["  • ACCUSED_OF / named", "Typed edge (conf ≥ 0.6)", "minority", "High-fidelity, Case-Alpha relevant."],
      ["graph_edges (cache)", "Derived / materialised", "n/a", "Reserved for Phase 66 traversal cache."],
    ],
    [3200, 2600, 1600, 1960]
  ),

  h2("6.3 Integrity Verdict"),
  makeTable(
    ["Dimension", "Verdict", "Rationale"],
    [
      ["Physical integrity", "PASS", "integrity_check = ok; WAL checkpoint clean."],
      ["Schema declaration", "PASS", "26 CASCADE constraints formally declared."],
      ["Runtime enforcement", "FAIL", "PRAGMA foreign_keys = 0 at session open."],
      ["Referential health", "FAIL", "241,393 orphan FK references detected."],
      ["Canonicalisation", "WATCH", "Actor duplicates still present (39 ↔ 1119)."],
      ["Concurrency posture", "PASS", "WAL + sqlite3.Row; single writer contract intact."],
      ["Audit surface", "PASS", "provenance + pii_audits + observer_promotion_log live."],
    ],
    [2600, 1400, 5360]
  ),
  p("Overall Verdict: AMBER. The substrate is physically sound but structurally under-enforced. The gap between declared intent (26 CASCADE FKs) and runtime behaviour (FK = 0) is the defining risk of the current architecture.", { run: { bold: true, color: "C00000" } }),
];

// Section 7 — Recommended Action
const sec7 = [
  h1("7. Recommended Action"),
  h2("7.1 Immediate (within current sprint)"),
  bullet("Activate PRAGMA foreign_keys = ON globally inside get_connection() — single-line change, activates all 26 CASCADE constraints, halts further orphan accrual. [Tracked as Phase 63 Fix 5c.]"),
  bullet("Canonicalise Actor 1119 (\"Hawk\") into Actor 39 (Directorate for Priority Crime Investigation) and repoint signal_actors / event_actors. Removes a primary hub-poisoning vector ahead of Phase 66."),
  bullet("Quarantine generic-noun actors (e.g. 877 \"company\") behind a non-routable flag; exclude from all traversal queries."),
  h2("7.2 Near-Term (Phases 63 – 65)"),
  bullet("Stress-test CASCADE behaviour under FK-ON with 500+ synthetic signals and surgical super-hub deletion — verify atomic cascade and rollback. [Tracked as Phase 64 Sigma.]"),
  bullet("Deploy the Observer Daemon with quality gates (length, acronym, digit-heavy, webpage-title, media-outlet) to prevent repeat NER noise events like MW/FRP. [Tracked as Phase 65.]"),
  bullet("Remediate the 241,393 orphan FK references in a dedicated pass, preferring DELETE over repoint where the parent is provably absent."),
  h2("7.3 Medium-Term (Phases 66 – 67)"),
  bullet("Build the native graph-traversal engine (scripts/network_emergence.py) using SQLite recursive CTEs with an anti-hub degree filter. [Tracked as Phase 66.]"),
  bullet("Introduce time-based gravity decay (entropy) for uncorroborated signals to prevent stale-hub inflation. [Tracked as Phase 67.]"),
  bullet("Materialise graph_edges as a read-optimised cache once the traversal engine stabilises, to amortise recursive CTE cost for repeat queries."),
  h2("7.4 Acceptance Criteria"),
  bullet("foreign_key_check returns 0 violations."),
  bullet("PRAGMA foreign_keys returns 1 for every connection opened by get_connection()."),
  bullet("No actor with degree > 500 in signal_actors is referenced by active traversal output."),
  bullet("Observer promotions carry provenance rows and appear in observer_promotion_log within the same transaction."),
  p("— End of Report —", {
    run: { italics: true, color: "7F7F7F" },
    alignment: AlignmentType.CENTER,
  }),
];

// ---------- assemble ----------
const doc = new Document({
  creator: "FORGE Command Architect",
  title: "FORGE Phase 62 — Substrate Interrogation Report",
  description: "Structural audit of the cognitive data layer.",
  styles: {
    default: { document: { run: { font: "Calibri", size: 22 } } },
    paragraphStyles: [
      {
        id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 32, bold: true, color: NAVY, font: "Calibri" },
        paragraph: { spacing: { before: 320, after: 200 }, outlineLevel: 0 },
      },
      {
        id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 26, bold: true, color: STEEL, font: "Calibri" },
        paragraph: { spacing: { before: 240, after: 140 }, outlineLevel: 1 },
      },
      {
        id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 22, bold: true, italics: true, color: "404040", font: "Calibri" },
        paragraph: { spacing: { before: 180, after: 100 }, outlineLevel: 2 },
      },
    ],
  },
  numbering: {
    config: [
      {
        reference: "bullets",
        levels: [{
          level: 0, format: LevelFormat.BULLET, text: "\u2022", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } },
        }],
      },
    ],
  },
  sections: [
    {
      properties: {
        page: {
          size: { width: 12240, height: 15840 },
          margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
        },
      },
      headers: {
        default: new Header({
          children: [new Paragraph({
            tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
            children: [
              new TextRun({ text: "FORGE // PHASE 62 — SUBSTRATE INTERROGATION", bold: true, color: NAVY, size: 18 }),
              new TextRun({ text: "\tINTERNAL // OPERATIONAL", color: "C00000", bold: true, size: 18 }),
            ],
          })],
        }),
      },
      footers: {
        default: new Footer({
          children: [new Paragraph({
            tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
            children: [
              new TextRun({ text: `FORGE Command • ${today}`, size: 18, color: "595959" }),
              new TextRun({ text: "\tPage ", size: 18, color: "595959" }),
              new TextRun({ children: [PageNumber.CURRENT], size: 18, color: "595959" }),
              new TextRun({ text: " of ", size: 18, color: "595959" }),
              new TextRun({ children: [PageNumber.TOTAL_PAGES], size: 18, color: "595959" }),
            ],
          })],
        }),
      },
      children: [
        ...coverChildren,
        ...tocChildren,
        ...sec1, ...sec2, ...sec3, ...sec4, ...sec5, ...sec6, ...sec7,
      ],
    },
  ],
});

Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync(OUT, buf);
  console.log("WROTE", OUT, buf.length, "bytes");
});
