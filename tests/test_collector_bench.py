"""Tests for the Batch 1 collector regression bench (tools/collector_bench.py).

These check the bench itself: the reference cases, the saved fixture, the
offline replay and the scorer. They do not pin today's collector behaviour,
so a later batch that improves a collector does not break them; the scores
for today's code live in docs/bench/collector_bench_baseline_2026-10-23.json.
"""
import json
import re
import socket

import pytest

from tools import collector_bench as cb

STREAMS = {"CRIME_INTEL", "PRIORITY", "INFRASTRUCTURE", "GLOBAL"}
RULES = {"absent", "not_placed_in_province", "not_placed_at", "not_stream",
         "court_step_flagged", "date_is_incident"}

# Every case the Batch 1 plan names must be on the bench.
REQUIRED = {
    "musina_matswale_murder", "musina_neltox_sandf", "musina_carrim", "musina_cigarette_seizure",
    "musina_low_water_notice", "lt_phiphidi_killing", "lt_univen_shooting", "lt_madombidzha_death",
    "lt_mashawana_bodies", "lt_botlokwa_explosives", "plk_shandesh_deaths", "plk_golf_club_body",
    "plk_cricket_club_death", "plk_airport_officers", "plk_tvet_official", "plk_seshego_school_illness",
    "nc_newcastle_west_water", "nc_signal_hill", "nc_ncandu_park", "nc_power_cut", "nc_p1_counterfeit",
    "nc_p2_quad_bike", "nc_au_sydney_syndicate", "nc_au_mater", "nc_au_stockton", "musina_protest_noise",
    "lt_nrcs_feed_date", "lt_r524_crash", "plk_sekhukhune_officers", "nc_oosthuizen_court",
    "nc_mlambo_court", "plk_88_firearms",
}


@pytest.fixture(scope="module")
def cases():
    return cb.load_cases()


@pytest.fixture(scope="module")
def recorded_card():
    return cb.score(recorded=True)


@pytest.fixture(scope="module")
def replay_card():
    return cb.score(recorded=False)


def _case(card, cid):
    return next(r for r in card["cases"] if r["id"] == cid)


# ── reference cases ──

def test_reference_cases_are_well_formed(cases):
    ids = [c["id"] for c in cases["cases"]]
    assert len(ids) == len(set(ids))
    assert REQUIRED <= set(ids)
    for c in cases["cases"]:
        assert c["sweep"] in cb.TOWNS, c["id"]
        assert c["kind"] in ("catch", "keep_out"), c["id"]
        for key in ("match", "match_also", "query_match"):
            if c.get(key):
                re.compile(c[key])
        if c["kind"] == "catch":
            assert c["expected_stream"] in STREAMS, c["id"]
            assert c["expected_place"], c["id"]
            assert "merge_group" in c and "incident_date" in c, c["id"]
        else:
            assert c["keep_out_rule"] in RULES, c["id"]


# ── fixture ──

def test_fixture_covers_every_sweep_run():
    meta, rows = cb.load_replay()
    assert len(meta["run_totals"]) == len(cb.TOWNS) * len(cb.RUN_LABELS)
    assert rows and meta["rows"] == len(rows)
    for r in rows:
        assert r["town"] in cb.TOWNS and r["run"] in cb.RUN_LABELS
        assert r["stream"] in STREAMS


# ── offline replay ──

def test_no_network_blocks_sockets():
    with cb.no_network():
        with pytest.raises(cb.NetworkBlocked):
            socket.create_connection(("example.com", 80), timeout=1)
    # restored afterwards
    assert socket.create_connection is not None and socket.socket.connect.__name__ != "_blocked"


def test_replay_runs_offline_without_errors(replay_card):
    f = replay_card["fidelity"]
    assert f["rows"] > 0
    assert f["replay_errors"] == 0


def test_bench_writes_no_files(tmp_path):
    before = {p: p.stat().st_mtime_ns for p in (cb.CASES_PATH, cb.REPLAY_PATH)}
    cb.score(recorded=False)
    assert before == {p: p.stat().st_mtime_ns for p in before}


# ── the recorded scorecard reproduces what the sweep notes found ──

def test_recorded_misses_match_the_notes(recorded_card):
    for cid in ("musina_neltox_sandf", "lt_mashawana_bodies", "lt_botlokwa_explosives",
                "nc_signal_hill", "nc_ncandu_park"):
        assert _case(recorded_card, cid)["caught"] is False, cid


def test_recorded_known_faults_match_the_notes(recorded_card):
    matswale = _case(recorded_card, "musina_matswale_murder")
    assert matswale["caught"] and matswale["right_place"] == "none"
    carrim = _case(recorded_card, "musina_carrim")
    assert carrim["records"] > 1 and len(carrim["streams"]) == 3
    for cid in ("plk_shandesh_deaths", "plk_golf_club_body", "plk_cricket_club_death", "lt_univen_shooting"):
        assert set(_case(recorded_card, cid)["streams"]) == {"GLOBAL"}, cid
    assert len(_case(recorded_card, "plk_airport_officers")["streams"]) == 3
    assert set(_case(recorded_card, "nc_p1_counterfeit")["streams"]) == {"CRIME_INTEL"}
    assert set(_case(recorded_card, "nc_p2_quad_bike")["streams"]) == {"CRIME_INTEL"}
    for cid in ("nc_au_sydney_syndicate", "nc_au_mater", "nc_au_stockton", "plk_sekhukhune_officers",
                "nc_oosthuizen_court", "nc_mlambo_court", "musina_protest_noise", "lt_r524_crash"):
        assert _case(recorded_card, cid)["status"] == "fail", cid
    assert _case(recorded_card, "musina_low_water_notice")["pass"] is True
    assert _case(recorded_card, "lt_nrcs_feed_date")["status"] == "not_tested"


