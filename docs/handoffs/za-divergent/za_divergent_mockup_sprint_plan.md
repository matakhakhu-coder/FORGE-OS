# ZA-Divergent mockup-led sprint plan

This revised four-sprint plan combines the mobile mockups and settled navigation choices for review before implementation begins.

## Scope and approval

- This is a new review note, replacing the earlier overhaul plan as the proposed continuation reference once approved. The earlier note remains unchanged.
- No implementation or publishing is authorized by recording this plan. Review and approval come first.
- Carry out the work directly, without delegation, as requested.
- Make routine design decisions using standard UI/UX principles rather than asking the user to choose between implementation options. Put the recommended design in this plan for review and approval. Ask only when a material requirement cannot be resolved from the agreed intent.
- The earlier completed mobile sprints are separate from this new overhaul.
- The map-first foundation applies to mobile and desktop. Desktop changes are intentional, not accidental mobile spillover. Keep visible desktop tool labels where there is room.
- Preserve existing tools, category filtering, search, settings, data freshness, incident drawers, ticker links, viewport fit and visible attribution.
- World Monitor is a layout reference, not a design to copy.

## Mockup Specifications

The user's two crude mobile mockups show the Tools menu open and closed. Treat them as a reference for layout intent, not a literal specification or pixel-perfect design. Retain flexibility in spacing and proportions, prioritizing legibility, accessibility and usable map space over literal reproduction. A Tools menu on mobile is settled. The recommended presentation below resolves routine design details for review rather than offering competing options.

- **Map first:** Southern Africa and the South African incident clusters are the main visible content. The map fills the available browser viewport beneath compact overlays instead of occupying a gap between large header and footer bands.
- **Tools open:** The mockup illustrates a compact vertical dropdown labelled Tools showing Area Check, Wire, Cases, Graph, Entities and Watchlist. Preserve the labelled tool access, but do not treat the illustrated dropdown shape or opening motion as mandatory.
- **Tools closed:** The labelled list disappears, leaving more of the map unobstructed. Keep the Tools trigger available.
- **Cluster visibility:** Yellow numbered clusters remain prominent, including the mockup's 12 near Cape Town and 16 near Johannesburg. The images also show 3 and 6. These numbers illustrate visibility and placement, not values to hard-code or guarantee in live data.
- **Map controls:** Compact right-side map controls remain available without recreating the wide text-button panel.
- **Attribution:** Keep Leaflet, OpenStreetMap and CARTO attribution visible and separate from the ticker while those are the services in use.
- **Visual direction:** Retain ZA-Divergent's dark map and yellow cluster language. Improve readability without copying another product's styling or treating cluster colour as a new danger score.

## Decision settled before Sprint 1

Use a **Tools menu on mobile** and **visible tool labels on desktop where there is room**. The icon rail is not the selected mobile approach.

Recommended design: put a clearly labelled Tools trigger in the compact mobile header beside the search trigger and general-menu control. Tools opens an anchored dropdown below the header, not a sliding side panel. Keep the general menu for site navigation, settings and support links, so its purpose does not duplicate Tools. The Tools dropdown contains the six named tools and closes after selection or outside activation. Opening another header menu closes the previous one to avoid stacked overlays. Keep animation restrained and respect reduced-motion preferences.

Use approximately 44 by 44 CSS-pixel touch targets, visible keyboard focus, accessible expanded-state labels and Escape-to-close behavior that returns focus to the trigger. On the narrowest screens, shorten branding before compressing touch targets or wrapping the header. Search and data freshness remain accessible. Unfamiliar icons retain accessible names; long-press alone is not a substitute for labels. These are recommended design decisions for the user's review, not changes already implemented.

## Sprint 1: Map-first foundation and header

**Applies to:** Mobile and desktop, with a mobile-specific compact header.

### Work

- Fill the available browser viewport with the map. Full viewport does not mean hiding the browser's controls. Absolute positioning is an option, not a requirement.
- Place compact header elements and controls over the map. Avoid recreating opaque bars that still hide it.
- Collapse the mobile header to logo, search trigger and menu, incorporating the settled Tools-menu layout from the outset.
- Adapt the existing compact desktop header as an overlay, retaining desktop labels where space permits.
- Keep the ticker thin. Move repeated branding, footer support links and the donate link into navigation or an About area rather than deleting access to them.
- Preserve attribution and existing subtitle removal.

