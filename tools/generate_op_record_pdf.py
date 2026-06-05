#!/usr/bin/env python3
from __future__ import annotations
"""
tools/generate_op_record_pdf.py
================================
Generates docs/OP_RECORD_PRINT.pdf — same content as the HTML operation
record but as a portable PDF using reportlab (Platypus) + matplotlib.

Usage:
    python tools/generate_op_record_pdf.py
    python tools/generate_op_record_pdf.py --out docs/my_brief.pdf
"""

import argparse
import importlib
import io
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_PATH  = Path(__file__).resolve().parent.parent / "database.db"
OUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "OP_RECORD_PRINT.pdf"


# ── Dependency bootstrap ──────────────────────────────────────────────────────
def _ensure(package: str, import_name: str | None = None) -> None:
    name = import_name or package
    try:
        importlib.import_module(name)
    except ImportError:
        print(f"Installing {package}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", package, "-q"])


_ensure("reportlab")
_ensure("matplotlib")
_ensure("networkx")

# ── Now safe to import ────────────────────────────────────────────────────────
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import networkx as nx
import numpy as np

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    BaseDocTemplate, Frame, HRFlowable, Image, PageTemplate,
    Paragraph, Spacer, Table, TableStyle, KeepTogether,
)
from reportlab.platypus.flowables import Flowable

W, H = A4
MARGIN = 2.2 * cm

# ── Colour palette ────────────────────────────────────────────────────────────
INK        = colors.HexColor("#1a1a2e")
INK_MID    = colors.HexColor("#2d2d4e")
INK_SOFT   = colors.HexColor("#6a6a8a")
ACCENT     = colors.HexColor("#b8621a")
ACCENT2    = colors.HexColor("#1a5c8a")
BORDER     = colors.HexColor("#d4cfc4")
BG_ALT     = colors.HexColor("#f4f0e8")
VERIFIED_C = colors.HexColor("#1a6b3a")
CAND_C     = colors.HexColor("#aaaaaa")
HIGH_C     = colors.HexColor("#155724")
HIGH_BG    = colors.HexColor("#d4edda")
MED_C      = colors.HexColor("#856404")
MED_BG     = colors.HexColor("#fff3cd")
LOW_C      = colors.HexColor("#721c24")
LOW_BG     = colors.HexColor("#f8d7da")
WHITE      = colors.white


# ── Styles ────────────────────────────────────────────────────────────────────
def build_styles():
    base = getSampleStyleSheet()
    S = {}

    def s(name, **kw):
        S[name] = ParagraphStyle(name, **kw)

    s("classify",
      fontName="Courier", fontSize=6.5, textColor=ACCENT,
      alignment=TA_CENTER, spaceAfter=8,
      borderColor=ACCENT, borderWidth=0.5, borderPadding=4,
      borderRadius=2)

    s("wordmark",
      fontName="Courier", fontSize=7.5, textColor=INK_SOFT,
      letterSpacing=2, spaceAfter=2)

    s("doc_title",
      fontName="Helvetica-Bold", fontSize=22, textColor=INK,
      spaceAfter=2, leading=26)

    s("doc_subtitle",
      fontName="Helvetica", fontSize=11, textColor=INK_SOFT,
      spaceAfter=4)

    s("doc_meta",
      fontName="Courier", fontSize=7, textColor=INK_SOFT,
      spaceAfter=16)

    s("section_head",
      fontName="Helvetica-Bold", fontSize=10, textColor=INK,
      spaceBefore=20, spaceAfter=6,
      letterSpacing=1.5, textTransform="uppercase")

    s("thread_title",
      fontName="Helvetica-Bold", fontSize=12, textColor=INK,
      spaceBefore=18, spaceAfter=6,
      borderPadding=(0, 0, 0, 8),
      borderColor=ACCENT, borderWidth=0,
      leftIndent=10)

    s("lede",
      fontName="Times-Roman", fontSize=11.5, textColor=INK_MID,
      leading=18, spaceAfter=10, alignment=TA_JUSTIFY)

    s("body",
      fontName="Times-Roman", fontSize=10, textColor=INK_MID,
      leading=16, spaceAfter=8, alignment=TA_JUSTIFY)

    s("body_italic",
      fontName="Times-Italic", fontSize=10, textColor=INK_SOFT,
      leading=16, spaceAfter=8, alignment=TA_JUSTIFY)

    s("finding_label",
      fontName="Courier", fontSize=6.5, textColor=INK_SOFT,
      spaceAfter=2, letterSpacing=1)

    s("finding_text",
      fontName="Times-Roman", fontSize=9.5, textColor=INK_MID,
      leading=14, spaceAfter=0, alignment=TA_JUSTIFY)

    s("note_text",
      fontName="Helvetica", fontSize=9, textColor=INK_MID,
      leading=13.5, spaceAfter=0, alignment=TA_JUSTIFY)

    s("metric_val",
      fontName="Helvetica-Bold", fontSize=20, textColor=INK,
      alignment=TA_CENTER, spaceAfter=0, leading=22)

    s("metric_label",
      fontName="Courier", fontSize=6.5, textColor=INK_SOFT,
      alignment=TA_CENTER, letterSpacing=1, spaceAfter=0)

    s("footer",
      fontName="Courier", fontSize=6.5, textColor=INK_SOFT,
      alignment=TA_CENTER)

    s("toc_item",
      fontName="Helvetica", fontSize=9, textColor=INK_MID,
      spaceAfter=2)

    s("req_item",
      fontName="Times-Roman", fontSize=9.5, textColor=INK_MID,
      leading=14, leftIndent=12, spaceAfter=4)

    return S


