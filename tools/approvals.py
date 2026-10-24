"""
Pin approval ledger: who approved which exact pin data, kept in Git.

    python tools/approvals.py verify            check the ledger against docs/signals
    python tools/approvals.py verify --enforce  also refuse pins with no approval record
    python tools/approvals.py payload-hash <signal-id>   hash to put in a new record

Layout (docs/approvals/pins/)
-----------------------------
    README.md                      how it works (editable)
    policy.json                    {"enforce": false|true} (editable, reviewed)
    schema/pin-approval.v1.json    the record format (append-only)
    _examples/example.json         a sample record; the validator skips it
    <signal-id>/<approval-id>.json one record per approval (append-only)

approval-id is YYYYMMDDTHHMMSSZ-<8 lowercase hex>, in UTC.

What verify checks
------------------
Every record: the exact field set of its schema (v1, or v2 = v1 plus
"material"), types, IDs that match the folder and
file name, scope "data-only", and payload_sha256 equal to the canonical hash of
approved_payload (the same hash as the pin's fingerprint in docs/signals:
sorted keys, UTF-8, no spaces, over the fingerprinted fields).

Each pin's records form one chain: version 1 is an "approve" with no
supersedes; every later record names the record before it and raises
pin_version by one. "supersede" and "revoke" follow a live approval; a fresh
"approve" may only follow a revoke. The current approval is the last record.

Material updates (time-aware pins, Batch 2, schema v2): an approval with
"material": true is a supersede that sets or changes the pin's
last_material_update_at, update_note and update_sources (metadata.checked),
and it may not change incident_start or published_at. Every other record (v1
records, and v2 with "material": false: first approvals, corrections,
wording fixes, revokes) must leave those three fields exactly as the
previous approved version had them, so a correction or a retraction keeps
its own wording and is never shown as an update. No material update may be
approved after the pin was retracted. A routine check needs no approval at
all: it writes only last_checked_at in docs/signals/pin-clocks.json
(tools/pin_clocks.py).

Against docs/signals: every active pin needs a current, unrevoked approval
whose payload_sha256 and batch_file match the pin as it is now. A revoked pin
must be retracted or removed. A record for a pin that is not in any batch is
flagged as a warning.

Append-only: compared with origin/main (and across the history of HEAD), no
record or schema file under docs/approvals/pins may be changed, deleted or
renamed. README.md and policy.json may be edited. With no GitHub checks on
this repo, this runs only where the tools run; a direct push can skip it.

Findings and rollout
--------------------
    violation  something wrong with records that exist (bad field, broken
               chain, hash mismatch, revoked pin still live, edited record,
               or a current record labelled draft / provisional / "not an
               approval", which leaves its pin unapproved).
               Always refuses an import --write or a publish.
    gap        a pin with no approval record, or a check that could not run
               (no git, no origin/main). Refuses only when enforcement is on.
    warning    reported, never refuses (a record for a pin not in any batch).

Enforcement is on when policy.json says "enforce": true, or when the
environment sets FORGE_APPROVALS_ENFORCE=1. The environment can only turn it
on, never off. It stays off until the legacy records for the existing pins are
committed, so current imports and publishes keep working and print the gap.
An approval covers data only: it never authorises a build, publish or deploy.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import checked_signals as cs_mod  # noqa: E402

APPROVALS_DIR = ROOT / "docs" / "approvals" / "pins"
POLICY_NAME = "policy.json"
SCHEMA_DIR = "schema"
EXAMPLES_DIR = "_examples"
EDITABLE_FILES = {"README.md", POLICY_NAME}
SKIP_DIRS = {SCHEMA_DIR, EXAMPLES_DIR}
DEFAULT_BASE = "origin/main"
ENFORCE_ENV = "FORGE_APPROVALS_ENFORCE"

RECORD_SCHEMA = "forge.pin_approval.v1"
RECORD_SCHEMA_V2 = "forge.pin_approval.v2"     # v1 plus "material" (Batch 2)
RECORD_SCHEMAS = (RECORD_SCHEMA, RECORD_SCHEMA_V2)
POLICY_SCHEMA = "forge.pin_approvals.policy"
ACTIONS = ("approve", "supersede", "revoke")
BASES = ("explicit", "legacy")
SCOPE = "data-only"

RECORD_FIELDS = (
    "schema", "approval_id", "signal_id", "action", "pin_version", "supersedes",
    "batch_file", "approved_payload", "payload_sha256", "source_hashes",
    "diff_from_previous", "owner", "approved_at", "recorded_at", "recorded_by",
    "approval_reference", "basis", "scope",
)
RECORD_FIELDS_V2 = RECORD_FIELDS + ("material",)


def record_fields(rec) -> tuple[str, ...]:
    """The exact field set a record's schema calls for."""
    return RECORD_FIELDS_V2 if isinstance(rec, dict) and rec.get("schema") == RECORD_SCHEMA_V2 \
        else RECORD_FIELDS


