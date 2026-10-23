# Collector regression bench (Batch 1)

The bench measures the collectors; it changes none of them. It replays the Civic Intelligence collector and the Google News monitor (town keyword, infrastructure-preset and PI-preset runs) over what they saved in the four manual town sweeps of 23 October 2026, and scores them against fixed reference cases.

- Reference cases: `data/bench/collector_bench_cases.json` (22 cases that should be caught, 10 that should be kept out or flagged). Each has its source link where the sweep note gives one, incident date, expected stream, acceptable places, the precinct where the note states one, and whether it should arrive as one record.
- Saved inputs: `data/bench/collector_bench_replay.jsonl.gz`, 434 rows the sweep runs added that name the sweep area or match a case, plus the total rows per run.
- Bench: `tools/collector_bench.py`. Replay runs the collectors' own `build_signal` / `entry_to_signal` with outbound connections blocked, and writes nothing.
- Baseline for today's code: `docs/bench/collector_bench_baseline_2026-10-23.json` and `.md`.
- Tests: `tests/test_collector_bench.py` checks the bench, not today's collector behaviour, so a batch that improves a collector does not break them.

## Use

```
python tools/collector_bench.py score                  # replay the current code
python tools/collector_bench.py score --recorded       # what the sweeps saved
python tools/collector_bench.py score --out after.json --md after.md
```

Each later batch runs `score` before and after its change and reports the difference per case. A batch that adds a de-duplication step passes it to `score(merge=...)`.

## Results by batch

- **Batch 1 (9384d13), baseline:** 17 of 22 caught, right stream on every record 5, full pass 1; keep-out 0 of 10.
- **Batch 2 (c877631), crime classifier:** `docs/bench/collector_bench_batch2_2026-10-23.json` and `.md`. 17 of 22 caught, right stream on every record 10, full pass 1; keep-out 1 of 10. Changed cases: UNIVEN, Madombidzha, Shandesh, Golf Club and Cricket Club now all Crime & Security (each still fails on place, merge or date); the R524 crash keep-out now passes (9 records, none in Crime). No case got worse. Noise: in the Newcastle keyword run, rows naming the area in Crime & Security went from 34 to 38. One is the Newcastle businessman stabbing; three are Australian Newcastle stories (Windale, the Stockton inquest, the vacant-land body), a look-alike place problem left for Batch 4.

## Limits

- The replay only covers what the collectors fetched on 23 October. New sources or searches (Batches 7 and 8) need a live run on database copies, scored against the same cases.
- The replay re-runs classifying and placing per saved row. It does not re-run the collectors' in-run de-duplication, so record counts are the saved counts.
- Collectors output no precinct yet, so precincts are recorded but not scored.
- A case's records are every saved row in that sweep's runs that matches its pattern, including rows whose text does not name the town. That is why some counts differ from the sweep notes, which counted only rows naming the area.