def test_committed_baseline_matches_the_recorded_scores(recorded_card):
    base = json.loads(cb.BASELINE_PATH.read_text(encoding="utf-8"))
    assert base["mode"] == "replay"
    assert base["fidelity"]["same_stream"] == base["fidelity"]["rows"]
    assert base["fidelity"]["same_place"] == base["fidelity"]["rows"]
    # Fields added to the scorecard later (Batch 3: precincts and arrest/court
    # flags, reported but not scored) are absent from the Batch 1 file, so
    # compare the fields the committed baseline has.
    assert base["summary"] == {k: recorded_card["summary"][k] for k in base["summary"]}
    for b, r in zip(base["cases"], recorded_card["cases"]):
        assert b == {k: r[k] for k in b}, b["id"]


# ── scorer rules on made-up rows ──

def _row(town="musina", run="kw", title="x", stream="CRIME_INTEL", place=None, province=None,
         ts="2026-10-16T08:00:00+00:00", court=False):
    out = {"stream": stream, "date": ts[:10], "place": place, "province": province, "precinct": None,
           "pi_kind": None, "court_step": court, "event_key": None, "stream_reason": None}
    return {"town": town, "run": run, "title": title, "content": "", "metadata": {"query": "q"},
            "timestamp": ts, "stream": stream, "outcome": out, "recorded": out}


def test_catch_scoring():
    case = {"id": "t", "sweep": "musina", "kind": "catch", "match": "store", "expected_stream": "CRIME_INTEL",
            "expected_place": ["Musina"], "one_record": True, "incident_date": "2026-10-16"}
    one = [_row(title="Musina store owner shot", place="Musina")]
    r = cb.score_case(case, cb.case_rows(case, one), "outcome")
    assert r["pass"] and r["merged"] is True and r["right_date"] == "all"
    two = one + [_row(title="store shooting", stream="GLOBAL", ts="2026-10-22T08:00:00+00:00")]
    r = cb.score_case(case, cb.case_rows(case, two), "outcome")
    assert not r["pass"] and r["merged"] is False
    assert r["right_stream"] == "some" and r["right_place"] == "some" and r["right_date"] == "some"
    r = cb.score_case(case, [], "outcome")
    assert r["caught"] is False and r["pass"] is False


def test_case_rows_stay_in_their_own_sweep():
    case = {"id": "t", "sweep": "musina", "kind": "catch", "match": "store"}
    rows = [_row(town="polokwane", title="store")]
    assert cb.case_rows(case, rows) == []


@pytest.mark.parametrize("rule,forbidden,row,ok", [
    ("absent", None, None, True),
    ("absent", None, {"title": "hit"}, False),
    ("not_placed_in_province", ["KwaZulu-Natal"], {"title": "hit", "province": "KwaZulu-Natal"}, False),
    ("not_placed_in_province", ["KwaZulu-Natal"], {"title": "hit"}, True),
    ("not_placed_at", ["Polokwane"], {"title": "hit", "place": "Polokwane"}, False),
    ("not_stream", ["CRIME_INTEL"], {"title": "hit", "stream": "GLOBAL"}, True),
    ("court_step_flagged", None, {"title": "hit"}, False),
    ("court_step_flagged", None, {"title": "hit", "court": True}, True),
    ("date_is_incident", None, {"title": "hit", "ts": "2026-10-08T00:00:00+00:00"}, False),
])
def test_keep_out_rules(rule, forbidden, row, ok):
    case = {"id": "k", "sweep": "musina", "kind": "keep_out", "match": "hit", "keep_out_rule": rule,
            "forbidden": forbidden, "incident_date": "2026-09-15"}
    rows = [_row(**row)] if row else []
    r = cb.score_case(case, cb.case_rows(case, rows), "outcome")
    assert r["pass"] is ok


def test_date_rule_without_records_is_not_tested():
    case = {"id": "k", "sweep": "musina", "kind": "keep_out", "match": "hit",
            "keep_out_rule": "date_is_incident", "incident_date": "2026-09-15"}
    assert cb.score_case(case, [], "outcome")["status"] == "not_tested"


def test_merge_hook_feeds_the_scorer(replay_card):
    def keep_first_per_case(rows):
        seen, out = set(), []
        for r in rows:
            if "carrim" in (r.get("title") or "").lower():
                if "carrim" in seen:
                    continue
                seen.add("carrim")
            out.append(r)
        return out
    card = cb.score(recorded=False, merge=keep_first_per_case)
    assert _case(card, "musina_carrim")["records"] < _case(replay_card, "musina_carrim")["records"]


def test_markdown_lists_every_case(replay_card):
    md = cb.to_markdown(replay_card)
    for r in replay_card["cases"]:
        assert r["id"] in md
