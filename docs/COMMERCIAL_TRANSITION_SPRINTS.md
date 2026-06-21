# FORGE Commercial Transition — Technical Sprint Layout

**Date:** 2026-06-21
**Baseline:** Stable 1.2.1 (Post-Monolith Extraction + Security Decontamination)
**Paths:** Path 1 (Proprietary Consulting Backend) + Path 2 (ZA-DIVERGENT Subscription Bulletin)

---

## Sprint 1: The Premium Content Gate (Path 2 Enablement)

**Objective:** Enable tiered content access on ZA-DIVERGENT so free visitors see truncated signal summaries while subscribers access full intelligence briefs, entity dossiers, and the interactive evidence graph.

### 1.1 Article Tier Metadata

Add a `tier` column to the `wiki_articles` table:

```sql
ALTER TABLE wiki_articles ADD COLUMN tier TEXT NOT NULL DEFAULT 'free'
    CHECK(tier IN ('free', 'preview', 'premium'));
```

- `free` — full content visible to all visitors
- `preview` — first 300 chars visible, remainder behind paywall
- `premium` — title-only visible, full content requires subscription

### 1.2 Publisher Content Gate

Modify `tools/publish.py` to read the `tier` column. When rendering article pages:

- `free` → render full `content_html`
- `preview` → render truncated excerpt + `{% include "gate_banner.html" %}`
- `premium` → render title + metadata + `{% include "gate_full.html" %}`

Template files `publisher/templates/gate_banner.html` and `gate_full.html` already exist as stubs. Wire them to display the subscription CTA and payment link from `revenue/config.py`.

### 1.3 Subscription Flow

- The `revenue/config.py` module already provides `get_template_context()` with `membership_url`, `payment_checkout_url`, and `sponsor_slots`
- Wire `subscribe.html` to the payment processor endpoint (Stripe, Paystack, or Yoco for SA)
- Add a `subscribers` table to track email + tier + expiry
- The Flask admin panel gets a `/admin/subscribers` management view

### 1.4 Deliverables

| File | Change |
|---|---|
| `app.py` → `migrate_db()` | Add `tier` column to `wiki_articles` |
| `tools/publish.py` | Tier-aware content rendering |
| `publisher/templates/gate_banner.html` | Truncation paywall banner |
| `publisher/templates/gate_full.html` | Full premium gate overlay |
| `publisher/templates/subscribe.html` | Subscription checkout page |
| `revenue/config.py` | Payment processor integration keys |
| `core/web/blueprints/admin.py` | Subscriber management routes |

**Effort:** 8-12 hours
**Dependencies:** Payment processor account (Stripe/Paystack/Yoco)

---

## Sprint 2: Production Automation & Daemon Security

**Objective:** Transform the manual `python tools/mega_ingest.py` workflow into a reliable, self-monitoring production daemon that runs on a schedule and alerts on failure.

### 2.1 Scheduled Execution

Replace the manual `bin/*.bat` scripts with a cross-platform scheduler:

| Task | Schedule | Command |
|---|---|---|
| Full collection sweep | Every 6 hours | `python tools/mega_ingest.py --collect-only` |
| Decay engine | Every 6 hours | `python -c "from forage.engines.decay_engine import DecayEngine; DecayEngine().run()"` |
| Wiki compilation | Daily 02:00 | `python tools/init_wiki.py` |
| Publisher build | Daily 06:00 | `python tools/publish.py --deploy` |
| Database sanitization | Weekly Sunday 03:00 | `python tools/sanitize_db.py` |

Implementation options (pick one):
- **Windows Task Scheduler** — `.bat` wrappers already exist in `bin/`, register via `schtasks`
- **systemd timers** (Linux deploy) — create `.service` + `.timer` unit files
- **APScheduler** (in-process) — add `pip install apscheduler`, wire into `app.py` as a background thread

### 2.2 Pipeline Health Monitoring

Extend `tools/sanitize_db.py` or create `tools/health_check.py`:

```python
# Checks:
# 1. pipeline_runs: last successful run < 12 hours ago?
# 2. signals: any new signals in last 24 hours?
# 3. database.db: file size growing (not stuck)?
# 4. Collector registry: all 20 collectors importable?
#
# Output: JSON health report
# Alert: write to logs/health_alert.json if any check fails
# Optional: send email via smtplib to admin address
```

