"""Standing publication authorization: the owner's advance approval for pins that
pass fixed machine checks, so an unattended run can publish without the owner
reviewing each pin.

What it is and is not
- It is the owner's own decision, recorded in docs/approvals/standing/<id>.json
  with the owner's words, when it was given, and when it ends.
- A pin published under it gets an ordinary approval record in
  docs/approvals/pins with basis "standing". The record says plainly that the
  owner did not read that pin's text: the pin was checked by machine against the
  authorization's conditions, and by the run that wrote it against
  docs/SOURCING_POLICY.md.
- It never widens any other rule. The build still runs every check in
  checked_signals (provenance, precision, dates) and the ledger checks in
  approvals.py. This adds conditions; it removes none.

Fail closed
A standing record is refused (a ledger violation, so imports and publishes
refuse) when the authorization file is missing or malformed, when it has no end
date (expires_at null: the owner has not set one, so it is not active), when the
record was approved before the grant, at or after expiry, or at or after a
revocation, when the record is a material update (those always need the owner),
or when the pin fails a machine condition.

Revoke by setting revoked_at (and revocation_reason) in the authorization file;
extend by writing a new authorization file. Records approved while it was
active stay valid; nothing new can be approved under it.
"""
from __future__ import annotations

import json
import pathlib
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

ROOT = pathlib.Path(__file__).resolve().parents[1]
STANDING_DIR = ROOT / "docs" / "approvals" / "standing"
SCHEMA = "forge.standing_publication_authorization.v1"
ID_RE = re.compile(r"^standing-[a-z0-9-]{3,60}$")
FIELDS = ("schema", "authorization_id", "owner", "granted_at", "granted_via", "owner_words",
          "expires_at", "revoked_at", "revocation_reason", "review", "scope", "conditions")
SCOPE_FIELDS = ("streams", "actions", "material_updates", "sourcing_policy")
CONDITION_FIELDS = ("min_independent_source_domains",)
KNOWN_STREAMS = ("CRIME_INTEL", "INFRASTRUCTURE", "PRIORITY", "GLOBAL")
REVIEW_TEXT = "machine"


class StandingError(ValueError):
    pass


def _iso(v):
    if not isinstance(v, str):
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else None


def validate(auth) -> list[str]:
    errs: list[str] = []
    if not isinstance(auth, dict) or tuple(sorted(auth)) != tuple(sorted(FIELDS)):
        return [f"authorization must have exactly the fields {', '.join(FIELDS)}"]
    if auth["schema"] != SCHEMA:
        errs.append(f"schema must be {SCHEMA}")
    if not isinstance(auth["authorization_id"], str) or not ID_RE.match(auth["authorization_id"]):
        errs.append("authorization_id must look like standing-<words>")
    for k in ("owner", "granted_via", "owner_words"):
        if not isinstance(auth[k], str) or not auth[k].strip():
            errs.append(f"{k} must be non-empty text")
    granted = _iso(auth["granted_at"])
    if granted is None:
        errs.append("granted_at must be a datetime with zone")
    if auth["expires_at"] is not None:
        exp = _iso(auth["expires_at"])
        if exp is None:
            errs.append("expires_at must be null (not active) or a datetime with zone")
        elif granted and exp <= granted:
            errs.append("expires_at must be after granted_at")
    if auth["revoked_at"] is not None:
        if _iso(auth["revoked_at"]) is None:
            errs.append("revoked_at must be null or a datetime with zone")
        if not isinstance(auth["revocation_reason"], str) or not auth["revocation_reason"].strip():
            errs.append("a revocation needs revocation_reason")
    if auth["review"] != REVIEW_TEXT:
        errs.append("review must be 'machine': the owner does not read each pin under a standing authorization")
    sc = auth["scope"]
    if not isinstance(sc, dict) or tuple(sorted(sc)) != tuple(sorted(SCOPE_FIELDS)):
        errs.append(f"scope must have exactly {', '.join(SCOPE_FIELDS)}")
    else:
        if not isinstance(sc["streams"], list) or not sc["streams"] or \
                any(s not in KNOWN_STREAMS for s in sc["streams"]):
            errs.append(f"scope.streams must list streams from {KNOWN_STREAMS}")
        if not isinstance(sc["actions"], list) or not sc["actions"] or \
                any(a not in ("approve", "supersede") for a in sc["actions"]):
            errs.append("scope.actions may hold only approve and supersede")
        if sc["material_updates"] is not False:
            errs.append("scope.material_updates must be false: material updates always need the owner")
        if sc["sourcing_policy"] != "docs/SOURCING_POLICY.md":
            errs.append("scope.sourcing_policy must be docs/SOURCING_POLICY.md")
    cond = auth["conditions"]
    if not isinstance(cond, dict) or tuple(sorted(cond)) != tuple(sorted(CONDITION_FIELDS)):
        errs.append(f"conditions must have exactly {', '.join(CONDITION_FIELDS)}")
    elif not isinstance(cond["min_independent_source_domains"], int) or \
            cond["min_independent_source_domains"] < 2:
        errs.append("conditions.min_independent_source_domains must be a whole number of at least 2")
    return errs


