# FORGE Licensing Architecture & Policy

**Version 1.3.0 — Hybrid Open-Core Model**

FORGE-OS utilizes a dual-license single-repository topology designed to foster open-source community collaboration on core ingestion infrastructure while strictly protecting advanced analytical intellectual property.

---

## 1. The Core Framework (Open Source — AGPLv3)

The underlying platform orchestration layer, user interface templates, data-pipeline mechanics, and all network collectors are licensed under the **GNU Affero General Public License v3 (AGPLv3)**.

### Covered components

```
app.py                          Flask application factory + schema
core/pipeline/                  Signal ingestion orchestrator
core/web/                       Blueprint architecture + shared helpers
core/conclave/                  FMS hook registry + engine runner
core/fms/                       Module discovery, validation, activation
core/db/                        Connection factory + FK enforcement
core/gravity.py                 CT-1 Contextual Tunneling scorer
forage/collectors/ (all 23)     OSINT signal collectors
forage/engines/                 Gravity, decay, escalation, correlation, cluster
flux/collectors/                SOCINT collectors (X/Twitter)
templates/                      Jinja2 UI templates
static/                         CSS, JavaScript, map tiles
tools/mega_ingest.py            Pipeline runner
tools/publish.py                Static site generator
```

### What this means for users

- You are free to run, study, modify, and distribute the core engine.
- **The network trigger:** If you modify the core framework and run it as a network service (SaaS, hosted platform, API service), you must make your modified source code publicly available under the AGPLv3.
- You may use FORGE internally within your organization without disclosure obligations — the AGPLv3 network clause only activates when you provide the software's functionality to external users over a network.

---

## 2. Advanced Analytical Capabilities (Commercial / Proprietary)

All directories within the `forge_modules/` architecture tree, alongside specialized processing components listed below, are explicitly excluded from the AGPLv3 license terms.

### Proprietary components

| Component | Location | Capability |
|---|---|---|
| Coalition Detector | `forge_modules/coalition_detector/` | Co-occurrence actor grouping algorithms |
| Counterintelligence Engine | `forge_modules/counterintel/` | Narrative clustering, bot detection, campaign fingerprinting |
| Emergence Engine | `forge_modules/emergence_engine/` | Network growth rate tracking + predictive flagging |
| FLUX SOCINT Bridge | `forge_modules/flux/` | Stylometric analysis pipeline integration |
| Hybrid Fuzzy Resolver | `forage/processors/entity_resolver.py` (Tier 3) | Jaro-Winkler fuzzy matching with surname+initial boost |
| Revenue & Subscription | `revenue/` | Payment processor integration + tier gating |
| Publisher Premium Gate | `publisher/` (gate templates) | Subscription paywall rendering |

### Legal status

These components are **proprietary software**. Copyright 2026 Matamela Ramovha. All rights reserved.

No permission is granted to copy, distribute, modify, sublicense, or execute these modules for commercial use, external deployment, or multi-tenant SaaS execution without acquiring a formal Commercial License directly from the copyright holder.

### What this means for users

- The core FORGE framework operates fully without these modules — they are optional analytical extensions.
- The FMS loader gracefully skips missing or unlicensed modules at startup.
- To access these capabilities, contact the copyright holder for commercial licensing terms.

---

## 3. The Boundary Mechanism

FORGE's modular architecture (the Forge Module System) provides a clean technical boundary between open and proprietary components:

- **Open components** are loaded directly by `app.py` and the blueprint system.
- **Proprietary modules** are loaded via `core/fms/loader.py`, which scans `forge_modules/` for `manifest.json` files. If a module directory is absent, the system logs a skip and continues without error.
- The entity resolver's Tier 3 (fuzzy matching) is a self-contained fallback within a single function. Removing it leaves Tiers 1 and 2 (exact + normalized lookup) fully operational.

This means the public repository compiles, boots, and operates without any proprietary components present.

---

## 4. Enterprise & Compliance Inquiries

Organizations requiring:
- A commercial waiver to bypass AGPLv3 source-disclosure obligations
- Deployment of proprietary analytical modules in production environments
- Custom integration, training, or consulting services
- White-label licensing for resale or embedded distribution

Contact: **matamelaramovha8@gmail.com**

---

## 5. Contributor License

By submitting code contributions (pull requests) to this repository, you agree that your contributions are licensed under the AGPLv3 for core framework components. Contributions to files within `forge_modules/` are not accepted without a separate Contributor License Agreement (CLA).
