# "What each fact rests on"

Every pin's panel has a section that lists the pin's main facts and, for each one, what it rests on: the source, a word-for-word quote, and how strong the footing is. This page says how the section works and how an unattended run fills it for new pins.

## How the section works

- **Data:** `docs/signals/fact-bases.json`, one entry per pin, keyed by signal id.
- **An entry:** `status: "audited"`, `police_party` (true when police are a party to the incident, so their account is not an independent finding), `audited_on` (the date the panel shows, e.g. "26 November 2026"; older entries without it show 19 October 2026), and exactly six `facts`.
- **A fact:** `label` (Deaths, Date and time, Place, ...), `claim` (what is said), `basis` (how strong: "Police account only", "One report", "Two reports", "Reports disagree", "Not reported in the saved sources", ...), an optional `detail`, `sources` (each with `name`, `url`, `archive`, `quote`) and `conflicts` (quotes from a source that says otherwise).
- **Build:** `tools/publish.py` `_fact_basis_for` puts the entry on the pin as `fact_basis`, but only if it is audited and has exactly six facts. Anything else shows "not yet audited" with an empty list. `publisher/templates/map.html` draws it.
- **Not part of the pin's approved data:** the entry is not in the pin's payload hash, so filling it in does not change an approved pin.

## In every unattended run

For each new pin, after the checked batch is written and before autopublish:

1. Save the text of each source page the sweep read for the pin (outside the repo). Caxton papers (citizen.co.za local titles) are not read automatically, so they are never quoted; say in a detail that the pin's other source was not read.
2. Write six facts in a JSON file keyed by signal id. Use the pin's category template: crime pins Deaths / Date and time / Place / How or Method / Reported motive or What was taken / Legal status or Arrests; Infrastructure pins What failed / Date and time / Place / Cause / Impact / Status.
3. Rules for each fact:
   - cite only the pin's own published sources, with a quote copied word for word;
   - name who said it (police, EMS, JMPD, a relative) in the source name or detail;
   - one origin is one origin: when both reports rest on the same statement, use an "... account only" basis and say "same statement" in a detail; "Two reports" or "Independent origins identified" need two different source links and reports that did not copy one statement;
   - when the sources differ, record the other version under `conflicts`;
   - when no saved source says it, basis "Not reported in the saved sources" and no source;
   - the Place fact says when the map point is approximate or estimated, and how far out;
   - never name a child or a victim the pin does not name; never say one incident caused another.
4. Check the quotes, then add the entries:

```
python tools/fact_bases.py quotes --entries new.json --texts <saved page text files>
python tools/fact_bases.py add --entries new.json --authorization standing-<id>
```

`add` writes nothing if any entry fails, and stamps each written entry `approval: {by: standing, authorization, checked_by: unattended-run}`.

5. autopublish checks every pin again (`fact_bases.gate_problems`) before it writes any approval record and refuses the run if one is missing or invalid. `python tools/fact_bases.py check --pins <id> ...` runs the same check by hand.

Tests: `tests/test_fact_bases_auto.py`.