def is_material(rec) -> bool:
    """True for an approved material update (v2 with "material": true). v1 records never are."""
    return isinstance(rec, dict) and rec.get("schema") == RECORD_SCHEMA_V2 and rec.get("material") is True

APPROVAL_ID_RE = re.compile(r"^(\d{8}T\d{6}Z)-[0-9a-f]{8}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
SHA_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
REF_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
BATCH_FILE_RE = re.compile(r"^[A-Za-z0-9._-]+\.json$")
# A record whose approval_reference says it is a draft, provisional, or "not an
# approval" is a placeholder, not the owner's approval. v1 has no status field
# (unknown fields are already invalid), so the label is the only marker.
DRAFT_RE = re.compile(r"\bdraft\b|\bprovisional\b|\bnot an approval\b", re.IGNORECASE)


class ApprovalError(Exception):
    """The ledger could not be checked at all."""


@dataclass
class Finding:
    kind: str          # violation | gap | warning
    code: str
    message: str
    signal_id: str | None = None

    def line(self) -> str:
        who = f" {self.signal_id}" if self.signal_id else ""
        return f"[approvals] {self.kind} {self.code}{who}: {self.message}"


@dataclass
class Report:
    enforce: bool
    findings: list[Finding] = field(default_factory=list)
    records: int = 0
    pins_checked: int = 0
    approved_pins: int = 0

    def _of(self, kind: str) -> list[Finding]:
        return [f for f in self.findings if f.kind == kind]

    @property
    def violations(self) -> list[Finding]:
        return self._of("violation")

    @property
    def gaps(self) -> list[Finding]:
        return self._of("gap")

    @property
    def warnings(self) -> list[Finding]:
        return self._of("warning")

    @property
    def ok(self) -> bool:
        return not self.violations and not (self.enforce and self.gaps)


# ── Hashing ─────────────────────────────────────────────────────────────────────────

def payload_sha256(pin: dict) -> str:
    """Canonical hash of a pin's data: the docs/signals fingerprint (sorted keys,
    UTF-8, no spaces, over the fingerprinted fields)."""
    return cs_mod.fingerprint(pin)


def approved_payload(pin: dict) -> dict:
    """The part of a batch pin an approval records (the fingerprinted fields)."""
    return json.loads(cs_mod.canonical(pin))


# ── Policy ──────────────────────────────────────────────────────────────────────────

def load_policy(approvals_dir: pathlib.Path, findings: list[Finding]) -> bool:
    """policy.json's enforce flag. A missing or broken policy is a violation and
    reads as enforcing, so tampering cannot quietly switch enforcement off."""
    path = approvals_dir / POLICY_NAME
    if not approvals_dir.is_dir():
        findings.append(Finding("gap", "ledger-missing",
                                f"{approvals_dir} does not exist; no approvals can be checked"))
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        findings.append(Finding("violation", "policy-missing", f"{path} is missing"))
        return True
    except (OSError, ValueError) as exc:
        findings.append(Finding("violation", "policy-invalid", f"{path}: {exc}"))
        return True
    if not isinstance(data, dict) or data.get("schema") != POLICY_SCHEMA \
            or data.get("version") != 1 or not isinstance(data.get("enforce"), bool):
        findings.append(Finding("violation", "policy-invalid",
                                f"{path} must be {{schema: {POLICY_SCHEMA}, version: 1, enforce: bool}}"))
        return True
    return data["enforce"]


def enforcement(approvals_dir: pathlib.Path, findings: list[Finding],
                enforce: bool | None = None) -> bool:
    policy = load_policy(approvals_dir, findings)
    env_on = os.environ.get(ENFORCE_ENV, "").strip() == "1"
    return bool(policy or env_on or enforce)


# ── Records ────────────────────────────────────────────────────────────────────────

def _iso_tz(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else None


def _approval_time(approval_id: str) -> datetime | None:
    m = APPROVAL_ID_RE.match(approval_id or "")
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ")
    except ValueError:
        return None


def validate_record(rec, *, signal_id: str | None = None,
                    approval_id: str | None = None) -> list[str]:
    """Field-level problems with one record (empty list = valid)."""
    if not isinstance(rec, dict):
        return ["record is not a JSON object"]
    errs: list[str] = []
    keys = set(rec)
    fields = record_fields(rec)
    missing = [k for k in fields if k not in keys]
    extra = sorted(keys - set(fields))
    if missing:
        errs.append("missing fields: " + ", ".join(missing))
    if extra:
        errs.append("unknown fields: " + ", ".join(extra))
    if missing:
        return errs

    if rec["schema"] not in RECORD_SCHEMAS:
        errs.append(f"schema must be one of {', '.join(RECORD_SCHEMAS)}")
    elif rec["schema"] == RECORD_SCHEMA_V2:
        if not isinstance(rec["material"], bool):
            errs.append("material must be true or false")
        elif rec["material"] and rec["action"] != "supersede":
            errs.append("material: true needs action supersede: a material update is a new "
                        "version of a live pin (never a first approval or a revoke)")
    aid = rec["approval_id"]
    if not isinstance(aid, str) or _approval_time(aid) is None:
        errs.append("approval_id must be YYYYMMDDTHHMMSSZ-<8 hex> with a real UTC time")
    elif approval_id is not None and aid != approval_id:
        errs.append(f"approval_id {aid} does not match its file name {approval_id}.json")
    sid = rec["signal_id"]
    if not isinstance(sid, str) or not UUID_RE.match(sid):
        errs.append("signal_id must be a lowercase 36-character UUID")
    elif signal_id is not None and sid != signal_id:
        errs.append(f"signal_id {sid} does not match its folder {signal_id}")

    action = rec["action"]
    if action not in ACTIONS:
        errs.append("action must be one of " + ", ".join(ACTIONS))
    v = rec["pin_version"]
    if not isinstance(v, int) or isinstance(v, bool) or v < 1:
        errs.append("pin_version must be an integer >= 1")
    sup = rec["supersedes"]
    if sup is not None and (not isinstance(sup, str) or not APPROVAL_ID_RE.match(sup)):
        errs.append("supersedes must be null or an approval_id")

    carries_data = action in ("approve", "supersede")
    payload, digest, batch = rec["approved_payload"], rec["payload_sha256"], rec["batch_file"]
    if carries_data:
        if not isinstance(batch, str) or not BATCH_FILE_RE.match(batch):
            errs.append("batch_file must be a docs/signals batch file name (no folders)")
        payload_ok = False
        if not isinstance(payload, dict):
            errs.append("approved_payload must be the pin's fingerprinted fields")
        elif not set(payload) <= set(cs_mod.FIELDS) or not {"signal_id", "lat", "lng"} <= set(payload):
            errs.append("approved_payload may hold only the fingerprinted fields ("
                        + ", ".join(cs_mod.FIELDS) + ") and needs signal_id, lat and lng")
        elif isinstance(sid, str) and payload.get("signal_id") != sid:
            errs.append("approved_payload.signal_id does not match signal_id")
        else:
            try:
                payload_ok = approved_payload(payload) == payload
            except (TypeError, ValueError):
                payload_ok = False
            if not payload_ok:
                errs.append("approved_payload is not in canonical form (lat and lng as numbers)")
        if not isinstance(digest, str) or not SHA_RE.match(digest):
            errs.append("payload_sha256 must be sha256:<64 hex>")
        elif payload_ok and payload_sha256(payload) != digest:
            errs.append("payload_sha256 does not match approved_payload")
    elif action == "revoke":
        if batch is not None or payload is not None or digest is not None:
            errs.append("a revoke carries no batch_file, approved_payload or payload_sha256")

    sources = rec["source_hashes"]
    if not isinstance(sources, list):
        errs.append("source_hashes must be a list")
    else:
        if carries_data and not sources:
            errs.append("source_hashes must list the pin's sources")
        for i, s in enumerate(sources):
            if not isinstance(s, dict) or set(s) != {"url", "retrieved_at", "content_sha256"}:
                errs.append(f"source_hashes[{i}] must be {{url, retrieved_at, content_sha256}}")
                continue
            if not isinstance(s["url"], str) or not s["url"].startswith(("http://", "https://")):
                errs.append(f"source_hashes[{i}].url must be an http(s) URL")
            if s["retrieved_at"] is not None and _iso_tz(s["retrieved_at"]) is None:
                errs.append(f"source_hashes[{i}].retrieved_at must be null or a datetime with zone")
            if s["content_sha256"] is not None and (not isinstance(s["content_sha256"], str)
                                                    or not SHA_RE.match(s["content_sha256"])):
                errs.append(f"source_hashes[{i}].content_sha256 must be null or sha256:<64 hex>")

    diff = rec["diff_from_previous"]
    if diff is not None and not isinstance(diff, (dict, list)):
        errs.append("diff_from_previous must be null, an object or a list")
    if v == 1 and diff is not None:
        errs.append("diff_from_previous must be null on version 1")
    if action == "supersede" and diff is None:
        errs.append("a supersede must record diff_from_previous")

    owner = rec["owner"]
    if not isinstance(owner, str) or not owner.strip() or len(owner) > 64 or "@" in owner:
        errs.append("owner must be a pseudonym (1-64 characters, no email address)")
    basis = rec["basis"]
    if basis not in BASES:
        errs.append("basis must be explicit or legacy")
    if basis == "legacy" and (action != "approve" or v != 1):
        errs.append("a legacy record can only be the first approval (approve, version 1)")
    approved_at = rec["approved_at"]
    if approved_at is None:
        if basis != "legacy":
            errs.append("approved_at may be null only on a legacy record (no date is invented)")
    elif _iso_tz(approved_at) is None:
        errs.append("approved_at must be null or a datetime with zone")
    recorded_at = _iso_tz(rec["recorded_at"])
    if recorded_at is None:
        errs.append("recorded_at must be a datetime with zone")
    elif approved_at is not None and _iso_tz(approved_at) and _iso_tz(approved_at) > recorded_at:
        errs.append("approved_at is after recorded_at")
    if not isinstance(rec["recorded_by"], str) or not rec["recorded_by"].strip():
        errs.append("recorded_by must be set")

    ref = rec["approval_reference"]
    if not isinstance(ref, dict) or set(ref) != {"id", "summary"}:
        errs.append("approval_reference must be {id, summary}")
    else:
        if not isinstance(ref["id"], str) or not REF_ID_RE.match(ref["id"]):
            errs.append("approval_reference.id must be a short opaque ID")
        summary = ref["summary"]
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 200 \
                or "\n" in summary or "\r" in summary:
            errs.append("approval_reference.summary must be one line of at most 200 characters")
    if rec["scope"] != SCOPE:
        errs.append(f"scope must be {SCOPE!r}: an approval never covers a build, publish or deploy")
    return errs


def is_draft(rec: dict) -> bool:
    """True when a record labels itself a draft or provisional, or says it is not
    an approval. Such a record never approves anything."""
    ref = rec.get("approval_reference") if isinstance(rec, dict) else None
    if not isinstance(ref, dict):
        return False
    text = " ".join(str(ref.get(k) or "") for k in ("id", "summary"))
    return bool(DRAFT_RE.search(text))


def load_records(approvals_dir: pathlib.Path,
                 findings: list[Finding]) -> dict[str, list[dict]]:
    """Every valid record, grouped by signal_id. Problems become violations."""
    by_signal: dict[str, list[dict]] = {}
    if not approvals_dir.is_dir():
        return by_signal
    for entry in sorted(approvals_dir.iterdir()):
        if entry.is_symlink():
            findings.append(Finding("violation", "unexpected-file", f"{entry.name} is a symlink"))
            continue
        if entry.is_file():
            if entry.name not in EDITABLE_FILES:
                findings.append(Finding("violation", "unexpected-file",
                                        f"{entry.name} does not belong at the top of the ledger"))
            continue
        if entry.name in SKIP_DIRS:
            continue
        if not UUID_RE.match(entry.name):
            findings.append(Finding("violation", "unexpected-file",
                                    f"folder {entry.name} is not a signal_id"))
            continue
        for f in sorted(entry.iterdir()):
            sid = entry.name
            if f.is_symlink() or not f.is_file() or f.suffix != ".json" \
                    or not APPROVAL_ID_RE.match(f.stem):
                findings.append(Finding("violation", "unexpected-file",
                                        f"{f.name} is not an <approval-id>.json record", sid))
                continue
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                findings.append(Finding("violation", "record-invalid", f"{f.name}: {exc}", sid))
                continue
            errs = validate_record(rec, signal_id=sid, approval_id=f.stem)
            if errs:
                for e in errs:
                    findings.append(Finding("violation", "record-invalid", f"{f.name}: {e}", sid))
                continue
            by_signal.setdefault(sid, []).append(rec)
    return by_signal


def chain_tip(sid: str, records: list[dict], findings: list[Finding]) -> dict | None:
    """Check one pin's chain and return its current record (None if broken)."""
    ordered = sorted(records, key=lambda r: r["pin_version"])
    versions = [r["pin_version"] for r in ordered]
    if versions != list(range(1, len(ordered) + 1)):
        findings.append(Finding("violation", "chain-broken",
                                f"pin_version must run 1..{len(ordered)} with no gaps or repeats; "
                                f"found {versions}", sid))
        return None
    ok = True
    for prev, rec in zip([None] + ordered[:-1], ordered):
        aid = rec["approval_id"]
        if prev is None:
            if rec["action"] != "approve" or rec["supersedes"] is not None:
                findings.append(Finding("violation", "chain-broken",
                                        f"{aid}: version 1 must be an approve with no supersedes", sid))
                ok = False
            continue
        if rec["supersedes"] != prev["approval_id"]:
            findings.append(Finding("violation", "chain-broken",
                                    f"{aid}: must supersede the current record {prev['approval_id']}", sid))
            ok = False
        if aid <= prev["approval_id"]:
            findings.append(Finding("violation", "chain-broken",
                                    f"{aid}: is not later than {prev['approval_id']}", sid))
            ok = False
        if prev["action"] == "revoke" and rec["action"] != "approve":
            findings.append(Finding("violation", "chain-broken",
                                    f"{aid}: only a fresh approve may follow a revoke", sid))
            ok = False
        if prev["action"] != "revoke" and rec["action"] == "approve":
            findings.append(Finding("violation", "chain-broken",
                                    f"{aid}: a live approval is changed with supersede, not approve", sid))
            ok = False
    if ok:
        for aid, code, msg in update_problems(ordered):
            findings.append(Finding("violation", code, f"{aid}: {msg}", sid))
            ok = False
    return ordered[-1] if ok else None


def _checked(payload) -> dict:
    meta = payload.get("metadata") if isinstance(payload, dict) else None
    chk = meta.get("checked") if isinstance(meta, dict) else None
    return chk if isinstance(chk, dict) else {}


def update_problems(ordered: list[dict]) -> list[tuple[str, str, str]]:
    """The material-update rules over one pin's chain, in version order.

    Each record carrying data is compared with the last earlier record that
    carried data (revokes carry none). Returns (approval_id, code, message)."""
    out: list[tuple[str, str, str]] = []
    prev = None
    for rec in ordered:
        body = rec.get("approved_payload")
        if not isinstance(body, dict):
            continue
        aid = rec["approval_id"]
        chk, pchk = _checked(body), _checked(prev["approved_payload"]) if prev else {}
        new_u, old_u = cs_mod.update_fields(chk), cs_mod.update_fields(pchk)
        if not is_material(rec):
            if new_u != old_u:
                out.append((aid, "unflagged-material-update",
                            "changes last_material_update_at / update_note / update_sources "
                            "without a material approval; a material update needs a pin-approval "
                            "v2 supersede with material: true, and a correction or retraction "
                            "keeps its own wording, not the update fields"))
        else:
            start = None
            try:
                start = cs_mod._parse_iso(chk.get("incident_start"), "incident_start", aid)
            except cs_mod.CheckedSignalError:
                pass
            why = cs_mod.update_fields_problem(chk, body.get("published_at"), start)
            if why is None and not new_u:
                why = "a material update must set last_material_update_at, update_note and update_sources"
            if why:
                out.append((aid, "material-update-invalid", why))
            elif new_u == old_u:
                out.append((aid, "material-update-invalid",
                            "material: true but the update fields are unchanged from the previous version"))
            else:
                when = _iso_tz(new_u["last_material_update_at"])
                before = _iso_tz(old_u.get("last_material_update_at"))
                if before is not None and when is not None and when <= before:
                    out.append((aid, "material-update-invalid",
                                "last_material_update_at must be later than the previous update"))
                approved = _iso_tz(rec.get("approved_at"))
                if approved is not None and when is not None and when > approved:
                    out.append((aid, "material-update-invalid",
                                "last_material_update_at is after the owner approved the update"))
            if prev is not None:
                for k, a, b in (("incident_start", chk.get("incident_start"), pchk.get("incident_start")),
                                ("published_at", body.get("published_at"),
                                 prev["approved_payload"].get("published_at"))):
                    if a != b:
                        out.append((aid, "update-changed-clock",
                                    f"{k} may not change on an update ({b!r} -> {a!r})"))
        prev = rec
    return out


# ── Append-only (git) ──────────────────────────────────────────────────────────────

def _git(repo_root: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo_root), *args],
                          capture_output=True, text=True, timeout=60)