# ── Custom Flowables ──────────────────────────────────────────────────────────

class HRule(Flowable):
    def __init__(self, width=None, color=BORDER, thickness=0.5):
        super().__init__()
        self._w  = width
        self.color = color
        self.thickness = thickness
        self.height = 1 + thickness

    def wrap(self, aW, aH):
        self.width = self._w or aW
        return self.width, self.height

    def draw(self):
        self.canv.setStrokeColor(self.color)
        self.canv.setLineWidth(self.thickness)
        self.canv.line(0, 0, self.width, 0)


class AccentBar(Flowable):
    """Vertical accent bar on the left of thread titles."""
    def __init__(self, text, style, bar_color=ACCENT):
        super().__init__()
        self._text      = text
        self._style     = style
        self._bar_color = bar_color
        self._para      = Paragraph(text, style)

    def wrap(self, aW, aH):
        w, h = self._para.wrap(aW - 12, aH)
        self.width  = aW
        self.height = h
        return aW, h

    def draw(self):
        self.canv.setFillColor(self._bar_color)
        self.canv.rect(0, 0, 3, self.height, fill=1, stroke=0)
        self._para.drawOn(self.canv, 12, 0)


class FindingBlock(Flowable):
    """Bordered finding card."""
    def __init__(self, label, text, styles, status="normal"):
        super().__init__()
        self._label  = label
        self._text   = text
        self._styles = styles
        self._status = status  # normal | demoted | note

        color_map = {
            "normal":  ACCENT,
            "demoted": colors.HexColor("#cccccc"),
            "note":    ACCENT2,
        }
        self._bar   = color_map.get(status, ACCENT)
        self._label_p = Paragraph(label,  styles["finding_label"])
        self._text_p  = Paragraph(text,   styles["finding_text"] if status != "note"
                                           else styles["note_text"])

    def wrap(self, aW, aH):
        pad  = 10
        inner_w = aW - pad * 2 - 4
        lw, lh  = self._label_p.wrap(inner_w, 9999)
        tw, th  = self._text_p.wrap(inner_w, 9999)
        self.width  = aW
        self.height = lh + th + pad * 2 + 4
        self._lh = lh
        self._th = th
        return aW, self.height

    def draw(self):
        c   = self.canv
        pad = 10
        h   = self.height

        # Background + border
        c.setFillColor(colors.HexColor("#ffffff"))
        c.setStrokeColor(BORDER)
        c.setLineWidth(0.5)
        c.roundRect(0, 0, self.width, h, 2, fill=1, stroke=1)

        # Left accent bar
        c.setFillColor(self._bar)
        c.rect(0, 0, 3.5, h, fill=1, stroke=0)

        if self._status == "demoted":
            c.setFillColor(colors.HexColor("#f0f0f0"))
            c.rect(0, 0, self.width, h, fill=1, stroke=0)
            c.setFillColor(self._bar)
            c.rect(0, 0, 3.5, h, fill=1, stroke=0)

        # Label
        self._label_p.drawOn(c, pad + 4, h - pad - self._lh)
        # Text
        self._text_p.drawOn(c, pad + 4, h - pad - self._lh - self._th - 2)


class ConfTag(Flowable):
    """Small inline confidence tag."""
    def __init__(self, text, level="high"):
        super().__init__()
        self._text  = text
        self._level = level
        self.width  = 60
        self.height = 12

    def wrap(self, aW, aH):
        return self.width, self.height

    def draw(self):
        colors_map = {
            "high":   (HIGH_BG,  HIGH_C),
            "medium": (MED_BG,   MED_C),
            "low":    (LOW_BG,   LOW_C),
        }
        bg, fg = colors_map.get(self._level, (BG_ALT, INK_SOFT))
        self.canv.setFillColor(bg)
        self.canv.setStrokeColor(fg)
        self.canv.setLineWidth(0.4)
        self.canv.roundRect(0, 0, self.width, self.height, 2, fill=1, stroke=1)
        self.canv.setFillColor(fg)
        self.canv.setFont("Courier", 5.5)
        self.canv.drawCentredString(self.width / 2, 3.5, self._text.upper())