def load(auth_id: str, standing_dir: pathlib.Path | None = None) -> dict:
    d = pathlib.Path(standing_dir if standing_dir is not None else STANDING_DIR)
    if not isinstance(auth_id, str) or not ID_RE.match(auth_id):
        raise StandingError(f"{auth_id!r} is not a standing authorization id")
    path = d / f"{auth_id}.json"
    if not path.is_file():
        raise StandingError(f"no standing authorization file {path.name}")
    try:
        auth = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StandingError(f"{path.name} is unreadable: {exc}") from exc
    errs = validate(auth)
    if not errs and auth["authorization_id"] != auth_id:
        errs.append("authorization_id does not match the file name")
    if errs:
        raise StandingError(f"{path.name}: " + "; ".join(errs))
    return auth


def inactive_reason(auth: dict, when: datetime) -> str | None:
    """None when the authorization covers an approval made at `when`; else why not."""
    if auth["expires_at"] is None:
        return "it has no end date yet (expires_at is null), so it is not active"
    if when < _iso(auth["granted_at"]):
        return "the approval is earlier than the grant"
    if when >= _iso(auth["expires_at"]):
        return f"it expired at {auth['expires_at']}"
    if auth["revoked_at"] is not None and when >= _iso(auth["revoked_at"]):
        return f"it was revoked at {auth['revoked_at']}"
    return None


def source_domain(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    parts = host.split(".")
    # co.za, org.za, gov.za and the like: keep three labels so sabcnews.com and
    # iol.co.za stay apart while news.iol.co.za and iol.co.za count as one site.
    if len(parts) >= 3 and parts[-2] in ("co", "org", "gov", "ac", "net", "com"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def pin_problems(auth: dict, pin: dict) -> list[str]:
    """Machine conditions on the pin itself."""
    out = []
    if pin.get("stream") not in auth["scope"]["streams"]:
        out.append(f"stream {pin.get('stream')!r} is outside the authorization")
    checked = (pin.get("metadata") or {}).get("checked") or {}
    urls = [s.get("url") for s in checked.get("sources") or [] if isinstance(s, dict) and s.get("url")]
    domains = {source_domain(u) for u in urls}
    need = auth["conditions"]["min_independent_source_domains"]
    if len(domains) < need:
        out.append(f"{len(domains)} source site(s) ({', '.join(sorted(domains)) or 'none'}); "
                   f"the authorization needs at least {need} different sites")
    if not str(checked.get("method") or "").strip():
        out.append("the pin does not say how it was checked (metadata.checked.method)")
    return out


def record_problems(rec: dict, pin: dict, standing_dir: pathlib.Path | None = None) -> list[str]:
    """Why a basis=standing record does not approve its pin (empty list: it does)."""
    try:
        auth = load(rec["approval_reference"]["id"], standing_dir)
    except StandingError as exc:
        return [str(exc)]
    out = []
    when = _iso(rec.get("approved_at"))
    if when is None:
        out.append("approved_at is missing")
    else:
        why = inactive_reason(auth, when)
        if why:
            out.append(f"{auth['authorization_id']} does not cover it: {why}")
    if rec["action"] not in auth["scope"]["actions"]:
        out.append(f"action {rec['action']!r} is outside the authorization")
    if rec.get("material") is True:
        out.append("a material update always needs the owner's own approval")
    if rec.get("owner") != auth["owner"]:
        out.append("record owner differs from the authorization owner")
    return out + pin_problems(auth, pin)


def active_now(auth_id: str, standing_dir: pathlib.Path | None = None,
               now: datetime | None = None) -> tuple[dict | None, str | None]:
    """(authorization, None) when it is active now; (auth or None, reason) when not."""
    try:
        auth = load(auth_id, standing_dir)
    except StandingError as exc:
        return None, str(exc)
    return auth, inactive_reason(auth, now or datetime.now(timezone.utc))