def _protected(rel_in_ledger: str) -> bool:
    return rel_in_ledger not in EDITABLE_FILES


def append_only_findings(repo_root: pathlib.Path, approvals_dir: pathlib.Path,
                         base: str = DEFAULT_BASE) -> list[Finding]:
    findings: list[Finding] = []
    try:
        rel = approvals_dir.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return [Finding("gap", "append-only-unchecked",
                        f"{approvals_dir} is not inside {repo_root}")]
    try:
        have_base = _git(repo_root, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}")
    except (OSError, subprocess.SubprocessError) as exc:
        return [Finding("gap", "append-only-unchecked", f"git is not available: {exc}")]
    if have_base.returncode != 0:
        return [Finding("gap", "append-only-unchecked",
                        f"{base} is not available here; run git fetch first")]

    def _report(path: str, how: str) -> None:
        inner = path[len(rel) + 1:] if path.startswith(rel + "/") else path
        if _protected(inner):
            findings.append(Finding("violation", "append-only",
                                    f"{path} was {how}; records are append-only "
                                    f"(add a supersede or revoke record instead)"))

    # 1. The working tree against the base: what this checkout would add.
    diff = _git(repo_root, "diff", "--no-renames", "--name-status", base, "--", rel)
    if diff.returncode != 0:
        return [Finding("gap", "append-only-unchecked", f"git diff failed: {diff.stderr.strip()}")]
    words = {"M": "changed", "D": "deleted", "T": "changed (type)"}
    for line in diff.stdout.splitlines():
        status, _, path = line.partition("\t")
        if status and status[0] != "A":
            _report(path, words.get(status[0], f"altered ({status})"))
    # 2. History: a change already pushed to main is invisible to the diff above.
    log = _git(repo_root, "log", "--no-renames", "--diff-filter=DMT", "--name-status",
               "--format=", "HEAD", "--", rel)
    if log.returncode == 0:
        seen: set[str] = set()
        for line in log.stdout.splitlines():
            status, _, path = line.partition("\t")
            if path and path not in seen:
                seen.add(path)
                _report(path, "changed or deleted in an earlier commit")
    else:
        findings.append(Finding("gap", "append-only-unchecked",
                                f"git log failed: {log.stderr.strip()}"))
    return findings


