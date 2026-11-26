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

`tools/autopublish.py` step 7b runs the research step for every new pin, after
the site builds and before the commit. Its candidate files go into the same
commit as the batch, and the run prints one line per pin: no photo (home,
school, grave or care site), no usable licensed photo within 2 km, or the
nearest usable candidate with its distance, licence, creator and Commons page.

- It never blocks or delays a pin. If Commons is down or nothing fits, the
  pin is published without a photo and the report says so.
- It never approves a photo. The standing authorization covers new pins only;
  each photo still needs a person's privacy check and the owner's approval
  (steps 2 to 4), then a publish.
- Research also refuses photos that are not of a place (`OFF_TOPIC`:
  banknotes, birds and species, food, equipment, logos and similar), all found
  near real pins on 26 November.
- `FORGE_SKIP_PHOTO_RESEARCH=1` turns the step off for one run.

## Not in this version

Other photo sources (Mapillary, Flickr direct), street-level imagery, and any
incident photo. Each needs its own licence and privacy review first.
