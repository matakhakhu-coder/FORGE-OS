# Unattended publishing run

How a sweep's new pins go live without the owner reviewing each one. Built 21 November 2026 after the first approval-free test (the Flora Park pin).

## What has to be true

1. **An active standing authorization** in `docs/approvals/standing/`, with the owner's own end date. Without one, every step below refuses. See that folder's README.
2. **The new pins are in a checked batch** in `docs/signals`, listed in `index.json`, with their provenance, precision and incident-date entries, each checked by the sweep against `docs/SOURCING_POLICY.md` (traceable sourcing; local-paper-only incidents need independent corroboration; copies count once).
3. **Each pin has at least two different source sites.** This is the standing authorization's own condition, stricter than the sourcing rule: a single traceable national report can support a pin the owner approves, but not an unattended one.
4. **Each pin has its "What each fact rests on" entry** in `docs/signals/fact-bases.json`, written by the sweep from the pages it read and added with `tools/fact_bases.py`. Steps: `docs/signals/FACT_BASES.md`. autopublish refuses the whole run, before writing anything, if any pin has no valid entry, so no pin goes live with that section empty.

## The run

```
python tools/autopublish.py --authorization standing-<id> --db <database copy> --pins <signal_id> ... [--dry-run]
```

It checks the branch (main, up to date, nothing else staged), the authorization, each pin's fact-basis entry, writes each pin's `basis: standing` approval record after the machine checks, verifies the ledger (enforced, append-only against origin/main), loads the pins into the database copy, runs the full test suite, and builds the site (`publish.py` without `--deploy`, so it cannot push). `--dry-run` stops there, removes the records it wrote and restores `dist/`. Otherwise it commits the batch and records locally only, then runs `publish.py --deploy` with `FORGE_ALLOW_PUBLISH=1` set for that one process only, which pushes the records commit and the `dist/` commit together. Vercel deploys main; on 21 November the pin was live about 75 seconds after the push.

Approval records reach origin only together with a successful build. Any refusal or failed build or deploy removes the records the run wrote, undoes its local commits and restores `dist/`, so nothing reaches origin and the pins can be retried. (Before this change, 21 November: records were pushed before the build, so the Madiba Park pin's failed build left a pushed record that made every retry refuse it as already published.) A pin whose record is on origin still needs the owner: a standing authorization approves new pins only.

## Location photos for every new pin

Before calling autopublish, the sweep looks for a licensed place photo for each new pin and approves the best one itself if it passes the visual check (no recognisable people, plates, homes, children, victims or incident). Steps: `docs/signals/LOCATION_PHOTOS.md`, "In every unattended run". autopublish re-checks every run-approved photo before the build and turns back any that fails; a photo never blocks a pin.

## Tests no longer need editing for new pins

Counts of pins, months and ledger records in the tests are read from `docs/signals` and `docs/approvals/pins` (`tests/real_inventory.py`). Exact checks stay for the 86 pins published up to 21 November 2026. Checked on 21 November with a throwaway 87th pin dated 12 November: the full suite passed with no test edits.

## Location lookups (OpenStreetMap Overpass)

Write them as plain reads: an HTTP GET with the query in the URL, for example `curl -sG https://overpass-api.de/api/interpreter --data-urlencode 'data=[out:json];node["name"="Flora Park"](around:5000,-23.90,29.45);out;'`. The `-G` turns the data into a GET. A POST (plain `curl --data`, or `requests.post`) counts as sending data to a site and waits for the owner's approval in Sesame. That approval is a Sesame permission and is not bypassed; reads simply do not need it.

## Not set up

- **No schedule** for a daily run, and no phone-call alert. The owner has not chosen them yet.
- **Revoke at any time** by setting `revoked_at` and `revocation_reason` in the authorization file; the next run refuses.
