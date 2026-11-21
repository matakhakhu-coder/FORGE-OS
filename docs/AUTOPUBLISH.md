# Unattended publishing run

How a sweep's new pins go live without the owner reviewing each one. Built 21 November 2026 after the first approval-free test (the Flora Park pin).

## What has to be true

1. **An active standing authorization** in `docs/approvals/standing/`, with the owner's own end date. Without one, every step below refuses. See that folder's README.
2. **The new pins are in a checked batch** in `docs/signals`, listed in `index.json`, with their provenance, precision and incident-date entries, each checked by the sweep against `docs/SOURCING_POLICY.md` (traceable sourcing; local-paper-only incidents need independent corroboration; copies count once).
3. **Each pin has at least two different source sites.** This is the standing authorization's own condition, stricter than the sourcing rule: a single traceable national report can support a pin the owner approves, but not an unattended one.

## The run

```
python tools/autopublish.py --authorization standing-<id> --db <database copy> --pins <signal_id> ... [--dry-run]
```

It checks the branch (main, up to date, nothing else staged), the authorization, writes each pin's `basis: standing` approval record after the machine checks, verifies the ledger (enforced, append-only against origin/main), loads the pins into the database copy, and runs the full test suite. `--dry-run` stops there and removes the records it wrote. Otherwise it commits and pushes the batch and records, then runs `publish.py --deploy` with `FORGE_ALLOW_PUBLISH=1` set for that one process only. Vercel deploys main; on 21 November the pin was live about 75 seconds after the push.

Any refusal removes the records the run wrote and commits nothing.

## Tests no longer need editing for new pins

Counts of pins, months and ledger records in the tests are read from `docs/signals` and `docs/approvals/pins` (`tests/real_inventory.py`). Exact checks stay for the 86 pins published up to 21 November 2026. Checked on 21 November with a throwaway 87th pin dated 12 November: the full suite passed with no test edits.

## Location lookups (OpenStreetMap Overpass)

Write them as plain reads: an HTTP GET with the query in the URL, for example `curl -sG https://overpass-api.de/api/interpreter --data-urlencode 'data=[out:json];node["name"="Flora Park"](around:5000,-23.90,29.45);out;'`. The `-G` turns the data into a GET. A POST (plain `curl --data`, or `requests.post`) counts as sending data to a site and waits for the owner's approval in Sesame. That approval is a Sesame permission and is not bypassed; reads simply do not need it.

## Not set up

- **No schedule** for a daily run, and no phone-call alert. The owner has not chosen them yet.
- **Revoke at any time** by setting `revoked_at` and `revocation_reason` in the authorization file; the next run refuses.