# ── Verify ──────────────────────────────────────────────────────────────────────────

def _batch_pins(index_path: pathlib.Path):
    """(the loaded CheckedSet, pins by signal_id with their batch file name)."""
    loaded = cs_mod.load_index(index_path)
    by_id: dict[str, tuple[dict, str]] = {}
    for b in loaded.batches:
        for pin in b.signals:
            by_id[pin["signal_id"]] = (pin, b.path.name)
    return loaded, by_id


def verify(index_path: pathlib.Path | None = None,
           approvals_dir: pathlib.Path | None = None,
           repo_root: pathlib.Path | None = None,
           base: str = DEFAULT_BASE,
           enforce: bool | None = None,
           check_git: bool = True) -> Report:
    # Defaults are read at call time so tests (and callers) can point elsewhere.
    index_path = pathlib.Path(index_path if index_path is not None else cs_mod.INDEX_PATH)
    approvals_dir = pathlib.Path(approvals_dir if approvals_dir is not None else APPROVALS_DIR)
    repo_root = pathlib.Path(repo_root if repo_root is not None else ROOT)
    findings: list[Finding] = []
    report = Report(enforce=enforcement(approvals_dir, findings, enforce), findings=findings)

    records = load_records(approvals_dir, findings)
    report.records = sum(len(v) for v in records.values())
    tips: dict[str, dict | None] = {sid: chain_tip(sid, recs, findings)
                                    for sid, recs in records.items()}

    try:
        loaded, by_id = _batch_pins(pathlib.Path(index_path))
    except (cs_mod.CheckedSignalError, OSError, ValueError) as exc:
        findings.append(Finding("violation", "pins-unreadable", f"docs/signals could not be read: {exc}"))
        loaded, by_id = None, {}

    if loaded is not None:
        active = {p["signal_id"] for p in loaded.active}
        retracted = set(loaded.retracted)
        report.pins_checked = len(active)
        for sid in sorted(active):
            pin, batch_name = by_id[sid]
            if sid not in records:
                findings.append(Finding("gap", "no-approval", "active pin has no approval record", sid))
                continue
            tip = tips.get(sid)
            if tip is None:
                continue   # chain already reported
            if tip["action"] == "revoke":
                findings.append(Finding("violation", "revoked-active",
                                        "approval was revoked but the pin is still active; "
                                        "retract or remove it before any publish", sid))
                continue
            if is_draft(tip):
                findings.append(Finding("violation", "draft-approval",
                                        f"{str(pin.get('title') or '')[:70]!r}: "
                                        f"current record {tip['approval_id']} is a draft, not the "
                                        f"owner's approval (approval_reference: "
                                        f"{tip['approval_reference']['summary'][:80]!r}); the pin is "
                                        f"unapproved until a real approval record replaces it", sid))
                continue
            current = payload_sha256(pin)
            if tip["payload_sha256"] != current:
                findings.append(Finding("violation", "hash-mismatch",
                                        f"pin data ({current}) differs from the approved data "
                                        f"({tip['payload_sha256']}, {tip['approval_id']})", sid))
                continue
            if tip["batch_file"] != batch_name:
                findings.append(Finding("violation", "batch-mismatch",
                                        f"pin is in {batch_name}; approval names {tip['batch_file']}", sid))
                continue
            report.approved_pins += 1
        known = set(by_id)
        for sid in sorted(records):
            tip = tips.get(sid)
            if sid not in known and not (tip and tip["action"] == "revoke"):
                findings.append(Finding("warning", "absent-pin",
                                        "approval record for a pin that is not in any batch", sid))
            elif sid in retracted and tip and tip["action"] != "revoke":
                findings.append(Finding("warning", "retracted-approved",
                                        "pin is retracted but its approval is not revoked", sid))
            if sid in retracted:
                gone = _iso_tz(loaded.retracted[sid].get("retracted_at"))
                for rec in records[sid]:
                    when = _iso_tz(rec.get("approved_at"))
                    if is_material(rec) and gone is not None and when is not None and when > gone:
                        findings.append(Finding("violation", "retracted-update",
                                                f"{rec['approval_id']}: a material update was approved "
                                                f"after the pin was retracted; a retraction keeps its "
                                                f"own wording", sid))

    if check_git:
        findings.extend(append_only_findings(pathlib.Path(repo_root), pathlib.Path(approvals_dir), base))
    return report


