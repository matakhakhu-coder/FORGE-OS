# FORGE Operator Task Queue

> Cognitive document tracking pending actions, uncommitted work, and deferred tasks.
> Updated after every sprint. Check this before starting a new session.

**Last updated:** 2026-06-21

---

## Uncommitted changes

- Sprint 1 content gate (app.py tier migration, publish.py tier rendering, article.html gate banner) — **not yet committed**

## Pending operator actions

- [ ] Gate test articles to verify paywall rendering:
  ```sql
  UPDATE articles SET tier = 'preview' WHERE slug = 'beitbridge-explosives-smuggling-maroto';
  UPDATE articles SET tier = 'premium' WHERE slug = 'graft-roundup-joshco-home-affairs-june-2026';
  ```
  Then run `python tools/publish.py --deploy` and check Vercel site.

- [ ] Set up Paystack/Yoco payment processor keys in `.env`:
  ```
  FORGE_PAYSTACK_PUBLIC_KEY=pk_live_...
  ```

- [ ] Set `FORGE_REVENUE_LIVE=true` in `.env` when payment processor is ready

- [ ] Send email to `access@acleddata.com` to request API data access for ACLED collector

- [ ] Set `VERCEL_DEPLOY_HOOK_URL` in `.env` for instant deploy triggers

- [ ] Configure `NDBC_STATIONS` in `.env` if marine buoy monitoring is needed

## Known network-gated collectors

| Collector | Issue | Workaround |
|---|---|---|
| `interpol_red_notices` | GeoIP blocked from SA (403) | Run from VPN with non-ZA exit |
| `court_rolls_predictive` | judiciary.org.za returning 404 on all divisions | Site restructured — needs URL update |
| `treasury_tenders` | etenders.gov.za returning 404 (JS-rendered SPA) | Needs Selenium/Playwright or alt endpoint |
| `sanctions_sa_fic` | FIC XML endpoint returning empty data | Endpoint may have moved — needs reconnaissance |

## Sprint completion log

| Sprint | Status | Date | Commit |
|---|---|---|---|
| Sprint 1: Content Gate | Done | 2026-06-21 | Pending commit |
| Sprint 2: Automation | Done | 2026-06-21 | Pending commit |
| Sprint 3: Dashboard | Not started | — | — |

## Deferred improvements

- [ ] Nitter instance pool refresh — 5 of 6 instances dead/rate-limited
- [ ] `requirements.txt` trim — remove unused packages (streamlit, pygame, psycopg2, fastapi)
- [ ] Dockerfile tested with clean build
- [ ] Runbook: "Deploy FORGE on a new machine in 15 minutes"
