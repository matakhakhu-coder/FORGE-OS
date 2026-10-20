# ZA-Divergent map-first overhaul sprints

This agreed four-sprint plan is the shared reference for continuing the overhaul in chunks, with a full-viewport map and compact overlays on mobile and desktop.

## Scope and current status

- Sprint order approved; implementation of this overhaul has not started. Recording the plan does not authorize coding or publishing.
- Handle the work directly, without delegation, as requested.
- Desktop changes are intentional shared design changes, not accidental mobile spillover. Verify against the agreed redesigned desktop baseline rather than promising an identical desktop appearance.
- World Monitor is a layout reference: the map is the foundation and controls are subordinate. Do not copy its styling.
- Preserve existing tools and functionality, working mobile viewport fit, separated map attribution, search, settings, data freshness, incident drawers and ticker links.
- The earlier mobile layout sprints are separate completed work; these four sprints describe the newly agreed overhaul.

## Sprint 1: Map-first foundation

**Applies to:** Mobile and desktop, with mobile-specific header changes.

### Settled navigation decision before Sprint 1

Use a Tools menu on mobile and keep visible tool labels on desktop where there is room. Settle this before building Sprint 1, not Sprint 2: the mobile header already includes a menu, so plan its relationship with Tools now to avoid colliding controls or rebuilding the header. The mobile Tools menu opens a labelled list and closes after selection. Unfamiliar symbols retain discoverable names and accessible labels; long-press alone is not sufficient.

### Goals

- Make the map fill the available browser viewport, placing compact controls and header elements over it rather than allocating separate stacked page bands.
- Keep overlays compact or appropriately translucent so the map does not remain hidden under opaque bars.
- Collapse the mobile header to logo, search trigger and menu. Retain accessible search, settings and data freshness, using expandable areas where needed.
- Adapt the existing compact desktop header as an overlay rather than applying the mobile header redesign to desktop.
- Keep a thin ticker. Move repeated branding and support links into navigation or an About area, away from the map screen.
- Preserve visible map attribution.
- Full viewport means the available browser viewport, not hiding the browser's own controls. Absolute positioning is an implementation option, not a requirement.

### Acceptance checks

- Resizing and changing phone browser toolbar height cause neither page overflow nor a hidden map.
- Map dragging works outside controls.
- Search, menus and settings remain usable, and data freshness remains accessible.
- Ticker and attribution do not overlap.
- The existing mobile subtitle removal and viewport fit remain intact.

**Status:** Planned, not started.

## Sprint 2: Navigation and compact controls

**Applies to:** Shared navigation cleanup, with responsive mobile controls.

### Goals

- Keep Filter and the existing category panel. Remove only the duplicate Streams shortcut.
- Preserve the underlying Streams panel, its counts and toggles for Crime & Security, Infrastructure, Priority Intelligence, and Africa & World. Repository inspection confirmed that Streams and Filter open the same panel.
- Keep all six remaining tools available: Area Check, Wire, Cases, Graph, Entities and Watchlist.
- Compact mobile zoom controls to plus and minus, with a Reset symbol and Filter icon. Preserve Reset access if it is placed inside a menu.
- Keep adequate touch targets even when the visible icons are small.
- Follow the navigation decision settled before Sprint 1: a Tools menu on mobile, with visible tool labels on desktop where there is room. Do not reopen the menu-versus-rail decision in this sprint.

### Acceptance checks

- Every remaining tool opens and closes, with no lost functionality.
- Filter opens the existing category panel; category toggles update pins and clusters.
- Category counts and toggles remain available after removing the duplicate Streams shortcut.
- Zoom, Reset and Filter remain usable with adequate touch targets.
- Keyboard focus and accessible names work.
- Closing panels restores map interaction without trapping input.

**Status:** Planned, not started. Navigation choice is settled in Sprint 1.

## Sprint 3: Data framing and readability

**Applies to:** Mobile and desktop.

### Goals

- Fit the initial map to incident bounds with padding for overlays, controls and ticker.
- Build the framing function with `heading` as a parameter, as requested, and keep overlay padding configurable. This lets us try the framing earlier and adjust padding values after Sprint 2 without rebuilding the function. The earlier trial does not move the rest of Sprint 3 ahead of the agreed order.
- Make Reset use the same framing logic.
- Include a sensible maximum zoom and a South Africa fallback when no data is available.
- Improve dim status and tool text contrast.
- Assess basemap contrast separately before changing providers; do not indiscriminately make every element lighter.
- Keep clustering semantics unchanged. Counts represent incidents, not an invented intensity or danger score.

### Acceptance checks

- Initial clusters remain clear of controls and ticker on phone and desktop sizes.
- Empty and single-point datasets produce sensible views.
- Reset restores the agreed framing.
- Text and map labels remain legible, and attribution remains intact.
- Clustering retains its existing meaning.

**Status:** Planned, not started.

## Sprint 4: Cross-device regression gate

**Applies to:** Both layouts, including narrow and typical phones, tablet, landscape and desktop.

### Goals and acceptance checks

- Verify map pan, pinch/zoom and Reset.
- Verify search, all tool panels and category filtering.
- Verify incident drawers and ticker links.
- Verify keyboard navigation, accessible names and adequate touch targets.
- Check safe-area spacing, viewport fit and absence of unwanted page scrolling.
- Check for browser errors.
- Compare desktop against the agreed redesigned baseline.
- Distinguish browser emulation from physical-device testing when reporting results.
- Do not publish any implementation until its agreed checks and review pass. Verification must accompany each sprint's review; this final gate checks the combined result.

**Status:** Planned, not started.

## Reference observations from the preceding check

These are the recorded observations from the preceding Chromium check at 390 × 844 and 1366 × 768, not a new regression pass:

- The mobile map began about 173 pixels down, not the roughly 490 pixels described in the older screenshot analysis.
- Mobile already had no page overflow, no subtitle, and attribution above the ticker without collision.
- The mobile header was about 131 pixels tall; the left tool panel about 100 pixels wide and the right controls about 103 pixels wide.
- The map container already occupied about 70 percent of phone height. Its usable width was reduced by the two labelled control columns.
- Desktop already had a compact, approximately 52-pixel single-row header.
- The checked build had 37 Crime & Security pins and zero in the other three categories. These are recorded counts, not a promise that future data will stay unchanged.

## Continuation point

Resume with Sprint 1 only after implementation is requested. Use this note to track completed checks and agreed changes as work proceeds. The navigation decision is settled before Sprint 1: Tools menu on mobile, visible labels on desktop where there is room. Integrate that choice into the mobile header from the outset. Trial the parameterized framing function early, then adjust its padding after Sprint 2.