### Review checks

- The map remains full-viewport through resizing and changes to phone browser toolbar height, without unwanted page overflow.
- Header, Tools trigger, general menu and search do not collide.
- Map dragging works outside controls; search, settings and freshness remain accessible.
- Ticker and attribution do not overlap.

**Status:** Proposed, not started. Review this note before beginning.

## Sprint 2: Tools and compact controls

**Applies to:** Shared navigation cleanup and responsive mobile controls.

### Work

- Complete the mobile Tools menu, using the mockups as a flexible layout reference, with Area Check, Wire, Cases, Graph, Entities and Watchlist.
- Keep visible desktop tool labels where there is room. Do not reopen the menu-versus-rail decision.
- Remove only the duplicate Streams shortcut. The preceding repository inspection found that Streams and Filter open the same category panel.
- Preserve that panel, its counts and its toggles for Crime & Security, Infrastructure, Priority Intelligence, and Africa & World.
- Use compact plus and minus controls, a Reset symbol and the Filter icon on mobile. Preserve Reset access if placed inside a menu.
- Keep adequate touch targets and accessible names even where the visible controls are small.

### Review checks

- Review both Tools-open and Tools-closed states against the mockups' intent: labelled access when open and an unobstructed map when closed, not exact visual reproduction.
- All six tools open and close; selecting a tool closes the dropdown.
- Filter still opens the category panel and its toggles update pins and clusters.
- Counts, zoom, Reset, keyboard focus and accessible names remain usable.
- Closing a panel restores map interaction without trapped input.

**Status:** Proposed, not started.

## Sprint 3: Parameterized framing and readability

**Applies to:** Mobile and desktop.

### Work and early trial

- Fit initial incident bounds with configurable padding for the header, controls and ticker.
- Make the framing function accept overlay-padding inputs so it can be tried earlier, then have its padding values adjusted after Sprint 2 without rewriting the function. The rest of Sprint 3 stays in the agreed order.
- The earlier discussion called an input `heading`. The practical approach discussed afterward was to measure the actual header height and use that measurement for top framing padding. This is not a map-bearing or rotation requirement.
- Keep the map itself full-screen. Header measurement changes the framing padding, not the map's top edge.
- Make Reset use the same framing logic.
- Include a sensible maximum zoom and a South Africa fallback for an empty dataset.
- Improve dim status and tool text contrast. Assess basemap contrast separately before changing providers, rather than making every element lighter.
- Preserve current clustering semantics: numbers represent incident counts, not an invented intensity or danger score.

### Review checks

- Initial clusters remain visible and clear of overlays on phone and desktop layouts, with the mockups as the visual reference rather than fixed counts.
- Padding can be retuned after the final controls are in place.
- Empty and single-point datasets produce sensible views.
- Reset restores the agreed framing.
- Map labels, interface text and attribution remain legible.

**Status:** Proposed, not started. Early framing trials are allowed only after implementation is approved.

## Sprint 4: Combined regression gate

**Applies to:** Narrow and typical phones, tablet, landscape and desktop.

### Checks

- Map pan, pinch/zoom and Reset.
- Search, Tools-open and Tools-closed states, all six tool panels and category filtering.
- Incident drawers and ticker links.
- Keyboard navigation, accessible names and adequate touch targets.
- Safe-area spacing, viewport fit, changing browser toolbar height and absence of unwanted page scrolling.
- Attribution and ticker separation, plus cluster visibility around overlays.
- Browser errors and comparison against the intentionally redesigned desktop baseline.
- Clearly distinguish browser emulation from physical-device tests when reporting results.

Verification accompanies each sprint's review; this final gate checks the combined result. Do not publish an implementation until its agreed checks and review pass.

**Status:** Proposed, not started.

## Continuation point

The user reviews this new note and brings back any changes or approval. Only then begin Sprint 1. Track completed work and checks against this plan as the overhaul proceeds in chunks.
