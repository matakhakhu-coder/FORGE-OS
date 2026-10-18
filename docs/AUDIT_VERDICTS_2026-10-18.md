# Module audit verdicts, 18 October 2026

Short record of the read-only module audit done for the 24 hand-checked Western Cape pins (main at 8f06226). Nothing was run against a real database for it; every verdict comes from reading code and the two data files.

## Decisions

1. **Checked pins stay out of the automatic engine.** They are curated data, not engine input. Never reprocess them through ingest.
2. **The checked-signal import is a curated-layer tool beside publish.py,** not an FMS module (`forge_modules/`) or a collector: both feed the engine these pins must skip. Built in Sprint 1 as `tools/checked_signals.py`.
3. **Checked pins get their own source type, `checked`.** In this repo "seed" already means the synthetic ZA-Divergent archive data (`tools/seed_data.py`), and the pins previously used `news`, which is neither documented layer.
4. **docs/signals is the source of truth** for checked pins. database.db is not in git and Vercel serves the committed dist/, so a build from a database missing a batch would silently drop pins.
5. **Lines on the map only for explicit, supported relationships** (Sprint 3). Today every pair of signals in a published case gets a line.

## Verdicts by part

| Part | Reaches the pins today? | Verdict |
|---|---|---|
| tools/publish.py, publisher/ | Yes | Use. Now guarded (Sprint 1). Case lines to fix (Sprint 3) |
| tools/checked_signals.py (was seed_checked_signals.py) | Yes | The one way checked pins get in |
| tests/ (standalone-signal, drawer) | Yes | Base for new tests |
| forage/collectors | Only as finders | Keep the Google News keyword collector; keep dork off |
| forage/processors | Name extraction only (no status check) | Run on private article text, not on pins |
| forage/engines | Cluster and decay only | Repair correlation to use incident time; leave the rest |
| forage/utils | No | Reuse hashing; fix Admiralty grades before use |
| flux/, forge_modules/flux | No | Leave unused for incident linking; repair is its own track |
| forge_modules/counterintel | No (skips reviewed) | Repair; useful for telling syndicated copy from independent reports |
| coalition_detector, emergence_engine | No | Leave; they need real actors |
| geo_enrichment | No | Optional reuse for labels (its province boxes are too tight for the Western Cape) |
| signal_enrichment, graph_sync, core/conclave, core/fms, core/pipeline | No | Leave as is |
| core/web, core/api (local app) | Map showed only raw or promoted signals | Fixed in Sprint 1: new CHECKED lens |
| core/gravity.py | No | Only once a real case exists |
| utils/wayback.py | No | Reuse to archive source links (needs the owner's go-ahead) |
| forge_security/ | No | Reuse if article text is ever shown |
| maintenance/system_decontamination.py | Would have reset the pins to `seed` | Fixed in Sprint 1: it keeps `checked` |
| surface/, wiki/, revenue/, bin/, scripts/ | No | Not relevant now |

## Known gaps (not fixed by Sprint 1)

- `app.py`, `tools/publish.py` and the FLUX add-on ignore `FORGE_DB` and always use the repo's database.db (Sprint 2).
- Name extraction and the actor bridge in mega_ingest can still read checked pins; the cluster and decay engines touch all non-dismissed signals. The import puts every checked field back before each publish, but Sprint 2 should confirm this with an isolated engine run.
- The FMS loader calls `register()` before checking declared engines, so a rejected module keeps what it registered; the local app may register hooks twice. Inferred from code, not run.
- News reports rarely carry detectives, witnesses or ballistics, so those links cannot be established from these sources however good the tools are.

## Sprint 1 choices that differ from the audit wording

- **Retracted pins get status `dismissed`,** not `retracted`: the signals table's CHECK constraint only allows raw, reviewed, promoted and dismissed. The retraction reason is kept in `metadata_json.retracted`, and the record stays in its batch file.
- **Fingerprints are full SHA-256 hashes** of each pin's canonical JSON, rather than forensics.py's 16-character device hash, so any change to a pin is caught.
