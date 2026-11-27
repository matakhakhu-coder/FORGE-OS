# Standing publication authorizations

A file here is the owner's advance approval for new pins that pass fixed machine checks, so `tools/autopublish.py` can publish them without the owner reading each one. The code that enforces it is `tools/standing_auth.py`; the ledger check is in `tools/approvals.py` (basis `standing`).

- **Who decides:** only the owner. Each file quotes the owner's words, when they were given, and the end date the owner chose. No end date (`expires_at: null`) means the file is not active.
- **What it covers:** version-1 approvals of new pins in the listed streams, with at least `min_independent_source_domains` different source sites, checked against `docs/SOURCING_POLICY.md`. It never covers a change to a published pin or a material update; those need the owner's own approval.
- **What the owner did not do:** read the pin. Every record written under it says "Owner did not review this pin's text."
- **Fails closed:** a missing or malformed file, a record dated before the grant, at or after expiry, or at or after revocation, or a pin that fails a check is a ledger violation, and imports and publishes refuse.
- **Revoke:** set `revoked_at` and `revocation_reason`. **Extend:** write a new file with the owner's new decision. Records approved while a file was active stay valid.

| File | Granted | Ends | State |
|---|---|---|---|
| standing-autopub-test-2026-11-21.json | 21 Nov 2026 16:40 SAST | 21 Nov 2026 23:59 SAST | owner's test of an automated run |
| standing-autopub-2026-11-26.json | 26 Nov 2026 10:08 SAST | 26 Nov 2026 23:59 SAST | owner's renewal for the end-to-end test of the publish-order fix |
| standing-autopub-2026-11-27.json | 27 Nov 2026 06:26 SAST | 27 Nov 2026 23:59 SAST | owner's renewal for the province-by-province sweeps of 1 to 27 Nov |
