# FORGE — Roadmap

## Current Phase: CT-1 — Contextual Tunneling

> Gravity-based feed and signal filtering anchored to an active case context.
> All changes are additive — no DB schema changes, no existing routes removed.

---

### Step 1 — core/gravity.py
- [ ] Copy `core/gravity.py` into the project at `core/gravity.py`

---

### Step 2 — app.py
- [ ] Delete the existing `@app.route("/api/feed")` function (lines ~2955–3366)
- [ ] Open `app_routes_patch.py` and paste the full block (from `# ── PASTE THIS BLOCK` to end of file) into `create_app()`, after the `@app.route("/api/anomaly/baselines")` route

New routes this adds:
- `GET /api/cases/<id>/anchors`
- `GET /api/cases/<id>/fetch-suggestions`
- `GET /api/feed` (gravity-aware replacement)
- `GET /api/surface/signals/context`

---

### Step 3 — Templates

#### 3a. feed.html
- [ ] Replace `templates/feed.html` with the delivered version (superset, nothing removed)

#### 3b. signals.html
- [ ] Replace `templates/signals.html` with the delivered version (Phase 14 filter logic preserved)

#### 3c. case_detail.html
- [ ] **Edit 1** — Replace the `page-header__eyebrow` div (line ~28) with `PATCH 1` from `case_detail_patch.html`
- [ ] **Edit 2** — Replace the `page-header__actions` div (line ~36) with `PATCH 2` from `case_detail_patch.html` (adds ⊛ Context Feed and ⊡ Context Signals buttons)
- [ ] **Edit 3** — Inside `{% block scripts %}`, paste the `PATCH 3` script block from `case_detail_patch.html` at the very top, before the existing Phase 30E script

#### 3d. base.html
- [ ] **Edit 1** — Find `<div class="topbar__meta" id="js-clock">——</div>` and insert `PATCH 1` div from `base_patch.html` immediately before it
- [ ] **Edit 2** — Find the closing `</script>` of the base.html inline script block (line ~444) and paste `PATCH 2` script content from `base_patch.html` just before it

---

### Step 4 — CSS
- [ ] Append the full contents of `static/ct_styles.css` to `static/css/main.css`

---

### Verification Checklist
- [ ] Open a case (e.g. `/cases/42`) — Focus Active indicator appears, topbar pulses
- [ ] Click **⊛ Context Feed** — CT banner shows, gravity-filtered feed loads
- [ ] Drag Gravity slider to 80 — feed tightens to direct actor/location matches only
- [ ] Click **⊕ Global View** — full firehose returns, case context preserved
- [ ] Click **⊡ Context Signals** — Signal Monitor opens with Gravity column and case filter
- [ ] Test `/api/feed` without `case_id` — must behave exactly as Phase 29.3 (no gravity)
- [ ] Test `/signals` without `case_id` — must behave exactly as Phase 14

---

_Last updated: Phase 41 committed — CT-1 integration pending._