def report_lines(report: Report, limit: int = 10) -> list[str]:
    """Short console summary; gaps are counted, not listed one by one."""
    lines = [f.line() for f in report.violations]
    no_approval = [f for f in report.gaps if f.code == "no-approval"]
    other_gaps = [f for f in report.gaps if f.code != "no-approval"]
    lines += [f.line() for f in other_gaps]
    if no_approval:
        shown = ", ".join(f.signal_id or "?" for f in no_approval[:limit])
        more = f" (+{len(no_approval) - limit} more)" if len(no_approval) > limit else ""
        lines.append(f"[approvals] gap no-approval: {len(no_approval)} active pin(s) have no "
                     f"approval record: {shown}{more}")
    lines += [f.line() for f in report.warnings]
    mode = "enforced" if report.enforce else "NOT enforced (policy.json enforce=false)"
    verdict = "OK" if report.ok else "REFUSED"
    lines.append(f"[approvals] {report.records} record(s), {report.approved_pins}/{report.pins_checked} "
                 f"active pins approved, {len(report.violations)} violation(s), "
                 f"{len(report.gaps)} gap(s); ledger {mode}: {verdict}")
    return lines


def refusal_reason(report: Report) -> str:
    parts = []
    drafts = [f.signal_id or "?" for f in report.violations if f.code == "draft-approval"]
    if drafts:
        parts.append(f"{len(drafts)} pin(s) whose only approval is a draft, not the owner's "
                     f"approval: {', '.join(drafts)}")
    if report.violations:
        parts.append(f"{len(report.violations)} violation(s), first: {report.violations[0].line()}")
    if report.enforce and report.gaps:
        parts.append(f"{len(report.gaps)} gap(s) with enforcement on, first: {report.gaps[0].line()}")
    return "; ".join(parts) or "ok"