### 2.3 Collector Timeout Guard

Add per-collector timeout to the `mega_ingest.py` semaphore dispatcher:

```python
async def _run_one(label, mod):
    async with sem:
        try:
            return await asyncio.wait_for(_execute(mod), timeout=300)  # 5 min max
        except asyncio.TimeoutError:
            return (label, Exception(f"Collector {label} timed out after 300s"))
```

### 2.4 Deliverables

| File | Change |
|---|---|
| `bin/forge_scheduler.py` | Cross-platform task scheduler |
| `tools/health_check.py` | Pipeline health monitor with alerting |
| `tools/mega_ingest.py` | Per-collector timeout in dispatcher |
| `logs/health_alert.json` | Auto-generated alert output |

**Effort:** 6-8 hours
**Dependencies:** None (stdlib only)

---

## Sprint 3: The Dashboard Production Switch

**Objective:** Surface operational intelligence and administrative controls in the FORGE web UI so the platform is self-contained — no terminal required for routine operations.

### 3.1 One-Click Publish Endpoint

Add to `core/web/blueprints/control.py`:

```python
@control_bp.route("/api/admin/publish", methods=["POST"])
def api_admin_publish():
    """Trigger a ZA-DIVERGENT publish cycle from the admin panel."""
    # Guard: _PIPELINE_ACTIVE check
    # Spawn: tools/publish.py --deploy in background thread
    # Return: job_id for telemetry tracking
```

Wire a "Publish Now" button in `templates/admin.html` that hits this endpoint via HTMX.

### 3.2 Live System Health Dashboard

Add a health status card to the admin panel or create a dedicated `/status` page:

| Metric | Source | Display |
|---|---|---|
| Signal count | `SELECT COUNT(*) FROM signals` | Large number card |
| Active actors | `SELECT COUNT(*) FROM actors` | Number card |
| Cases open | `SELECT COUNT(*) FROM cases WHERE status='active'` | Number card |
| Last pipeline run | `SELECT MAX(run_at) FROM pipeline_runs` | Timestamp + age badge |
| DB integrity | `PRAGMA integrity_check` | Green/Red indicator |
| Collector fleet | `_COLLECTOR_REGISTRY` count | "20/20 healthy" badge |
| FMS modules | ConclaveContext status | "7/7 attached" badge |
| DB size | `os.path.getsize('database.db')` | "28.6 MB" display |

Render via a lightweight HTMX polling loop (refresh every 30 seconds) or a manual "Refresh" button.

### 3.3 Collector Control Panel Enhancement

Extend the existing Control Room (`/api/control/registry`) to show:
- Last run timestamp per collector (from `pipeline_runs`)
- Success/failure status badge
- "Run Now" button per collector (already exists via `/api/control/run_collector/<id>`)
- Signal count per source (from `signals` table `GROUP BY source`)

### 3.4 Deliverables

| File | Change |
|---|---|
| `core/web/blueprints/control.py` | `/api/admin/publish` endpoint |
| `core/web/blueprints/diagnostics.py` | `/status` health dashboard route |
| `templates/admin.html` | "Publish Now" button + health cards |
| `templates/status.html` | Live system health dashboard |
| `static/js/admin.js` | HTMX polling for health metrics |

**Effort:** 8-10 hours
**Dependencies:** None

---

## Timeline

| Sprint | Focus | Effort | Dependency |
|---|---|---|---|
| **Sprint 1** | Premium content gate + subscription | 8-12 hrs | Payment processor |
| **Sprint 2** | Automation + monitoring | 6-8 hrs | None |
| **Sprint 3** | Admin dashboard + publish UI | 8-10 hrs | None |

**Total estimated effort:** 22-30 hours across 3 sprints.

Sprints 2 and 3 can execute in parallel. Sprint 1 is the critical path — it directly enables revenue generation via the ZA-DIVERGENT subscription model.

---

## Post-Sprint: Due Diligence Readiness Checklist

- [ ] `requirements.txt` trimmed to production-only dependencies
- [ ] Dockerfile tested with clean build
- [ ] `README.md` rewritten for external audience (not internal dev notes)
- [ ] LICENSE file added (proprietary or dual-license)
- [ ] API documentation generated for consulting clients
- [ ] Database backup/restore procedure documented
- [ ] Runbook: "How to deploy FORGE on a new machine in 15 minutes"
