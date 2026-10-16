#!/usr/bin/env python3
"""
Seed the Limpopo rerun (v2): one case, hand-picked signal pins, one article.

Dry-run by default (prints what it would do). Pass --write to change the database.
Safe to re-run: it updates the same case and article instead of duplicating them.

Run from the repo root:
    python tools/seed_limpopo_v2.py            # dry run
    python tools/seed_limpopo_v2.py --write    # apply
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "database.db"
REPORT_MD = ROOT / "docs" / "reports" / "limpopo-procurement-arrests-v2.md"

CASE_ID = 17  # must also be listed in PUBLISHED_CASE_IDS in tools/publish.py
SLUG = "limpopo-procurement-arrests-v2"
TITLE = "What the Records Support: Limpopo's April 2026 Municipal Procurement Arrests"
SUMMARY = (
    "A narrower second look at the April 2026 Limpopo municipal procurement arrests: "
    "which claims the public record supports, and which connections it does not."
)
AUTHOR = "ZA-DIVERGENT Analyst"
TAGS = ["limpopo", "molemole", "elias motsoaledi", "municipal procurement", "hawks", "verification"]

CASE_NAME = "Limpopo Municipal Procurement Arrests, April 2026 (rerun v2)"
CASE_DESC = (
    "Rerun of the baseline Limpopo report with hand-picked signals, a control group and a decoy "
    "group. Asks what the public record supports, not what a story would like to connect."
)
CASE_HYPOTHESIS = (
    "Null hypothesis: the Molemole and Elias Motsoaledi arrests, the 2025 Polokwane document "
    "seizure and the Mkhwanazi arrest are separate matters. A link is accepted only if a Hawks, "
    "NPA or court record states one."
)

# (signal_id prefix, note). Chosen by hand from the October 2026 ingest.
PINS = [
    # subject: the Limpopo arrests
    ("1e15c644", "subject: Molemole"),
    ("001d12b4", "subject: Molemole"),
    ("c343b837", "subject: Molemole"),
    ("b4245051", "subject: Molemole"),
    ("6140a4bb", "subject: Molemole"),
    ("261a12c8", "subject: Molemole (headline only)"),
    ("b98e6b1c", "subject: Molemole, later arrests (headline only)"),
    ("4eff391c", "subject: Molemole, later arrests (headline only)"),
    ("7c779be4", "subject: Elias Motsoaledi"),
    ("00fb6d56", "subject: Elias Motsoaledi"),
    ("c17df827", "subject: Elias Motsoaledi"),
    # subject: the September 2025 Polokwane document seizure and its follow-up
    ("969710a9", "subject: Polokwane 2025"),
    ("4d73738b", "subject: Polokwane 2025"),
    ("7d4900e2", "subject: Polokwane 2025"),
    ("e3c51354", "subject: Polokwane 2025 (the municipality's framing)"),
    ("46de37fa", "subject: Polokwane 2025 (quotes the Hawks)"),
    ("2e09caff", "subject: Polokwane 2025, mayor's court challenge"),
    ("d041fa56", "subject: Polokwane, May 2026 follow-up (headline only)"),
    # control: the matter the baseline tied to Limpopo
    ("579726c3", "control: Mkhwanazi (the baseline's signal)"),
    ("d522e09a", "control: Mkhwanazi, EMPD"),
    ("c7970535", "control: Mkhwanazi, task team"),
    ("61c541b1", "control: Mkhwanazi, EMPD"),
    ("8bc54a90", "control: Mkhwanazi, Ekurhuleni case"),
    ("4a114a5b", "control: Mkhwanazi, August arrest (headline only)"),
    ("76734add", "control: Mkhwanazi, latest signal (headline only)"),
    ("f195e708", "control: surname-only headline (no first name)"),
    ("9301f88f", "control: namesake? headline-level inference"),
    # decoy: same province, link not examined
    ("4fdc0250", "decoy: same province, link not examined"),
    ("5a048ad4", "decoy: same province, link not examined"),
    ("dda6d67d", "decoy: same province, link not examined"),
]

def sha256_text(rows) -> str:
    h = hashlib.sha256()
    for r in rows:
        h.update(("|".join("" if v is None else str(v) for v in r) + "\n").encode("utf-8"))
    return h.hexdigest()


def git(*args: str) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=30)
        return out.stdout.strip()
    except Exception:
        return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="apply changes to the database")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"database not found: {DB_PATH}")
        return 1
    if not REPORT_MD.exists():
        print(f"report not found: {REPORT_MD}")
        return 1

    # Settle the WAL so reads see everything, then read through a read-only connection.
    settle = sqlite3.connect(DB_PATH, timeout=60)
    settle.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    settle.close()
    ro = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)

    def count(where: str = "1=1", *params) -> int:
        return ro.execute(f"SELECT COUNT(*) FROM signals WHERE {where}", params).fetchone()[0]

    # Provenance. The fingerprint covers the signal table only, so it does not change
    # when this script adds the case and article.
    fingerprint = sha256_text(
        ro.execute("SELECT signal_id, source, timestamp, title FROM signals ORDER BY signal_id")
    )[:12]
    commit = git("rev-parse", "--short", "HEAD") or "unknown"
    if git("status", "--porcelain", "--untracked-files=no", "--", "app.py", "core", "forage", "publisher", "tools"):
        commit += " (+uncommitted changes)"

    # Resolve pins by prefix; every prefix must match exactly one signal.
    all_pins = PINS
    resolved, problems = [], []
    for prefix, note in all_pins:
        rows = ro.execute(
            "SELECT signal_id, source, timestamp, title FROM signals WHERE signal_id LIKE ?",
            (prefix + "%",),
        ).fetchall()
        if len(rows) != 1:
            problems.append(f"  {prefix}: {len(rows)} matches")
        else:
            resolved.append((rows[0][0], note, rows[0]))
    if problems:
        print("Could not resolve these pins (need exactly one match each):")
        print("\n".join(problems))
        return 1

    polokwane_ids = [p for p, note in PINS if note.startswith("subject: Polokwane 2025")]
    polokwane = (
        f"FORGE holds {len(polokwane_ids)} headlines on it, from 17 to 21 September 2025, plus one "
        "from May 2026 reporting that the Hawks would return to the municipality for more documents "
        "(d041fa56). None of them reports an arrest."
    )

    values = {
        "RUN_DATE": datetime.now(timezone.utc).strftime("%d %B %Y").lstrip("0"),
        "N_SIGNALS": f"{count():,}",
        "N_SOURCES": ro.execute("SELECT COUNT(DISTINCT source) FROM signals").fetchone()[0],
        "N_PINNED": len(resolved),
        "N_SURNAME": count("title LIKE ?", "%Mkhwanazi%"),
        "N_EMPD": count(
            "title LIKE ? AND (title LIKE ? OR title LIKE ?)",
            "%Mkhwanazi%", "%EMPD%", "%Ekurhuleni%",
        ),
        "N_MKH_LIMPOPO": count("title LIKE ? AND title LIKE ?", "%Mkhwanazi%", "%Limpopo%"),
        "N_JULIUS": count("title LIKE ? AND title LIKE ?", "%Julius%", "%Mkhwanazi%"),
        "N_NHLANHLA": count("title LIKE ? AND title LIKE ?", "%Nhlanhla%", "%Mkhwanazi%"),
        "N_NEITHER": count(
            "title LIKE ? AND title NOT LIKE ? AND title NOT LIKE ?",
            "%Mkhwanazi%", "%Julius%", "%Nhlanhla%",
        ),
        "POLOKWANE_COVERAGE": polokwane,
        "COMMIT": commit,
        "CORPUS_SHA": fingerprint,
    }

    md = REPORT_MD.read_text(encoding="utf-8")
    for key, val in values.items():
        md = md.replace("{" + key + "}", str(val))
    leftover = sorted(set(re.findall(r"\{[A-Z_]+\}", md)))
    if leftover:
        print(f"Unfilled placeholders in the report: {leftover}")
        return 1

    print("== computed values ==")
    for key, val in values.items():
        if key != "POLOKWANE_COVERAGE":
            print(f"  {key:14} {val}")
    print(f"\n== {len(resolved)} pins ==")
    for sid, note, row in resolved:
        print(f"  {sid[:8]}  {note[:48]:48}  {str(row[2])[:10]}  {row[3][:60]}")

    if not args.write:
        print("\nDRY RUN: nothing written. Re-run with --write to apply.")
        return 0

    have = {r[0] for r in ro.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    missing = [t for t in ("cases", "case_signals", "articles") if t not in have]
    if missing:
        print(f"Tables missing from this database: {missing}")
        print("Run tools/sync_schema_from_backup.py --write first.")
        return 1

    now_iso = datetime.now(timezone.utc).isoformat()
    now_db = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    wc = sqlite3.connect(DB_PATH, timeout=60)
    with wc:
        if wc.execute("SELECT 1 FROM cases WHERE case_id=?", (CASE_ID,)).fetchone():
            wc.execute(
                "UPDATE cases SET name=?, description=?, hypothesis=? WHERE case_id=?",
                (CASE_NAME, CASE_DESC, CASE_HYPOTHESIS, CASE_ID),
            )
        else:
            wc.execute(
                "INSERT INTO cases (case_id, name, description, hypothesis, case_type, status, "
                "source_type, created_at, auto_generated) VALUES (?,?,?,?,?,?,?,?,0)",
                (CASE_ID, CASE_NAME, CASE_DESC, CASE_HYPOTHESIS, "general", "active", "live", now_db),
            )
        for sid, note, _row in resolved:
            wc.execute(
                "INSERT OR IGNORE INTO case_signals (case_id, signal_id, note) VALUES (?,?,?)",
                (CASE_ID, sid, note),
            )
            wc.execute(
                "UPDATE case_signals SET note=? WHERE case_id=? AND signal_id=?",
                (note, CASE_ID, sid),
            )
        keep = [sid for sid, _n, _r in resolved]
        marks = ",".join("?" * len(keep))
        wc.execute(
            f"DELETE FROM case_signals WHERE case_id=? AND signal_id NOT IN ({marks})",
            (CASE_ID, *keep),
        )
        existing = wc.execute("SELECT article_id FROM articles WHERE slug=?", (SLUG,)).fetchone()
        if existing:
            wc.execute(
                "UPDATE articles SET title=?, summary=?, body_markdown=?, stream=?, author=?, "
                "status='published', tags=?, tier='free', updated_at=? WHERE article_id=?",
                (TITLE, SUMMARY, md, "CRIME_INTEL", AUTHOR, json.dumps(TAGS), now_db, existing[0]),
            )
        else:
            wc.execute(
                "INSERT INTO articles (title, slug, summary, body_markdown, stream, author, status, "
                "published_at, created_at, updated_at, tags, tier) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (TITLE, SLUG, SUMMARY, md, "CRIME_INTEL", AUTHOR, "published",
                 now_iso, now_db, now_db, json.dumps(TAGS), "free"),
            )
    wc.close()
    print(f"\nWROTE case {CASE_ID} with {len(resolved)} pins and article '{SLUG}'.")
    print("Next: add 17 to PUBLISHED_CASE_IDS in tools/publish.py, then run tools/publish_additive.ps1.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
