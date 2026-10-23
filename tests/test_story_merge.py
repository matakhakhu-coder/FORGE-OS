"""Batch 5 (23 Oct 2026), step 2: story_merge on its own.

Covers the rule that a killing is never demoted to a supporting record, the
two known over-merges (Carrim's Madlanga Commission arrest merged with his
Absa vehicle case; Padayachee's killing merged into Feroz Khan's commission
testimony), and the plan's acceptance cases that story_merge alone can show:
the R524 crash collapses to one main record with supporting ones, and two
different events in the same town a week apart stay separate.

Headlines are the ones the town sweeps saved. Not wired into collectors yet,
so nothing here touches a database other than an in-memory one.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forage.collectors import story_merge as sm  # noqa: E402

WC = json.loads((ROOT / "data" / "bench" / "story_word_counts.json").read_text())
BASE = (WC["rows"], WC["df"])


def rec(rid, title, day, stream="CRIME_INTEL", **kw):
    return sm.StoryRecord(rid=rid, title=title, date=sm._date(f"2026-10-{day:02d}T07:00:00"),
                          stream=stream, **kw)


def build(records):
    idx = sm.StoryIndex(base=BASE)
    for r in records:
        idx.add(r)
    return idx


def story_of(idx, rid):
    return next(sid for sid, mem in idx.stories.items() if any(m.rid == rid for m in mem))


# ── Feroz Khan / Padayachee ──────────────────────────────────────────────────────

KHAN_TESTIMONY = [
    rec("k1", "Feroz Khan to testify in camera at Madlanga Commission", 2, "PRIORITY_INTEL"),
    rec("k2", "Former Crime Intelligence Deputy Head Feroz Khan Set to Testify at Madlanga Commission", 6,
        "PRIORITY_INTEL"),
    rec("k3", "Feroz Khan vs Madlanga Commission: the one-word fight over his in camera testimony", 3,
        "PRIORITY_INTEL"),
]
PADAYACHEE = [
    rec("p1", "Former Crime Intelligence boss Feroz Khan's uncle, Preggy Padayachee, shot dead", 6, "GLOBAL"),
    rec("p2", "Who was Preggy Padayachee, murdered uncle-in-law of former Crime Intelligence deputy "
              "Feroz Khan?", 7, "GLOBAL"),
]


def test_padayachee_killing_is_a_killing_and_testimony_is_not():
    assert all(sm.is_killing(r) for r in PADAYACHEE)
    assert not any(sm.is_killing(r) for r in KHAN_TESTIMONY)


def test_crime_kind_from_collector_wins_over_headline():
    r = rec("x", "Musina businessman shot more than 20 times in store", 16, crime_kind="killing")
    assert sm.is_killing(r)
    r2 = rec("y", "Five killed in head-on collision on R524 near Makhado", 6, crime_kind="road_crash")
    assert not sm.is_killing(r2)


def test_killing_and_testimony_are_never_the_same_story():
    w = sm.Weights([], BASE)
    for k in KHAN_TESTIMONY:
        s, why = sm.same_story(PADAYACHEE[0], k, w)
        assert s == 0.0, (k.title, why)


def test_killing_is_never_demoted_by_choose_main():
    # Stronger stream, earlier date: still not main over a killing.
    main = sm.choose_main([KHAN_TESTIMONY[0], PADAYACHEE[0]])
    assert main is PADAYACHEE[0]


def test_feroz_khan_over_merge_fixed_in_index():
    idx = build(sorted(KHAN_TESTIMONY + PADAYACHEE, key=lambda r: r.date))
    sid = story_of(idx, "p1")
    assert not any(m.rid.startswith("k") for m in idx.stories[sid])
    roles = idx.roles()
    for p in PADAYACHEE:
        if roles[p.rid]["story_role"] != "main":
            # It may support another report of the same killing, never a non-killing.
            assert sm.is_killing(idx.main_of(story_of(idx, p.rid)))


def test_death_report_does_not_fold_killing_into_testimony():
    # Matches the killing (death words) and the testimony (name, commission):
    # it must not join the two stories together.
    bridge = rec("b1", "Feroz Khan's Madlanga testimony postponed after relative shot dead", 6,
                 "PRIORITY_INTEL")
    idx = build(sorted(KHAN_TESTIMONY + PADAYACHEE, key=lambda r: r.date) + [bridge])
    for mem in idx.stories.values():
        kinds = {sm.is_killing(m) for m in mem}
        no_violence = [m for m in mem if not sm.has_violence(m)]
        assert not (True in kinds and no_violence), [m.title for m in mem]


def test_link_story_keeps_killing_main_in_database():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE signals (signal_id TEXT, title TEXT, timestamp TEXT, stream TEXT, "
                 "metadata_json TEXT, source_type TEXT)")
    for k in KHAN_TESTIMONY:
        meta = sm.link_story(conn, k.rid, k.title, k.stream, f"{k.date}T07:00:00", {})
        conn.execute("INSERT INTO signals VALUES (?,?,?,?,?, 'live')",
                     (k.rid, k.title, f"{k.date}T07:00:00", k.stream, json.dumps(meta)))
    p = PADAYACHEE[0]
    meta = sm.link_story(conn, p.rid, p.title, p.stream, f"{p.date}T07:00:00", {})
    assert meta["story_role"] == "main"
    assert "supports_signal_id" not in meta
    # Nothing in the testimony story was re-pointed at the killing either.
    for (mj,) in conn.execute("SELECT metadata_json FROM signals"):
        assert json.loads(mj).get("supports_signal_id") != p.rid


# ── Carrim: Madlanga arrest vs Absa vehicle case ──────────────────────────────────

CARRIM_MADLANGA = [
    rec("m1", "Suliman Carrim arrested at OR Tambo for ignoring Madlanga Commission subpoena", 16),
    rec("m2", "Former ANC regional treasurer linked to Madlanga Commission arrested at OR Tambo", 16),
    rec("m3", "Suliman Carrim expected back in court after spending weekend behind bars", 18),
    rec("m4", "Suliman Carrim's bail bid postponed", 19),
    rec("m5", "Suliman Carrim stays behind bars after dramatic airport arrest", 19),
    rec("m6", "Prosecutors accuse Suliman Carrim of raising ill health willy-nilly", 20),
]
CARRIM_ABSA = [
    rec("a1", "Suliman Carrim faces additional charges after Malawi-bound cars intercepted at Beitbridge", 21),
    rec("a2", "Suliman Carrim Now Faces Car Theft Charges", 22),
    rec("a3", "Suliman Carrim in court for attempted car theft", 22),
    rec("a4", "Suliman Carrim's vehicle-theft case postponed", 22),
    rec("a5", "Court adds Carrim to Absa vehicle theft case", 22),
]


def test_name_only_on_different_days_is_not_a_match():
    w = sm.Weights([], BASE)
    s, why = sm.same_story(CARRIM_MADLANGA[3], CARRIM_ABSA[3], w)   # bail bid vs vehicle-theft case
    assert s == 0.0, why
    s, why = sm.same_story(CARRIM_MADLANGA[4], CARRIM_ABSA[3], w)   # behind bars vs vehicle-theft case
    assert s == 0.0, why


def test_name_only_match_never_bridges_stories():
    m = sm.Match(0.9, "headline (name only): carrim, suliman", "s", "m1")
    assert m.short and not m.bridges


def test_carrim_madlanga_and_absa_cases_stay_apart():
    allr = sorted(CARRIM_MADLANGA + CARRIM_ABSA, key=lambda r: (r.date, r.rid))
    idx = build(allr)
    for mem in idx.stories.values():
        ids = {m.rid[0] for m in mem}
        assert ids != {"m", "a"} and len(ids) == 1, [m.title for m in mem]


def test_carrim_absa_case_core_reports_collapse():
    idx = build(sorted(CARRIM_ABSA, key=lambda r: (r.date, r.rid)))
    sid = story_of(idx, "a2")
    assert {"a2", "a3"} <= {m.rid for m in idx.stories[sid]}


# ── plan acceptance cases story_merge alone can show ──────────────────────────────

R524 = [
    rec("r1", "R524 road in Limpopo closed following multi-vehicle collision - SABC News", 6),
    rec("r2", "Police say 6 now dead after Saps vehicle crash on R524 - The Citizen", 6),
    rec("r3", "6 lives lost: SAPS vehicle collides head-on with bakkie on R524 in Limpopo - Briefly News", 6),
    rec("r4", "Three SAPS Members, Two Awaiting-Trial Prisoners And Civilian Killed In R524 Levubu Road "
              "Crash | SA Trucker", 6),
    rec("r5", "Breaking: R524 Between Louis Trichardt and Thohoyandou Closed After Crash - Central News", 6),
    rec("r6", "Five killed in head-on collision on R524 near Makhado", 6),
    rec("r7", "Six killed in a head-on collision on the R524 Levubu Road in Limpopo's Vhembe District", 7),
    rec("r8", "Six killed in R524 horror crash identified - Limpopo Mirror", 8, "GLOBAL"),
]


def test_r524_crash_is_not_a_killing():
    assert not any(sm.is_killing(r) for r in R524)


def test_r524_crash_collapses_to_one_main_with_supporting():
    idx = build(R524)
    assert len(idx.stories) == 1, [[m.title for m in mem] for mem in idx.stories.values()]
    roles = idx.roles()
    mains = [rid for rid, role in roles.items() if role["story_role"] == "main"]
    assert len(mains) == 1
    assert sum(1 for role in roles.values() if role["story_role"] == "supporting_evidence") == len(R524) - 1


def test_two_events_same_town_a_week_apart_stay_separate():
    a = rec("e1", "Musina businessman shot dead in his store", 9, place="Musina", lat=-22.35, lng=30.04)
    b = rec("e2", "Man shot dead outside tavern in Musina", 16, place="Musina", lat=-22.35, lng=30.04)
    w = sm.Weights([], BASE)
    s, why = sm.same_story(a, b, w)
    assert s == 0.0, why
    idx = build([a, b])
    assert len(idx.stories) == 2


def test_two_killings_same_town_same_day_different_words_stay_separate():
    a = rec("f1", "Store owner shot more than 20 times in Matswale", 16, place="Musina", lat=-22.35, lng=30.04)
    b = rec("f2", "Body of woman found in veld near Musina border fence", 16, place="Musina", lat=-22.35,
            lng=30.04)
    w = sm.Weights([], BASE)
    assert sm.same_story(a, b, w)[0] == 0.0


# ── step 3: wiring ──────────────────────────────────────────────────────────

def test_safe_link_story_never_blocks_a_save():
    class Broken:
        def execute(self, *a, **k):
            raise RuntimeError("db gone")
    # link_story swallows the SELECT error; a failing UPDATE path must not raise either
    out = sm.safe_link_story(Broken(), "x1", "Suliman Carrim back in court", "GLOBAL",
                             "2026-10-19T07:00:00", {"url": "u"})
    assert out["url"] == "u"


def test_safe_link_story_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("FORGE_STORY_MERGE", "0")
    meta = {"url": "u"}
    assert sm.safe_link_story(None, "x1", "t", "GLOBAL", "2026-10-19", meta) is meta


def test_collectors_call_the_story_check():
    for name in ("civic_intel_collector.py", "google_news_collector.py"):
        src = (ROOT / "forage" / "collectors" / name).read_text(encoding="utf-8")
        assert "_story.safe_link_story(" in src, name


def test_bench_without_story_check_matches_batch4_scorecard():
    from tools import collector_bench as cb
    ref = json.loads((ROOT / "docs" / "bench" / "collector_bench_batch4_cs_2026-10-23.json").read_text())
    card = cb.score()
    assert card["summary"] == ref["summary"]


def test_bench_story_check_makes_no_case_worse():
    from tools import collector_bench as cb
    ref = {c["id"]: c for c in json.loads(
        (ROOT / "docs" / "bench" / "collector_bench_batch4_cs_2026-10-23.json").read_text())["cases"]}
    card = cb.score(merge=cb.story_merge_rows)
    for c in card["cases"]:
        before = ref[c["id"]]
        assert c["pass"] >= before["pass"], c["id"]
        assert c["caught"] == before["caught"], c["id"]
        if c["kind"] == "catch":
            rank = {"none": 0, "n/a": 0, "some": 1, "all": 2}
            assert rank[c["right_stream"]] >= rank[before["right_stream"]], c["id"]
            assert rank[c["right_place"]] >= rank[before["right_place"]], c["id"]
    cig = next(c for c in card["cases"] if c["id"] == "musina_cigarette_seizure")
    assert cig["records"] == 1 and cig["pass"]
