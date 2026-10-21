# Pin approval ledger

This folder records which exact pin data the owner approved for the authority database. Each approval is a small JSON file kept in Git, so anyone can check that every pin on the map matches something the owner said yes to.

An approval covers data only. It lets one exact pin payload into the authority. It never authorises a build, a publish or a deploy; each of those still needs its own approval.

## Layout

| Path | What it is | Can it change? |
| --- | --- | --- |
| `README.md` | This file | Yes, by reviewed commit |
| `policy.json` | Whether missing approvals refuse (`enforce`) | Yes, by reviewed commit |
| `schema/pin-approval.v1.json` | The record format | No. A new format is a new file |
| `_examples/example.json` | A sample record. The validator skips it | No |
| `<signal-id>/<approval-id>.json` | One record per approval | No. Append only |

`approval-id` is the UTC time the record was made plus eight random hex digits: `YYYYMMDDTHHMMSSZ-<8 hex>`, for example `20261021T013000Z-3f9a0c1e`. One folder per pin also gets around Git's inability to keep an empty folder.

## What a record holds

The schema file has the full rules. In short:

- **Identity:** `schema`, `approval_id`, `signal_id` (the pin's 36-character UUID).
- **What happened:** `action` (`approve`, `supersede` or `revoke`), `pin_version`, `supersedes`.
- **The approved data:** `batch_file` (the `docs/signals` batch that holds the pin), `approved_payload` (the pin's fingerprinted fields as approved) and `payload_sha256`. The hash is taken over the payload with sorted keys, UTF-8 and no spaces, which is the same value as the pin's fingerprint in `docs/signals`. Get it with `python tools/approvals.py payload-hash <signal-id>`.
- **Sources:** `source_hashes`, each a URL, a retrieval time and a content hash. Unknown values stay `null`.
- **Changes:** `diff_from_previous`, required on a supersede.
- **Who and when:** `owner` (a pseudonym, never an email address), `approved_at` (with time zone), `recorded_at`, `recorded_by`.
- **Reference:** `approval_reference`, a short opaque ID and a one-line summary. Chat text never goes into Git.
- **Basis:** `explicit`, or `legacy` for an approval given before this ledger existed. Only a legacy record may leave `approved_at` as `null`: an unrecorded date is left unknown, never invented.
- **Scope:** always `data-only`.

## The chain for each pin

1. Version 1 is an `approve` with `supersedes: null`.
2. Every later record names the current latest record in `supersedes` and raises `pin_version` by one.
3. `supersede` replaces a live approval with new data. `revoke` withdraws it. A fresh `approve` may only follow a revoke.
4. The pin's current approval is the last record in its chain.
5. After a revoke, the pin must be retracted or removed from its batch before any publish.

Records are never edited, renamed or deleted. To correct one, add a supersede or revoke record.

## Checking

```
python tools/approvals.py verify
python tools/approvals.py verify --enforce   # also refuse pins with no record
python tools/approvals.py verify --all       # list every finding
```

The same check runs inside `tools/checked_signals.py import --write` (before anything is written) and inside `tools/publish.py` (before the pins are compared with the database). A dry-run import prints the report and never refuses.

Findings come in three kinds:

- **Violation:** something is wrong with records that exist. A malformed record, a broken chain, a pin whose data no longer matches its approved hash, a revoked pin still live, or a record or schema file changed, deleted or renamed compared with `origin/main` or anywhere in the history of `HEAD`. A violation always refuses.
- **Gap:** an active pin with no record, or a check that could not run (no git, no `origin/main`). A gap refuses only while enforcement is on.
- **Warning:** reported only. For example, a record for a pin that is in no batch, or a retracted pin whose approval was never revoked.

## Rollout

The ledger starts empty, so every existing pin is a gap. `policy.json` therefore starts with `"enforce": false`: imports and publishes keep working and print how many pins have no record. Nothing about current production changes until these steps happen, each with its own owner approval:

1. Add one `legacy` record for each existing pin, with unknown values left `null`.
2. Run `python tools/approvals.py verify --enforce` and confirm it reports no gaps.
3. Change `policy.json` to `"enforce": true` in a reviewed commit.

For a one-off strict run before step 3, set `FORGE_APPROVALS_ENFORCE=1`. The environment can only turn enforcement on, never off.

## Limits

- This repository has no GitHub checks, so the validator runs only where the tools run. A direct push to `main` can skip it; the history check catches an edited record the next time any tool runs.
- The ledger proves what was recorded, not who pressed the keys. Whoever can push to the repository can add records.
- The staging role (`FORGE_ROLE=staging`, see `core/db/paths.py`) refuses to import hand-checked pins or publish, and keeps ingest on its own database file. It is the code half of the isolation only: the separate account, folders, deny list and empty staging database are later steps, and the agent account that starts a staging run can still bypass all of it.
