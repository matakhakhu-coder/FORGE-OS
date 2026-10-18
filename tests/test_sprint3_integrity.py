"""Sprint 3: the Sprint 2 offline-run fixes allocated to this sprint.

- the cluster, decay and name-finder engines leave hand-checked pins alone,
  and the checked import clears what they could leave outside the fingerprint
  (cluster_id, confidence_score, relevance_score, signal_entities rows);
- the workbench stops duplicating ingested records as new signals and stops
  registering each add-on hook twice;
- every entry point honours FORGE_DB, and the three collectors stop creating
  stray SQLite files named after themselves;
- pins that share a publish time keep one order between rebuilds.

All data here is synthetic or a fresh temp database: nothing touches
database.db or docs/signals.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.test_checked_signals import _fresh_db, _markers, _publish_to  # noqa: E402

EPPING = "dbf90d51-2633-4a8f-86f1-ab1bf2b48d6c"
LONG = ("Police in Cape Town are investigating a shooting in which two men were "
        "killed on Friday evening. ") * 4


def _imported(tmp_path, monkeypatch):
    from tools.checked_signals import load_index, import_checked

    conn, db_file = _fresh_db(tmp_path, monkeypatch)
    import_checked(conn, load_index(), write=True)
    conn.commit()
    return conn, db_file


def _add_plain(conn, n=2, lat=-33.93, lng=18.53, ts="2026-10-16T06:00:00", title="plain"):
    ids = []
    for i in range(n):
        sid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO signals (signal_id, source, external_id, title, content, lat, lng, "
            "timestamp, status, stream, relevance_score) VALUES (?,?,?,?,?,?,?,?,?,?,1.0)",
            (sid, "test", f"test:{sid}", f"{title} {i}", LONG, lat, lng, ts, "raw", "CRIME_INTEL"))
        ids.append(sid)
    conn.commit()
    return ids


# ── Engines leave checked pins alone ─────────────────────────────────────────────────────

def test_cluster_engine_skips_checked_pins(tmp_path, monkeypatch):
    from forage.engines.cluster_engine import ClusterEngine

    conn, db_file = _imported(tmp_path, monkeypatch)
    plain = _add_plain(conn)              # right on top of the Epping pin
    conn.close()
    ClusterEngine(db_file).run()
    conn = sqlite3.connect(str(db_file))
    assert conn.execute("SELECT COUNT(*) FROM signals WHERE source_type='checked' "
                        "AND cluster_id IS NOT NULL").fetchone()[0] == 0
    # the engine still works on everything else
    got = conn.execute(f"SELECT cluster_id FROM signals WHERE signal_id IN (?,?)", plain).fetchall()
    assert all(r[0] for r in got)


def test_decay_engine_skips_checked_pins(tmp_path, monkeypatch):
    from forage.engines.decay_engine import DecayEngine

    conn, db_file = _imported(tmp_path, monkeypatch)
    _add_plain(conn, ts="2026-01-01T00:00:00+00:00")
    conn.close()
    DecayEngine(db_file).run()
    conn = sqlite3.connect(str(db_file))
    assert conn.execute("SELECT COUNT(*) FROM signals WHERE source_type='checked' "
                        "AND relevance_score != 1.0").fetchone()[0] == 0


def test_name_finder_skips_checked_pins(tmp_path, monkeypatch):
    pytest.importorskip("spacy")
    from forage.processors import ner_processor

    conn, db_file = _imported(tmp_path, monkeypatch)
    _add_plain(conn, n=1, title="Cape Town shooting")
    conn.close()
    try:
        ner_processor.run(db_path=db_file)
    except SystemExit:
        pytest.skip("spaCy model not installed")
    conn = sqlite3.connect(str(db_file))
    assert conn.execute("SELECT COUNT(*) FROM signal_entities se JOIN signals s "
                        "ON s.signal_id = se.signal_id WHERE s.source_type='checked'"
                        ).fetchone()[0] == 0


def test_import_clears_machine_traces_and_verify_catches_them(tmp_path, monkeypatch):
    from tools.checked_signals import import_checked, load_index, verify

    conn, _ = _imported(tmp_path, monkeypatch)
    cs = load_index()
    conn.execute("UPDATE signals SET cluster_id='CL-x', confidence_score=0.4, "
                 "relevance_score=0.2 WHERE signal_id=?", (EPPING,))
    conn.execute("INSERT INTO signal_entities (signal_id, text, label) "
                 "VALUES (?, 'Cape Town', 'GPE')", (EPPING,))
    conn.commit()
    problems = " | ".join(verify(conn, cs))
    for word in ("cluster_id", "confidence_score", "relevance_score", "signal_entities"):
        assert word in problems
    acts = {a.signal_id: a for a in import_checked(conn, cs, write=True)}
    assert acts[EPPING].kind == "update"
    assert {"cluster_id", "confidence_score", "relevance_score", "signal_entities"} <= set(acts[EPPING].changed)
    assert verify(conn, cs) == []
    assert tuple(conn.execute("SELECT cluster_id, confidence_score, relevance_score FROM signals "
                              "WHERE signal_id=?", (EPPING,)).fetchone()) == (None, None, 1.0)
    assert conn.execute("SELECT COUNT(*) FROM signal_entities").fetchone()[0] == 0
    # and a second import leaves it alone
    assert {a.kind for a in import_checked(conn, cs, write=True)} == {"unchanged"}


def test_build_refused_while_a_cluster_id_sits_on_a_checked_pin(tmp_path, monkeypatch):
    conn, db_file = _imported(tmp_path, monkeypatch)
    conn.execute("UPDATE signals SET cluster_id='CL-x' WHERE signal_id=?", (EPPING,))
    conn.commit()
    conn.close()
    with pytest.raises(SystemExit):
        _publish_to(tmp_path, monkeypatch, db_file)
    assert not (tmp_path / "dist" / "index.html").exists()


# ── Workbench noise ───────────────────────────────────────────────────────────────────

def _artifact_run(db_file):
    from forage.processors.artifact_processor import ProcessorManager
    pm = ProcessorManager(db_path=db_file)
    return pm


@pytest.mark.parametrize("with_metadata_column", [True, False])
def test_artifact_processor_does_not_duplicate_ingested_signals(tmp_path, monkeypatch,
                                                                 with_metadata_column):
    from forage.processors.artifact_processor import ProcessorManager

    conn, db_file = _fresh_db(tmp_path, monkeypatch)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(artifacts)")]
    assert "metadata_json" in cols                      # migrate_db adds it now
    if not with_metadata_column:
        # A database migrated before Sprint 3: rebuild artifacts without it.
        conn.execute("ALTER TABLE artifacts DROP COLUMN metadata_json")
        conn.commit()
    plain = _add_plain(conn, n=3)
    rows = [dict(r) for r in conn.execute("SELECT * FROM signals")]
    conn.close()

    pm = ProcessorManager(db_path=db_file)
    made = [pm.signal_to_artifact(r) for r in rows]
    assert all(a > 0 for a in made)
    # Ingesting the same records again copies nothing new.
    assert [pm.signal_to_artifact(r) for r in rows] == made
    pm.run_batch(limit=50, base_dir=tmp_path)
    pm.run_batch(limit=50, base_dir=tmp_path)

    conn = sqlite3.connect(str(db_file))
    assert conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM signals WHERE external_id LIKE 'artifact:%'"
                        ).fetchone()[0] == 0


def test_artifact_processor_still_creates_signals_for_real_uploads(tmp_path, monkeypatch):
    from forage.processors.artifact_processor import ProcessorManager

    conn, db_file = _fresh_db(tmp_path, monkeypatch)
    conn.execute("INSERT INTO artifacts (title, description, type, source, raw_text_cache, "
                 "processing_status) VALUES ('Upload', 'x', 'document', 'citizen', ?, 'pending')",
                 (LONG,))
    conn.commit()
    conn.close()
    pm = ProcessorManager(db_path=db_file)
    row = sqlite3.connect(str(db_file))
    row.row_factory = sqlite3.Row
    r = row.execute("SELECT * FROM artifacts").fetchone()
    pm._conn()
    sid = pm._get_or_create_signal(r["artifact_id"], r)
    assert sid and pm._get_or_create_signal(r["artifact_id"], r) == sid


def test_hooks_registered_once_after_bootstrap_and_attach():
    """Same check as Sprint 2's check_hooks.py: app startup, count hooks."""
    code = (
        "import app\n"
        "from core.conclave.context import get_context\n"
        "app.init_db(); app.migrate_db()\n"
        "app.create_app()\n"
        "ctx = get_context()\n"
        "import collections, json\n"
        "out = {}\n"
        "for name, fns in ctx._hooks.items():\n"
        "    keys = [(getattr(f,'__module__',''), getattr(f,'__qualname__','')) for f in fns]\n"
        "    out[name] = max(collections.Counter(keys).values()) if keys else 0\n"
        "print('HOOKS' + json.dumps(out))\n"
    )
    env = dict(os.environ, FORGE_DB=str(Path("/tmp") / f"forge-hooks-{uuid.uuid4().hex}.db"))
    p = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=180)
    line = next((l for l in p.stdout.splitlines() if l.startswith("HOOKS")), None)
    assert line, p.stderr[-2000:]
    counts = json.loads(line[5:])
    assert counts and max(counts.values()) == 1, counts


