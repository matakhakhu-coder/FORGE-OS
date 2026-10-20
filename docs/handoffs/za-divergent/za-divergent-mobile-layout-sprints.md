# ZA-Divergent mobile layout

Fit the existing header, map, controls, drawers and footer within the phone screen without forcing the user to drag the page to reveal them. This is a layout-only project, scoped into three sprints.

## Scope boundaries

- Keep the existing desktop layout unchanged.
- Keep existing features, content, labels and links. Changes to their content belong in a separate scope.
- Do not code or push anything during scoping. This note records the agreed requirements, not permission to begin implementation.
- World Monitor illustrates fitting controls and open panels within a phone screen; the requirement is to arrange Divergent's existing elements, not add its features.

## Sprint 1: Header and map lock

- Fit the existing desktop header elements on mobile: ZA-Divergent branding, a full search bar rather than only a magnifying glass, the clock, the existing hamburger/settings controls and the Open Source label.
- Keep the map underneath the interface, with a top-down view centred on Africa.
- Fit the header and map into the mobile screen without having to drag from the Filter/Zoom pill to force the header into view.
- The map remains underneath an opened panel and is visible again when that panel closes. Map lock refers to its layout within the screen, not a request to remove map navigation.

## Sprint 2: Controls and drawers

- Move the existing bottom controls, including Area Check, Wire and Streams, to the sides.
- Keep Filter, Zoom In, Zoom Out and Reset in their existing control area.
- Area Check and Wire open as drawers over the map, fitting within the mobile layout rather than fighting it. Close the drawer to return to the map underneath.
- Preserve the existing functions; this is a placement and fit change, not a feature redesign.
- Keep the Leaflet and © OpenStreetMap contributors attribution above the horizontal scrolling ticker.
- Keep the ticker and the existing footer below it within the mobile layout.
- Preserve the footer wording and links: ZA-DIVERGENT ARCHIVE, FORGE OSINT ENGINE, ALL SOURCES OPEN, How It Works and Support Independent OSINT leading to Ko-fi.
- Preserve the generated timestamp as existing footer content. Changing the timestamp or other footer content is outside this layout scope.

## Sprint 3: Verification

- Verify the complete layout on mobile screens: header, full search bar, clock, settings, Open Source label, map, side controls, drawers, attribution, ticker and footer.
- Confirm the user does not need to force or drag the page to reveal the header or controls.
- Confirm Area Check and Wire fit when open, close cleanly and leave the map underneath.
- Confirm the existing desktop layout and existing features remain unchanged.