# ── Data loader ───────────────────────────────────────────────────────────────
def load_data() -> dict:
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.row_factory = sqlite3.Row

    actors = conn.execute("""
        SELECT DISTINCT a.actor_id, a.name, a.type,
               ROUND(a.confidence_score,3) as confidence_score
        FROM actors a
        WHERE a.confidence_score IS NOT NULL
          AND a.actor_id IN (
              SELECT subject_actor_id FROM entity_relationships
              UNION SELECT object_actor_id FROM entity_relationships)
        ORDER BY a.confidence_score DESC
    """).fetchall()

    edges = conn.execute("""
        SELECT er.relation_type, ROUND(er.confidence,3) as confidence,
               er.extraction_method,
               a.actor_id as source_id, a.name as source_name,
               b.actor_id as target_id, b.name as target_name
        FROM entity_relationships er
        JOIN actors a ON er.subject_actor_id = a.actor_id
        JOIN actors b ON er.object_actor_id  = b.actor_id
        ORDER BY er.extraction_method DESC, er.confidence DESC
    """).fetchall()

    cases = conn.execute("""
        SELECT c.case_id, c.name, COUNT(cs.signal_id) as n_signals,
               ROUND(AVG(s.gravity_score),3) as avg_g,
               ROUND(MAX(s.gravity_score),3) as max_g
        FROM cases c
        LEFT JOIN case_signals cs ON c.case_id=cs.case_id
        LEFT JOIN signals s ON cs.signal_id=s.signal_id
        WHERE c.status='active' AND c.case_id IN (7,8,9,10)
        GROUP BY c.case_id
    """).fetchall()

    totals = conn.execute("""
        SELECT COUNT(*) as total, COUNT(DISTINCT source) as sources
        FROM signals
    """).fetchone()

    rel_counts = conn.execute("""
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


# ── Graph figure ──────────────────────────────────────────────────────────────
TYPE_COLOURS = {
    "person":          "#c0392b",
    "institution":     "#2980b9",
    "political_party": "#8e44ad",
    "government":      "#27ae60",
    "movement":        "#e67e22",
    "media":           "#16a085",
    "other":           "#7f8c8d",
}

def build_graph_image(actors: list, edges: list) -> io.BytesIO:
    G = nx.DiGraph()
    node_meta = {a["actor_id"]: a for a in actors}

    for a in actors:
        G.add_node(a["actor_id"], name=a["name"], type=a["type"],
                   conf=a["confidence_score"] or 0.3)

    edge_meta = {}
    for e in edges:
        if e["source_id"] in node_meta and e["target_id"] in node_meta:
            G.add_edge(e["source_id"], e["target_id"])
            edge_meta[(e["source_id"], e["target_id"])] = e

    if len(G.nodes) == 0:
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.text(0.5, 0.5, "No graph data", ha="center", va="center",
                fontsize=12, color="#888")
        ax.axis("off")
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        return buf

    # Layout
    try:
        pos = nx.spring_layout(G, k=2.2, iterations=120, seed=42)
    except Exception:
        pos = nx.circular_layout(G)

    fig, ax = plt.subplots(figsize=(11, 6.5))
    fig.patch.set_facecolor("#f8f6f0")
    ax.set_facecolor("#f8f6f0")

    # Separate verified vs candidate edges
    verified_edges = [(u, v) for u, v in G.edges()
                      if edge_meta.get((u, v), {}).get("extraction_method") == "manual"]
    candidate_edges = [(u, v) for u, v in G.edges()
                       if (u, v) not in verified_edges]

    def edge_width(u, v):
        conf = edge_meta.get((u, v), {}).get("confidence", 0.3)
        return 0.8 + conf * 2.5

    # Draw candidate edges (dashed, grey)
    for u, v in candidate_edges:
        x0, y0 = pos[u]; x1, y1 = pos[v]
        ax.annotate("", xy=(x1, y1), xytext=(x0, y0),
                    arrowprops=dict(arrowstyle="-|>", color="#bbbbbb",
                                   lw=edge_width(u, v) * 0.6,
                                   linestyle="dashed",
                                   connectionstyle="arc3,rad=0.08"))

    # Draw verified edges (solid, green)
    for u, v in verified_edges:
        x0, y0 = pos[u]; x1, y1 = pos[v]
        ax.annotate("", xy=(x1, y1), xytext=(x0, y0),
                    arrowprops=dict(arrowstyle="-|>", color="#1a6b3a",
                                   lw=edge_width(u, v),
                                   connectionstyle="arc3,rad=0.08"))

    # Edge labels (verified, high conf only)
    for u, v in verified_edges:
        meta = edge_meta.get((u, v), {})
        if meta.get("confidence", 0) >= 0.3:
            x0, y0 = pos[u]; x1, y1 = pos[v]
            mx, my = (x0 + x1) / 2, (y0 + y1) / 2
            ax.text(mx, my, meta.get("relation_type", ""),
                    fontsize=5.5, ha="center", va="center",
                    color="#1a6b3a", fontfamily="monospace",
                    bbox=dict(boxstyle="round,pad=0.15", fc="#f8f6f0",
                              ec="none", alpha=0.85))

    # Nodes
    for node_id in G.nodes():
        a   = node_meta[node_id]
        col = TYPE_COLOURS.get(a["type"], "#7f8c8d")
        conf = a["conf"]
        sz   = 180 + conf * 400

        ax.scatter(*pos[node_id], s=sz, c=col, alpha=0.25,
                   edgecolors=col, linewidths=2.0, zorder=3)

        label = a["name"]
        if len(label) > 18:
            words = label.split()
            mid   = max(1, len(words) // 2)
            label = " ".join(words[:mid]) + "\n" + " ".join(words[mid:])

        fw = "bold" if conf >= 0.6 else "normal"
        ax.text(pos[node_id][0], pos[node_id][1] + 0.06,
                label, fontsize=7, ha="center", va="bottom",
                fontweight=fw, color="#1a1a2e",
                bbox=dict(boxstyle="round,pad=0.2", fc="white",
                          ec="none", alpha=0.75))

        ax.text(pos[node_id][0], pos[node_id][1] - 0.06,
                f"{a['type']} · {conf:.2f}",
                fontsize=5.5, ha="center", va="top",
                color="#888888", fontfamily="monospace")

    # Legend
    legend_patches = []
    seen_types = {node_meta[n]["type"] for n in G.nodes()}
    for t in ["person", "institution", "political_party", "government", "other"]:
        if t in seen_types:
            legend_patches.append(
                mpatches.Patch(color=TYPE_COLOURS.get(t, "#888"), label=t.replace("_", " ").title())
            )
    legend_patches += [
        plt.Line2D([0], [0], color="#1a6b3a", lw=2, label="Verified edge"),
        plt.Line2D([0], [0], color="#bbb",    lw=1.5, linestyle="--", label="Candidate edge"),
    ]
    ax.legend(handles=legend_patches, loc="lower right",
              fontsize=6.5, framealpha=0.85,
              facecolor="white", edgecolor="#d4cfc4")

    ax.set_xlim(-1.5, 1.5)
    ax.set_ylim(-1.4, 1.4)
    ax.axis("off")
    ax.set_title("FORGE Entity Relationship Graph", fontsize=9,
                 fontfamily="monospace", color="#4a4a6a", pad=8)

    plt.tight_layout(pad=0.3)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=160, bbox_inches="tight",
                facecolor="#f8f6f0")
    plt.close(fig)
    buf.seek(0)
    return buf


# ── Case gravity chart ────────────────────────────────────────────────────────
def build_gravity_chart(cases: list) -> io.BytesIO:
    labels  = [f"Case {c['case_id']}" for c in cases]
    avg_g   = [c["avg_g"] or 0 for c in cases]
    max_g   = [c["max_g"] or 0 for c in cases]
    x       = np.arange(len(labels))
    width   = 0.35

    fig, ax = plt.subplots(figsize=(7, 2.8))
    fig.patch.set_facecolor("#f8f6f0")
    ax.set_facecolor("#f8f6f0")

    b1 = ax.bar(x - width/2, avg_g, width, label="Avg Gravity",
                color="#2980b9", alpha=0.7, edgecolor="white")
    b2 = ax.bar(x + width/2, max_g, width, label="Max Gravity",
                color="#b8621a", alpha=0.7, edgecolor="white")

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=7.5, fontfamily="monospace")
    ax.set_ylim(0, 1.0)
    ax.set_yticks([0, 0.25, 0.50, 0.75, 1.0])
    ax.tick_params(axis="y", labelsize=7)
    ax.yaxis.grid(True, color="#d4cfc4", linestyle="-", linewidth=0.5)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#d4cfc4")
    ax.legend(fontsize=7, framealpha=0.85, facecolor="white",
              edgecolor="#d4cfc4", loc="upper right")
    ax.set_title("Case Signal Gravity Distribution", fontsize=8,
                 fontfamily="monospace", color="#4a4a6a")

    plt.tight_layout(pad=0.5)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                facecolor="#f8f6f0")
    plt.close(fig)
    buf.seek(0)
    return buf


# ── Page template ─────────────────────────────────────────────────────────────
def _header_footer(canvas, doc, generated):
    canvas.saveState()
    w, h = A4
    # Top classification bar
    canvas.setFillColor(ACCENT)
    canvas.setFont("Courier", 6)
    canvas.drawCentredString(w / 2, h - 14 * mm,
        "ANALYST WORKING COPY — NOT FOR DISTRIBUTION — FORGE v1.1.3")
    # Bottom: page number + date
    canvas.setFillColor(INK_SOFT)
    canvas.setFont("Courier", 6)
    canvas.drawString(MARGIN, 12 * mm, f"Generated: {generated}")
    canvas.drawCentredString(w / 2, 12 * mm,
        "FORGE  ·  Foundational Open Research & Graph Engine  ·  Stable 1.1.3")
    canvas.drawRightString(w - MARGIN, 12 * mm, f"Page {doc.page}")
    # Rule under header
    canvas.setStrokeColor(BORDER)
    canvas.setLineWidth(0.4)
    canvas.line(MARGIN, h - 16 * mm, w - MARGIN, h - 16 * mm)
    canvas.line(MARGIN, 16 * mm, w - MARGIN, 16 * mm)
    canvas.restoreState()


# ── Main builder ──────────────────────────────────────────────────────────────
def build_pdf(data: dict, out_path: Path) -> None:
    S        = build_styles()
    actors   = data["actors"]
    edges    = data["edges"]
    cases    = data["cases"]
    totals   = data["totals"]
    generated = data["generated"]
    verified  = data["rel_counts"].get("manual", 0)
    candidate = sum(v for k, v in data["rel_counts"].items() if k != "manual")

    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc = BaseDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=2.8 * cm, bottomMargin=2.4 * cm,
        title="FORGE Operation Record 2026-06-03",
        author="FORGE Intelligence System",
        subject="Magaqa / Eskom / KZN HAWKS — Adversarial Review",
    )

    frame = Frame(MARGIN, 2.4 * cm,
                  W - 2 * MARGIN, H - 5.2 * cm,
                  id="body")
    tpl = PageTemplate(
        id="main", frames=[frame],
        onPage=lambda c, d: _header_footer(c, d, generated),
    )
    doc.addPageTemplates([tpl])

    story = []
    SP = Spacer

    # ── Classification banner ──────────────────────────────────────────────
    story.append(Paragraph(
        "ANALYST WORKING COPY  ·  NOT FOR DISTRIBUTION  ·  FORGE v1.1.3",
        S["classify"]))

    # ── Header ────────────────────────────────────────────────────────────
    story.append(Paragraph("FORGE  ·  Foundational Open Research &amp; Graph Engine",
                            S["wordmark"]))
    story.append(Paragraph("Operation Record", S["doc_title"]))
    story.append(Paragraph("Magaqa &nbsp;·&nbsp; Eskom &nbsp;·&nbsp; KZN HAWKS &nbsp;·&nbsp; Adversarial Review",
                            S["doc_subtitle"]))
    story.append(Paragraph(
        f"Sealed: {generated} &nbsp;&nbsp; Build: Stable 1.1.3 &nbsp;&nbsp; "
        f"Corpus: {totals['total']:,} signals &nbsp;&nbsp; "
        f"Graph: {verified} verified · {candidate} candidate edges",
        S["doc_meta"]))
    story.append(HRule())
    story.append(SP(1, 8))

    # ── Preface ───────────────────────────────────────────────────────────
    story.append(Paragraph(
        "What follows is a point-in-time record of three open intelligence threads maintained "
        "by FORGE as of June 2026. These threads are not unrelated. They share a geography, "
        "a period, and a pattern: institutions designed to hold power accountable appear, in "
        "varying degrees, to be compromised by the power they are meant to hold.",
        S["lede"]))
    story.append(Paragraph(
        "The analysis was subjected to independent adversarial review — findings were "
        "re-derived from raw signals without reference to prior conclusions, and only those "
        "that independently converged were retained at high confidence. What did not survive "
        "that test is marked plainly. The graph below represents the current state of the "
        "entity relationship model. Solid edges survived hostile review. Dashed edges are "
        "candidates awaiting the same.",
        S["body"]))

    story.append(SP(1, 10))

    # ── Section I: Graph ──────────────────────────────────────────────────
    story.append(Paragraph('<font color="#b8621a">I.</font>  THE ENTITY GRAPH',
                            S["section_head"]))
    story.append(HRule(thickness=0.3))
    story.append(SP(1, 6))

    print("  Rendering entity graph...")
    graph_buf = build_graph_image(actors, edges)
    graph_img = Image(graph_buf, width=W - 2 * MARGIN, height=9 * cm)
    story.append(graph_img)

    story.append(SP(1, 6))
    story.append(Paragraph(
        "Nodes are sized by confidence score and coloured by actor type. "
        "Edge weight reflects relationship confidence. Solid green edges are analyst-verified. "
        "Dashed grey edges are machine-extracted candidates pending hostile review.",
        S["body_italic"]))

    story.append(SP(1, 14))

    # ── Section II: Corpus metrics ────────────────────────────────────────
    story.append(Paragraph('<font color="#b8621a">II.</font>  CORPUS STATE',
                            S["section_head"]))
    story.append(HRule(thickness=0.3))
    story.append(SP(1, 8))

    col_w = (W - 2 * MARGIN) / 4
    metric_data = [
        [Paragraph(f"{totals['total']:,}", S["metric_val"]),
         Paragraph(f"{totals['sources']}",  S["metric_val"]),
         Paragraph(f"{verified + candidate}", S["metric_val"]),
         Paragraph(f"{verified}",            S["metric_val"])],
        [Paragraph("Total Signals",  S["metric_label"]),
         Paragraph("Source Channels", S["metric_label"]),
         Paragraph("Graph Edges",    S["metric_label"]),
         Paragraph("Verified Edges", S["metric_label"])],
    ]
    metric_tbl = Table(metric_data, colWidths=[col_w] * 4)
    metric_tbl.setStyle(TableStyle([
        ("GRID",        (0, 0), (-1, -1), 0.4, BORDER),
        ("BACKGROUND",  (0, 0), (-1, -1), colors.HexColor("#ffffff")),
        ("TOPPADDING",  (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("ALIGN",       (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(metric_tbl)
    story.append(SP(1, 12))

    print("  Rendering gravity chart...")
    chart_buf = build_gravity_chart(cases)
    story.append(Image(chart_buf, width=W - 2 * MARGIN - 2 * cm, height=5.5 * cm))
    story.append(SP(1, 6))

    # Case cards
    for c in cases:
        short = c["name"][:62] + "..." if len(c["name"]) > 62 else c["name"]
        row = Table([[
            Paragraph(f"<font color='#b8621a'>Case {c['case_id']}</font>", S["finding_label"]),
            Paragraph(short, ParagraphStyle("cc", fontName="Helvetica-Bold",
                                            fontSize=8.5, textColor=INK)),
            Paragraph(
                f"{c['n_signals']} signals  ·  avg {c['avg_g']}  ·  max {c['max_g']}",
                ParagraphStyle("cm", fontName="Courier", fontSize=7, textColor=INK_SOFT)),
        ]], colWidths=[1.8 * cm, 10.5 * cm, 4.5 * cm])
        row.setStyle(TableStyle([
            ("LEFTPADDING",  (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING",   (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 5),
            ("LINEBELOW",    (0, 0), (-1, -1), 0.4, BORDER),
            ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
            ("LINEBEFORE",   (0, 0), (0, -1),  3, ACCENT),
        ]))
        story.append(row)

    story.append(SP(1, 14))

    # ── Section III: The Threads ──────────────────────────────────────────
    story.append(Paragraph('<font color="#b8621a">III.</font>  THE THREADS',
                            S["section_head"]))
    story.append(HRule(thickness=0.3))

    # ── MAGAQA ────────────────────────────────────────────────────────────
    story.append(SP(1, 6))
    story.append(AccentBar("Operation Magaqa &nbsp;—&nbsp; Political Interference",
                            S["thread_title"], ACCENT))
    story.append(SP(1, 6))

    story.append(Paragraph(
        "Sindiso Magaqa was thirty-two years old when he was shot in Richards Bay "
        "in July 2017. He was the Secretary-General of the ANC Youth League — a position "
        "that placed him near the centre of some of the most contested political terrain "
        "in the country. He died from his injuries in August 2017. The investigation into "
        "his murder has been open for nine years.",
        S["body"]))

    story.append(Paragraph(
        "In May 2026, a Member of Parliament named Fadiel Adams was arrested in Cape Town. "
        "The charge: alleged interference in the Magaqa murder probe. What followed over "
        "five days is documented across five signals from three independent news sources — "
        "police searched Adams' home in Mitchells Plain; he obtained emergency court relief "
        "and was arrested within hours of that order; he was transported to KwaZulu-Natal "
        "under operational silence. A sitting MP does not seek emergency court relief against "
        "an arrest, and then lose it within hours, unless something specific is at risk of "
        "being disclosed.",
        S["body"]))

    story.append(SP(1, 4))
    story.append(KeepTogether([
        FindingBlock(
            "Finding M-I &nbsp;&nbsp; HIGH CONFIDENCE &nbsp;&nbsp; INDEPENDENTLY CONVERGED",
            "Fadiel Adams (NCC MP, Western Cape) arrested May 2026 for alleged interference "
            "in the Magaqa murder probe. Confirmed by Daily Maverick and News24 across five "
            "signals. Emergency court bid obtained and overturned same day. KZN transfer "
            "under operational security. Jurisdiction is KwaZulu-Natal — locus of the "
            "2017 murder.",
            S, status="normal"),
        SP(1, 6),
        FindingBlock(
            "Finding M-II &nbsp;&nbsp; PROVISIONAL &nbsp;&nbsp; SINGLE INFERENCE PATH",
            "SAFLII returned zero court judgments for Sindiso Magaqa. Nine years after "
            "the murder, no published prosecution judgment exists in the public record. "
            "This is either a case still approaching trial — consistent with Adams' arrest "
            "— or a case sustained in obstruction. The absence of a record is the shape "
            "of nine years of delay.",
            S, status="normal"),
        SP(1, 6),
        FindingBlock(
            "Finding M-III &nbsp;&nbsp; DEMOTED — SECOND PASS",
            "S v Thebus (338/2001) ZASCA 89 — retrieved on SAFLII search for 'Adams.' "
            "No body text was ingested. Adams is a common SA surname. Zero evidentiary "
            "weight without body text retrieval. Removed from case corpus pending "
            "full judgment fetch at saflii.org.",
            S, status="demoted"),
    ]))

    # ── ESKOM ─────────────────────────────────────────────────────────────
    story.append(SP(1, 12))
    story.append(AccentBar("Eskom Procurement Fraud &nbsp;—&nbsp; The R21 Billion Contract",
                            S["thread_title"], ACCENT))
    story.append(SP(1, 6))

    story.append(Paragraph(
        "In 2024, Minenhle Mavuso was twenty-four years old and running an online wig "
        "store called Milo Hair Beaute from Cape Town. In a parallel life, documented by "
        "amaBhungane in May 2026, she was the named operator of a company that had been "
        "awarded a R21-billion diesel supply contract with Eskom.",
        S["body"]))

    story.append(Paragraph(
        "The CIPC database — South Africa's public company registry — returns zero "
        "registration entries for Minenhle Mavuso. Zero for Milo Hair Beaute. An "
        "unregistered entity cannot legally hold a government procurement contract "
        "under the PFMA or PPPFA frameworks. Mavuso is not the story. She may be the "
        "least powerful person in this chain. The story is whoever approved her supplier "
        "registration. Those names are not in the corpus.",
        S["body"]))

    story.append(SP(1, 4))
    story.append(KeepTogether([
        FindingBlock(
            "Finding E-I &nbsp;&nbsp; MEDIUM CONFIDENCE &nbsp;&nbsp; SINGLE SOURCE CEILING",
            "Minenhle Mavuso (24, BA Psychology) named as front company operator for "
            "R21bn Eskom diesel supply contract (2024). CIPC returns zero registrations "
            "for Mavuso and Milo Hair Beaute. Structural fraud indicator confirmed. "
            "Source: amaBhungane (high credibility, single outlet). Authorising officials "
            "unidentified — they are the causal actors.",
            S, status="normal"),
        SP(1, 6),
        FindingBlock(
            "Analyst Note",
            "The highest-gravity signal in this case (g=0.46) was self-generated by the "
            "CIPC collector. A system cannot independently corroborate itself. That signal "
            "has been reclassified as derived analysis and removed from confidence "
            "calculations. The case currently rests on one investigative source.",
            S, status="note"),
    ]))

    # ── KZN HAWKS ────────────────────────────────────────────────────────
    story.append(SP(1, 12))
    story.append(AccentBar("KZN HAWKS Institutional Integrity",
                            S["thread_title"], ACCENT))
    story.append(SP(1, 6))

    story.append(Paragraph(
        "The Directorate for Priority Crime Investigation exists, in theory, to investigate "
        "priority crime. In KwaZulu-Natal, the corpus suggests the directorate has itself "
        "become a site of priority crime. What is documented here is not an isolated "
        "incident. It is a sequence.",
        S["body"]))

    story.append(Paragraph(
        "Someone broke into a HAWKS facility in KwaZulu-Natal repeatedly, before 541 "
        "kilograms of cocaine were stolen. The cocaine had been previously seized in a "
        "bust where officials failed to follow protocol. Before the theft, a paper trail "
        "was fabricated. Internal HAWKS sources told journalists it was an inside job. "
        "The KZN HAWKS boss was called before a commission and identified as someone "
        "who should have taken a lie detector test. The polygraph was never administered.",
        S["body"]))

    story.append(Paragraph(
        "Three independent publications — TimesLIVE, the Mail &amp; Guardian, and News24 "
        "— covered the same commission testimony on the same day in May 2026. That is what "
        "genuine multi-source convergence looks like. What the corpus does not answer is "
        "who is buying the cocaine that left the HAWKS office. Without that answer, "
        "this case describes institutional decay. With it, it describes a criminal "
        "enterprise with law enforcement as its logistics infrastructure.",
        S["body"]))

    story.append(SP(1, 4))
    story.append(KeepTogether([
        FindingBlock(
            "Finding K-I &nbsp;&nbsp; HIGH CONFIDENCE &nbsp;&nbsp; INDEPENDENTLY CONVERGED",
            "Three-outlet convergence (TimesLIVE, Mail & Guardian, News24) on same "
            "commission testimony (May 2026): paper trail fabricated before theft, "
            "repeated facility break-ins, HAWKS protocol failure on seizure. "
            "Four-stage operational sequence confirmed across independent sources. "
            "Beneficiary network: unknown.",
            S, status="normal"),
        SP(1, 6),
        FindingBlock(
            "Finding K-II &nbsp;&nbsp; PROVISIONAL CONVERGENCE",
            "2022 TimesLIVE report references cocaine theft from HAWKS office and "
            "names it a probable inside job — likely same event as 2026 commission "
            "testimony, given the 541kg cocaine figure. If confirmed, extends the "
            "documented timeline to four years of unresolved institutional compromise.",
            S, status="normal"),
        SP(1, 6),
        FindingBlock(
            "Finding K-III &nbsp;&nbsp; DEMOTED — SECOND PASS",
            "2020 HAWKS members arrested for theft (TimesLIVE). Six-year gap, no cocaine "
            "reference, no case number connection. First pass included this as a pattern "
            "signal. Second pass found no independent connection to the cocaine case. "
            "Removed from Case 10.",
            S, status="demoted"),
    ]))

    story.append(SP(1, 14))

    # ── Section IV: Methodology ───────────────────────────────────────────
    story.append(Paragraph('<font color="#b8621a">IV.</font>  THE METHODOLOGY OF DOUBT',
                            S["section_head"]))
    story.append(HRule(thickness=0.3))
    story.append(SP(1, 8))

    story.append(Paragraph(
        "This document applied a two-pass adversarial review to all analytical findings "
        "before publication. After an initial analytical pass that derives findings from "
        "the signal corpus, a second pass re-derives findings independently — working only "
        "from raw signal content, without reference to prior conclusions.",
        S["body"]))

    story.append(Paragraph(
        "Findings that appear in both passes at similar confidence levels are treated as "
        "converged. Findings that appear in only one pass, or at significantly different "
        "confidence levels, are demoted. The risk this process guards against is not "
        "that a finding is wrong — it is that a wrong finding survives because nothing "
        "was designed to challenge it. Independent convergence is not proof. It is the "
        "minimum condition for treating a finding as intelligence rather than hypothesis.",
        S["body"]))

    comp_data = [
        ["Finding", "Pass 1", "Pass 2", "Verdict"],
        ["Adams arrest / KZN transfer", "HIGH", "HIGH", "CONVERGES"],
        ["Magaqa 9yr no prosecution", "MEDIUM", "MEDIUM", "PROVISIONAL"],
        ["Thebus / Adams prior record", "MEDIUM", "UNRELIABLE", "FAILS — DEMOTED"],
        ["Mavuso no CIPC registration", "MEDIUM", "MEDIUM", "SINGLE SOURCE CEILING"],
        ["541kg cocaine 3-outlet convergence", "HIGH", "HIGH", "CONVERGES"],
        ["2020 HAWKS theft = same case", "MEDIUM", "NOT CONVERGED", "FAILS — DEMOTED"],
        ["CIPC finding as anchor signal", "HIGH g=0.46", "SELF-GENERATED", "RECLASSIFIED"],
    ]
    cw = (W - 2 * MARGIN) / 4
    comp_tbl = Table(comp_data, colWidths=[cw * 1.5, cw * 0.7, cw * 0.9, cw * 0.9])
    comp_tbl.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, 0), INK),
        ("TEXTCOLOR",   (0, 0), (-1, 0), WHITE),
        ("FONTNAME",    (0, 0), (-1, 0), "Courier"),
        ("FONTSIZE",    (0, 0), (-1, 0), 6.5),
        ("FONTNAME",    (0, 1), (-1, -1), "Times-Roman"),
        ("FONTSIZE",    (0, 1), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, colors.HexColor("#f8f6f0")]),
        ("GRID",        (0, 0), (-1, -1), 0.3, BORDER),
        ("TOPPADDING",  (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",(0,0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("ALIGN",       (1, 0), (-1, -1), "CENTER"),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("TEXTCOLOR",   (3, 1), (3, 1),  VERIFIED_C),
        ("TEXTCOLOR",   (3, 2), (3, 2),  VERIFIED_C),
        ("FONTNAME",    (3, 3), (3, 3),  "Helvetica-Bold"),
        ("FONTNAME",    (3, 6), (3, 6),  "Helvetica-Bold"),
        ("TEXTCOLOR",   (3, 3), (3, 3),  LOW_C),
        ("TEXTCOLOR",   (3, 6), (3, 6),  LOW_C),
        ("TEXTCOLOR",   (3, 7), (3, 7),  MED_C),
    ]))
    story.append(comp_tbl)

    story.append(SP(1, 14))

    # ── Section V: Open Requirements ─────────────────────────────────────
    story.append(Paragraph('<font color="#b8621a">V.</font>  WHAT THE GRAPH DOES NOT YET SHOW',
                            S["section_head"]))
    story.append(HRule(thickness=0.3))
    story.append(SP(1, 8))

    story.append(Paragraph(
        "The following questions cannot be answered from the current corpus. "
        "Answering any of them would materially change the analytical picture.",
        S["body"]))

    reqs = [
        ("Magaqa",
         "Who directed the 2017 assassination? What is Adams' specific role in the "
         "obstruction? Does S v Thebus name him as co-accused? Body text required from "
         "saflii.org/za/cases/ZASCA/2002/89.html."),
        ("Eskom",
         "Who authorised Mavuso's supplier registration? Who signed the contract "
         "award? What is the upstream procurement chain? Eskom CFO and procurement "
         "committee membership during 2024 diesel tender period."),
        ("KZN HAWKS",
         "Who is the KZN HAWKS boss named at the commission? Who is the beneficiary "
         "network for the diverted narcotics? Is the 2022 inside job the same event "
         "as the 2026 commission testimony?"),
        ("Collection Actions",
         "1. Fetch S v Thebus full judgment text.  "
         "2. Dork: site:news24.com 'KZN Hawks boss cocaine commission'.  "
         "3. Dork: site:eskom.co.za tender diesel 2024.  "
         "4. Run enricher_worker.bat on SAFLII signals for body text."),
    ]
    for label, text in reqs:
        story.append(SP(1, 4))
        story.append(FindingBlock(label, text, S, status="note"))

    story.append(SP(1, 16))

    # ── Footer note ───────────────────────────────────────────────────────
    story.append(HRule())
    story.append(SP(1, 6))
    story.append(Paragraph(
        "This record is sealed. No findings may be added or modified after "
        f"the seal timestamp: {generated}. "
        "Subsequent analysis continues in new operation records only. "
        "All findings are provisional unless marked as independently converged.",
        S["body_italic"]))

    print("  Building PDF...")
    doc.build(story)
    print(f"  Done: {out_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(OUT_PATH))
    args = parser.parse_args()

    print("Loading data from database...")
    data = load_data()
    print(f"  Actors in graph: {len(data['actors'])}")
    print(f"  Edges: {len(data['edges'])}")
    print(f"  Active cases: {len(data['cases'])}")

    build_pdf(data, Path(args.out))


if __name__ == "__main__":
    main()