def _module_level_hook(*a, **k):
    return 0


def test_register_hook_keeps_distinct_closures():
    from core.conclave.context import ConclaveContext

    ctx = ConclaveContext()

    def make(n):
        def h(*a, **k):
            return n
        return h

    def named(*a, **k):
        return 0

    ctx.register_hook("on_signal_ingested", make(1))
    ctx.register_hook("on_signal_ingested", make(2))
    ctx.register_hook("on_signal_ingested", named)
    ctx.register_hook("on_signal_ingested", named)          # same object
    ctx.register_hook("on_signal_ingested", _module_level_hook)
    ctx.register_hook("on_signal_ingested", _module_level_hook)
    assert len(ctx._hooks["on_signal_ingested"]) == 4


# ── FORGE_DB everywhere ───────────────────────────────────────────────────────────────

FORGE_DB_MODULES = [
    ("app", "DB_PATH"),
    ("tools.publish", "DB_PATH"),
    ("tools.checked_signals", "DB_PATH"),
    ("core.db.connection", "DB_PATH"),
    ("core.web.helpers", "DB_PATH"),
    ("core.pipeline.ingest", "_FORGE_DB_PATH"),
    ("forge_modules.coalition_detector.engine", "DB_PATH"),
    ("forge_modules.counterintel.engine", "DB_PATH"),
    ("forge_modules.emergence_engine.engine", "DB_PATH"),
    ("forage.engines.evolution_engine", "DB_PATH"),
    ("forage.processors.relationship_extractor", "DB_PATH"),
    ("forage.processors.content_enricher", "DB_PATH"),
    ("forage.processors.triple_extractor", "DB_PATH"),
]


