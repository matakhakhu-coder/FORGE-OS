# FORGE — Pipeline Contracts
### Classification: INTERNAL — ARCHITECTURE STANDARD
### Issued: Phase 32+ | FMS Reference Implementation
### Authority: Derived from live system failure analysis — signal_enrichment v1.0

---

> *These rules were not written in advance. They were extracted from the system's first real failure under load. That is the correct way to derive a contract.*

---

## PREAMBLE

This document defines the binding contracts that govern all Forge Native Modules (FNM). Every rule here was confirmed by a real failure observed during the first production ingestion cycle of the `signal_enrichment` module across 26,000+ signals.

These are not guidelines. They are contracts. A module that violates them will be rejected by the validator or produce undefined behaviour at scale.

---

## CONTRACT 1 — Hook Execution Rules

### Rule 1.1 — No imports inside hooks

Hooks are on the hot path. They execute once per signal. At 26,000 signals per ingest cycle, a single `import` statement inside a hook executes 26,000 times — each time hitting `sys.modules`, the filesystem, or both.

**FORBIDDEN:**
```python
def on_signal(signal):
    from forge_modules.my_module.engine import some_function  # ← VIOLATION
    some_function(signal)
```

**REQUIRED:**
```python
def register(conclave):
    from forge_modules.my_module.engine import run as engine_run  # ← import once

    def on_signal(signal):
        engine_run(signal)  # ← reference captured by closure

    conclave.register_hook("on_signal", on_signal)
```

**Why closure capture works:**
The function reference `engine_run` is resolved once at `register()` time and bound into the hook's closure. At hook-fire time, no resolution occurs — only the already-bound reference is called.

### Rule 1.2 — No dynamic resolution inside hooks

Hooks must not perform:
- `getattr()` lookups on modules
- `importlib` calls
- Filesystem reads
- Database queries
- Network calls

Hooks may perform:
- Function calls using pre-captured references
- Pure computation on the signal dict
- Logging at DEBUG level

### Rule 1.3 — Hook failures must be silent to the pipeline

The `fire_hook()` mechanism in `ConclaveContext` already wraps all hooks in try/except. Module authors must not assume hooks will always execute. A hook that raises an exception is logged and skipped — the signal continues processing normally.

Design hooks to be **additive and optional**. The pipeline must be correct with or without them.

### Rule 1.4 — Supported hook signatures

| Hook name | Signature | Fires |
|---|---|---|
| `on_signal` | `fn(signal: dict) -> None` | Before signal interpretation |
| `on_ingest` | `fn(signal: dict, result: dict) -> None` | After full ingestion pipeline |

Do not invent new hook names without extending the contract.

---

## CONTRACT 2 — Engine Interface Contract

### Rule 2.1 — One public function per engine

Each `engine.py` exposes exactly one public function:

```python
def run(signal: dict) -> AnalysisResult | None:
    ...
```

Everything else in `engine.py` is private (prefixed with `_`). No other file may import private engine functions.

**Rationale:** When `signal_enrichment` v1.0 renamed `_extract_sa_entities` to `_extract_entities`, `module.py` broke silently. The error only surfaced at runtime under load. A single public interface prevents this class of breakage entirely.

### Rule 2.2 — run() return contract

`run()` must return either:
- A valid `AnalysisResult` object
- `None` if the signal has no content relevant to this module

Returning `None` is the correct signal to the Conclave that this module has nothing to contribute. The Conclave merge skips `None` results cleanly.

Never return a zero-gravity `AnalysisResult` to signal "nothing found" — use `None`.

### Rule 2.3 — run() must not raise

All exceptions inside `run()` must be caught internally. If `run()` raises, the `run_conclave_with_modules()` function will catch it and log it — but this means the module contributed nothing and the error is silent to the analyst.

Catch your own exceptions. Return `None` on failure if the failure is expected (e.g. signal has no text). Log unexpected failures explicitly before returning `None`.

### Rule 2.4 — AnalysisResult provenance contract

The `provenance` dict must always include:
```python
{
    "module":  "<module_name>",
    "engine":  "<engine_name>",
}
```

Additional provenance keys are optional but recommended. The `entity_types` key is the established pattern for passing type metadata to `entity_engine`:
```python
{
    "entity_types": {"Entity Name": "type_string", ...}
}
```

---

## CONTRACT 3 — Import Discipline

### Rule 3.1 — Import hierarchy