# ── CLI ────────────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify", help="check the ledger against docs/signals")
    v.add_argument("--index", type=pathlib.Path, default=cs_mod.INDEX_PATH)
    v.add_argument("--approvals", type=pathlib.Path, default=APPROVALS_DIR)
    v.add_argument("--base", default=DEFAULT_BASE, help="append-only baseline (default origin/main)")
    v.add_argument("--no-git", action="store_true", help="skip the append-only check")
    v.add_argument("--enforce", action="store_true", help="treat gaps as refusals")
    v.add_argument("--all", action="store_true", help="list every finding")
    h = sub.add_parser("payload-hash", help="print the approved_payload hash for one pin")
    h.add_argument("signal_id")
    h.add_argument("--index", type=pathlib.Path, default=cs_mod.INDEX_PATH)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "payload-hash":
            _, by_id = _batch_pins(args.index)
            if args.signal_id not in by_id:
                print(f"[approvals] {args.signal_id} is not in any batch", file=sys.stderr)
                return 2
            pin, batch = by_id[args.signal_id]
            print(json.dumps({"signal_id": args.signal_id, "batch_file": batch,
                              "payload_sha256": payload_sha256(pin)}, indent=2))
            return 0
        report = verify(args.index, args.approvals, ROOT, args.base,
                        enforce=args.enforce or None, check_git=not args.no_git)
    except (cs_mod.CheckedSignalError, ApprovalError, OSError, ValueError) as exc:
        print(f"[approvals] error: {exc}", file=sys.stderr)
        return 2
    lines = [f.line() for f in report.findings] + report_lines(report)[-1:] if args.all \
        else report_lines(report)
    for line in lines:
        print(line)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