@pytest.mark.parametrize("set_env", [True, False])
def test_every_entry_point_honours_forge_db(tmp_path, set_env):
    target = tmp_path / "isolated.db"
    code = (
        "import importlib, json\n"
        f"mods = {FORGE_DB_MODULES!r}\n"
        "out = {}\n"
        "for m, attr in mods:\n"
        "    out[m] = str(getattr(importlib.import_module(m), attr))\n"
        "print('PATHS' + json.dumps(out))\n"
    )
    env = dict(os.environ)
    env.pop("FORGE_DB", None)
    if set_env:
        env["FORGE_DB"] = str(target)
    p = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=180)
    line = next((l for l in p.stdout.splitlines() if l.startswith("PATHS")), None)
    assert line, p.stderr[-2000:]
    paths = json.loads(line[5:])
    want = str(target.resolve()) if set_env else str(ROOT / "database.db")
    assert {m: v for m, v in paths.items() if v != want} == {}
    assert not target.exists() or set_env      # importing alone creates nothing elsewhere


def test_flux_module_honours_forge_db(tmp_path):
    src = (ROOT / "forge_modules" / "flux" / "module.py").read_text()
    assert 'os.environ["FORGE_DB"]' in src


def test_misused_log_run_creates_no_stray_file(tmp_path, monkeypatch):
    from forage.utils.pipeline_logger import log_run

    monkeypatch.chdir(tmp_path)
    log_run("gnews", "success", 3, 2, 0.5)          # the old call shape
    assert list(tmp_path.iterdir()) == []
    db = tmp_path / "runs.db"
    log_run(db, "gnews", "success", 3, 2, 0.5)
    assert sqlite3.connect(str(db)).execute("SELECT COUNT(*) FROM pipeline_runs").fetchone()[0] == 1


def test_collectors_pass_the_database_to_log_run():
    for name in ("courts_roll_collector", "google_news_collector", "pmg_collector"):
        src = (ROOT / "forage" / "collectors" / f"{name}.py").read_text()
        assert "log_run(SOURCE_ID" not in src and "log_run(DB_PATH, SOURCE_ID" in src


# ── Stable order ──────────────────────────────────────────────────────────────────────

def test_pins_with_one_publish_time_keep_one_order(tmp_path, monkeypatch):
    from tools.checked_signals import load_index, import_checked

    orders = []
    for run, reverse in (("a", False), ("b", True)):
        d = tmp_path / run
        d.mkdir()
        conn, db_file = _fresh_db(d, monkeypatch)
        cs = load_index()
        if reverse:                                      # insert in the other order
            cs.batches.reverse()
            for b in cs.batches:
                b.signals.reverse()
        import_checked(conn, cs, write=True)
        conn.close()
        dist = _publish_to(d, monkeypatch, db_file)
        orders.append([m["title"] for m in _markers(dist)])
    assert orders[0] == orders[1]
