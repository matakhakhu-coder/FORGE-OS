# Location photos beside a pin

Added 18 November 2026. Enforced by `tools/location_photos.py`; records live in
`docs/signals/location-photos.json`; approved files in `docs/photos/files/`.

## What it is

A pin's panel can show up to two small photos of the *place* the pin's map
point is at, under its sources. The panel says on every photo that it is a
general photo of the place and not of the incident.

The idea: take a pin's latitude and longitude, look for public photos whose
own location data puts them near it, and show one that helps a reader picture
the place. It is visual location context only. It never says the photo shows
the incident, the people in it, or that the area is dangerous.

## Two kinds, never mixed

- **location_context**: a public, licensed photo of the place. Caption and alt
  text name the place only, with no event words (shooting, scene, victim and
  so on are refused).
- **incident_verified**: a photo of the incident. Needs an original source,
  a licence or written permission, and a recorded verification (method, who,
  when, how it is tied to this incident). Never a victim or a body. None
  exist yet.

## Where a photo's location comes from

Kept exactly as found, never upgraded:

| basis | means | shown as |
|---|---|---|
| camera_gps | the photo file's own camera GPS data holds a position | "Location from the photo file's camera GPS data" |
| page_location | the photo's page gives a position, but the file has no camera GPS (placed by the uploader, or carried over from Flickr or Panoramio) | "Location from the photo's page, not from camera data" |
| described | no position; the place is only named in the title or description | "No location data; the place is only named", with no distance |

Camera GPS is what a device recorded, not a check by FORGE; files can be
edited. The distance on the page is worked out at build time from the photo's
position and the pin's map point, and the pin's own uncertainty is shown with
it. Nothing is typed in from memory.

## Rules

- **Sources**: a page that states creator, licence and location (Wikimedia
  Commons for now). Search-engine results and image caches are refused, so
  the images that come back from a general web search are never used.
- **Licences**: CC0, public domain, CC BY or CC BY-SA, with a link to the
  licence. No NC (FORGE takes support payments) and no ND (FORGE resizes).
  Credit, licence link, source page and "what FORGE changed" show on the page.
- **Hosting**: FORGE hosts a reduced copy (640 px wide, metadata removed),
  with its SHA-1 recorded. No hotlinking, so no reader's visit reaches a
  third party and a changed file is caught.
- **Distance**: within 2 km of the pin's map point.
- **No generated imagery**: `ai_generated: false` is required; files whose
  title, description or categories say generated or illustration are refused.
- **Off-limits subjects** found by the research step: species specimens,
  graffiti and gang tags, "victims of violence", schools and creches, views
  from space. Each was found near a real pin in the first survey.
- **Places that get no photo**: a pin whose place note puts it at a home,
  house, yard, shack, property, school, creche, grave, cemetery, shelter,
  clinic or hospital.
- **Privacy check before approval**, recorded with who and when: no
  identifiable people, no readable number plates or house numbers, no private
  home, no children, no sensitive site.
- **Owner approval**: `status: approved`, `approval.by: owner` and a date.
  Candidates never reach the page; their files are never copied into the site.

## Steps

1. `python -m tools.location_photos nearby <signal id>`: research only. Lists
   Commons photos within 2 km, with basis, distance, licence and automatic
   refusals, in `docs/photos/candidates/`.
2. Pick one, add a `candidate` record, then `fetch <photo id>` and resize.
3. A person looks at it and fills in the privacy check.
4. The owner approves; publish.

## In every unattended run (26 November 2026)

`tools/autopublish.py` step 5b runs before the tests and the build. For every
new pin it searches Commons for licensed photos within 2 km, commits the
candidate file with the batch, and prints one line per pin: no photo (home,
school, grave or care site), no usable licensed photo, or the nearest usable
candidate.

**The run approves the photo itself (owner's choice, 26 Nov 2026).** The
sweep doing the run looks at the best candidate before calling autopublish:

1. Run `python -m tools.location_photos nearby <pin>`; take the nearest candidate with no auto refusals.
2. Download the Commons thumbnail and look at it. Use it only if it shows the
   place (a road, a square, a landmark) and shows no person who can be
   recognised, no readable number plate or house number, no private home, no
   child, nothing about the incident, and no victim. If in doubt, no photo.
3. Save it to docs/photos/files/ resized to 640 px wide with metadata removed,
   and add a record to location-photos.json with
   `approval: {by: standing, at: <date>, authorization: <standing id>}`,
   `privacy.checked_by: unattended-run` and a `privacy.note` saying what the
   run saw. Caption, creator, licence and Commons page as for any photo.
4. The page shows it labelled "Place photo, not the incident", with credit.

autopublish then checks every run-approved photo: every rule above must pass,
it must be a place photo (never an incident photo), and it must belong to a
pin in this run. Any that fails goes back to candidate and never shows; the
pin still publishes. If no photo fits, the pin publishes without one and the
report says so. Photos found near homes or schools are never searched.
`FORGE_SKIP_PHOTO_RESEARCH=1` turns the research off for one run.

Research also refuses files that are not of a place (`OFF_TOPIC`: banknotes,
birds and species, food, equipment, logos and similar), all found near real
pins on 26 November.

## Not in this version

Other photo sources (Mapillary, Flickr direct), street-level imagery, and any
incident photo. Each needs its own licence and privacy review first.
