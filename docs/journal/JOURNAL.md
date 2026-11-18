# FORGE Journal

Owner go-ahead to build the frontend: 18 Nov 2026.

The Journal is the written side of the map. The map shows where something is;
a journal entry is a field note about that place, written by a named person.
The page is `journal.html` on forgemap.co.za, linked from the header and footer
of every page.

## What exists now

- The Journal page, built by `tools/publish.py` from `docs/journal/entries.json`.
- Validation in `tools/journal.py`. A bad entry stops the build; it is never
  dropped quietly.
- While there are no entries the page shows an empty state and carries
  `noindex`, so search engines do not index an empty page. It gets a canonical
  link and joins the sitemap with the first published entry.

## What does not exist yet

- No upload, sign-in, editor or publish button on the site.
- No location sharing or movement trail.
- No photo attachment on entries.

Those are being scoped separately (field tracking and photo publishing). Until
they are built, an entry reaches the site only when the owner adds it to
`entries.json` and runs a publish.

## Rules

1. `entries.json` holds published entries only. The repository is committed and
   pushed, so drafts never go in it.
2. Example or sample entries never publish (`example` or `sample` set refuses
   the build).
3. Each entry needs `id` (lower-case words joined by hyphens), `title`,
   `published` (a date, not in the future), `author` (the named writer),
   `place` (as people say it), `summary` (up to 280 characters) and `body` (a
   list of paragraphs).
4. `pin` is optional: the 8-character id of a pin live on the map. The entry
   then links to `index.html?signal=<pin>`. A pin that is not live refuses the
   build, so a link never opens nothing.
5. `sources` is optional: a list of `{label, url}` with https links.
6. A journal entry is a person's field note. It does not change, confirm or add
   a map pin; pins keep their own approval rules.

## Entry shape

```json
{
  "id": "short-hyphenated-id",
  "title": "What the note is about",
  "published": "2026-11-18",
  "author": "Writer's name",
  "place": "Street, suburb, town",
  "summary": "One or two sentences.",
  "body": ["First paragraph.", "Second paragraph."],
  "pin": "0c5e795f",
  "sources": [{"label": "Source name", "url": "https://..."}]
}
```
