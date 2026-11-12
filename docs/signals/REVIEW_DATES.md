# Last reviewed dates

Adopted 12 November 2026, on the owner's go-ahead ("Start them").

Each pin's panel can show a small "Last reviewed" line. It shows that FORGE went back to the case after it was published, so a pin with no new development doesn't read as forgotten. It proves a review happened. It says nothing about the case itself.

## What counts as a review

A review is a check of a published pin's case that is **written down in a FORGE log** and that:

1. names the pin (its ID or its place, unmistakably), and
2. records that its sources were re-read, or that news was searched for new reporting on that case (an arrest, a charge, a court step, a correction), and
3. ran **after** the pin was published, and not in the future.

An owner-approved material update is also a review: it can't be made without re-reading the sources.

These do **not** count:

- the checks made before a pin was first published (that is the publication date, already shown as "added");
- a sweep's duplicate check against the live records, which compares a new report with the map but doesn't look into the pin's case;
- a review of a parked or held candidate that is not a live pin;
- a check someone remembers but no log records. Nothing is back-filled.

## Where it lives

`docs/signals/pin-clocks.json`, one entry per pin:

```
"<signal_id>": {
  "last_checked_at": "2026-10-25T09:45:00+02:00",
  "record": "Sprint 5 results, section 15c: arrest follow-ups, 25 Oct 2026",
  "outcome": "no_new_information"
}
```

- `last_checked_at`: when the check ran (or the end of its written time window), with a UTC offset.
- `record`: the private log the check is written in. Required. No log, no entry.
- `outcome`: optional, and only one value is allowed, `no_new_information`. Set it only when the log says the check found nothing new on that case. A check that found a lead, a possible update or related reporting leaves it out: the date alone shows it ran, and anything real goes through an owner-approved material update.

Write entries with `python tools/pin_clocks.py record <signal_id> --at <ISO> --record "<log>" [--outcome no_new_information]`. A routine write needs no approval and changes no pin file or fingerprint. `python tools/pin_clocks.py check-routine` refuses a write that changes anything else, removes an entry or moves a date backwards.

## What the map shows

- **Last reviewed date:** the later of `last_checked_at` and the pin's last material update (`last_reviewed_at` in the pin detail and in records.json).
- **Wording when nothing new was found:** "Last reviewed 25 Oct: no new information was found in the sources checked." Otherwise just "Last reviewed 11 Nov".
- **Never** "cold", "closed", "resolved", "solved", "dormant", "inactive", "no longer", "dead end", "abandoned", "gone quiet", "shelved" or "concluded". FORGE can't know any of that. The validator refuses those words in a review record.
- **When the latest review is the material update itself**, the panel's "Updated" line already gives that day, so the Last reviewed line is left out to keep the panel short. records.json still carries the date.
- **A pin with no recorded review shows no Last reviewed line.** Its panel keeps the dates it always had ("Happened ..., added ..."), and "added" is the publication date, labelled as such. In records.json, `last_reviewed_at` is blank.

## Checks

`tools/pin_clocks.py` refuses an entry that:

- is in the future (more than 5 minutes ahead);
- is before the pin was published, or before the incident;
- has no `record`, or a record longer than one line of 200 characters;
- has an `outcome` other than `no_new_information`;
- uses any of the words above in its record;
- names a pin that isn't in any batch.

The same check runs inside `tools/publish.py`, which refuses to build with a bad entry.

## The first entries (12 November 2026)

Only two logs met the rule when this was adopted:

- **Sprint 5 results, section 15c** (arrest follow-ups, 25 Oct 2026, 09:15 to 09:45 SAST): ten live Western Cape pins.
- **Connections run notes, 11 Nov 2026** (live pins checked against news up to 11 Nov): five live pins.

The 22 October follow-ups (section 13b) are older than section 15c for the same pins, so they add no date. Every other pin has no recorded review since publication and shows no Last reviewed line.
