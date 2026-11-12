# Reported routes: the rule

Owner: Matamela Ramovha. Decided one at a time on 12 November 2026, from the
cross-border routes scope. This file is the rule `tools/routes.py` enforces.

## The three decisions

1. **Route lines may go on the map.** The batch rule that once said "no map-line
   links" (opened for story threads on 11 Nov 2026) now also allows reported
   routes. A route is a different kind of line from a thread: a thread puts
   South African events in time order; a route joins one South African case to
   a place abroad. They are kept apart in data (`docs/threads/` and
   `docs/routes/`) and on the page (thread view and routes view).
2. **A seizure with no arrest or charge can be a record**, of the kind
   `seizure`. It shows **only** in the "Reported routes" view (Africa and the
   world). It never appears on the everyday South Africa map, in the live pin
   count, in the charts, or in records.html, records.csv or records.json.
3. **A place abroad is a specific city, port, airport or border post** that a
   source names, with its own label, "Place, Country". Never a country on its
   own, never a region.

## When a line is drawn

- A source names **both** places for the **same** case: where it was found in
  South Africa, and the named place abroad. Each end carries its own quote,
  link and outlet; the quote for the place abroad must name that place.
- Who says so is always shown: police, customs, a court, the source itself,
  or a named party or agency.
- The far end is a place on the line, not an incident pin. It needs no record
  of its own, and nothing happened there that FORGE claims.
- The line shows what was reported, not a checked path. It does not say the
  drugs or people were made, packed or recruited at the far end, only what the
  source said (for example: "the flight left from").
- A case the owner has not approved (`status: held`) is never public.
- Where sources disagree (a value, a weight, a date, an origin), the case
  file records the disagreement and the page shows it.

## Guards against bias

- No line, label or summary may call a place or its people a threat, a
  hotspot, a source country, a hub or anything like it; `tools/routes.py`
  refuses that wording.
- No totals per country are published. Cases are grouped only by the two
  places a line joins.
- The routes view always carries the caption: "A line joins two places a
  source names for the same case. It shows what was reported, not a checked
  path, and says nothing about the place or its people." It also says that
  places with more press and customs reporting show more routes.
- Nationality is never a route end or a label.

## Pilot

- First case: OR Tambo International Airport, 25 Oct 2026, about 250 kg of
  cocaine; the SAPS national media statement of 26 Oct 2026 says it "had
  arrived on a flight from São Paulo, Brazil." Approved by the owner on
  12 Nov 2026.
- Second case: OR Tambo International Airport, 25 Aug 2026, more than
  333 kg of cocaine; the SARS media release of 25 Aug 2026 says it came "on a
  flight arriving from Sao Paulo, Brazil." Held at first, approved by the
  owner on 12 Nov 2026. It joins the same São Paulo to OR Tambo line.
- Pilot cap: 24 approved cases.

## Checks

`python tools/routes.py check` validates every case; the publish stops on any
problem, before anything is deployed.