```
module.py   imports from → engine.py (public run() only)
engine.py   imports from → core.conclave.registry (AnalysisResult)
engine.py   imports from → stdlib only (re, json, etc.)
engine.py   NEVER imports from → core.db, app, forage.*
```

Modules are sandboxed. They produce `AnalysisResult` objects. They do not touch the database, the Flask app, or other pipeline components directly.

### Rule 3.2 — register() is the import boundary

All imports that the module needs must occur inside `register()`. This means:
- If `register()` fails, the module is rejected cleanly
- If `register()` succeeds, all references are bound and stable
- No import can fail at hook-fire time

```python
def register(conclave):
    # All imports here — this is the only place
    from forge_modules.my_module.engine import run as engine_run
    from forge_modules.my_module.validators import validate  # if needed

    def on_signal(signal):
        engine_run(signal)  # no imports here

    conclave.register_engine("my_engine", engine_run)
    conclave.register_hook("on_signal", on_signal)
```

### Rule 3.3 — No circular imports

Modules must not import from:
- Other modules in `forge_modules/`
- `core.fms.*`
- `core.conclave.context`

If two modules need shared logic, extract it to a shared utility in `forge_modules/_shared/` and import from there.

---

## CONTRACT 4 — Failure Isolation Guarantees

These are guarantees made by the FMS infrastructure — not by individual modules.

### Guarantee 4.1 — Module load failure is isolated

If a module fails to load (invalid manifest, import error, contract violation), FORGE continues normally. Other modules are unaffected.

### Guarantee 4.2 — Hook failure is isolated

If a hook raises an exception, `fire_hook()` catches it, logs it, and continues. The signal proceeds through the pipeline as if the hook did not exist.

### Guarantee 4.3 — Engine failure is isolated

If `run()` raises an exception, `run_conclave_with_modules()` catches it, logs it, and excludes that module's result from the merge. The Conclave runs on the remaining results.

### Guarantee 4.4 — Detach is safe

`detach_module()` removes a module's engines and hooks without affecting other modules or the core pipeline. A detached module remains READY and can be re-attached.

### Guarantee 4.5 — The pipeline is correct without any modules

If `forge_modules/` is empty, deleted, or entirely invalid, FORGE runs exactly as it did before FMS existed. Modules are additive. They are never load-bearing.

---

## CONTRACT 5 — Module Manifest Contract

Every module must have a `manifest.json` with these required fields:

```json
{
    "name":     "my_module",
    "version":  "1.0.0",
    "engines":  ["my_engine_name"]
}
```

Optional fields:
```json
{
    "description": "Human-readable description",
    "author":      "Author name",
    "hooks":       ["on_signal"],
    "routes":      [],
    "schema":      null
}
```

### Naming rules
- `name` must match the directory name in `forge_modules/`
- `name` must be alphanumeric with underscores or hyphens only
- `engines` entries must exactly match the names passed to `register_engine()`
- Version follows semver: `MAJOR.MINOR.PATCH`

---

## REFERENCE IMPLEMENTATION

`forge_modules/signal_enrichment/` is the canonical reference implementation of all rules above. When in doubt, read it.

**Checklist for new modules:**

```
□ manifest.json has name, version, engines
□ module.py has def register(conclave) and nothing else at module level
□ All imports are inside register()
□ Only run() is imported from engine.py
□ Hooks capture references via closure — no imports inside hooks
□ engine.py exposes only def run(signal) publicly
□ run() returns AnalysisResult | None — never raises
□ provenance includes module and engine keys
□ entity_types included in provenance if actors are produced
□ No DB access in engine.py
□ No imports from other forge_modules
```

---

## FAILURE LOG — Lessons Encoded Here

| Failure | Rule derived |
|---|---|
| `_extract_sa_entities` renamed → `module.py` broke silently at runtime | Rule 2.1 — one public function only |
| Import inside `on_signal` → 26,000 import calls per run, sys.modules cache misses | Rule 1.1 — no imports inside hooks |
| `confidence = 0.5` hardcoded → all actors created with identical weight | Rule 2.2 — run() return must be meaningful |
| `location_ZA` leaked as actor name → polluted actor registry | Rule 2.4 — provenance carries entity_types |
| `str(actor_ids)` not `json.dumps` → conclave_meta silently corrupted | Not a module rule — pipeline contract |

---

*Document derived from live production failure analysis — FORGE Phase 32+*
*First failure cycle: signal_enrichment v1.0, 26,026 signals, 2026-03-20*
