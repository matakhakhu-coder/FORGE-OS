"""Per-town local paper lists and publisher switches (Batch 7, 23 Oct 2026).

The town routine names a town; this module says which local papers that town's
run reads, and whether each paper may be read at all. The list lives in
``data/town_sources.json`` so it can change without a code change.

A paper is read only when all of these hold:
  * its publisher's ``enabled`` is true in data/town_sources.json,
  * FORGE_LOCAL_SITEMAPS is not "0" (the global off switch),
  * it has at least one sitemap listed (the Newcastillian and Northern Natal
    News have none on purpose: they need written permission first), and
  * its host is on local_sitemaps.ALLOWED_HOSTS.

Papers that are listed for a town but can't be read come back in ``blocked``
with the reason, so a run report can say what it did not read and why.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CONFIG_FILE = os.path.join(REPO_ROOT, "data", "town_sources.json")
ENV_SWITCH = "FORGE_LOCAL_SITEMAPS"


def load_config(path: str = CONFIG_FILE) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def globally_enabled() -> bool:
    return (os.environ.get(ENV_SWITCH, "1").strip().lower() not in ("0", "false", "off", "no"))


def _norm(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def find_town(town: str, config: Optional[dict] = None) -> Optional[tuple[str, dict]]:
    """Return (canonical name, entry) for a town or one of its aliases."""
    cfg = config if config is not None else load_config()
    want = _norm(town)
    for name, entry in (cfg.get("towns") or {}).items():
        if _norm(name) == want or want in {_norm(a) for a in entry.get("aliases", [])}:
            return name, entry
    return None


@dataclass
class TownSources:
    town: str
    province: str
    readable: list = field(default_factory=list)   # paper dicts with "key"
    blocked: list = field(default_factory=list)    # (paper key, reason)

    def describe(self) -> str:
        parts = ["reads " + (", ".join(p["name"] for p in self.readable) or "no local paper")]
        for key, why in self.blocked:
            parts.append(f"not {key} ({why})")
        return f"{self.town} ({self.province}): " + "; ".join(parts)


def paper_status(key: str, config: Optional[dict] = None) -> tuple[bool, str]:
    """(readable, reason) for one paper key."""
    from forage.collectors import local_sitemaps as _ls  # avoid an import cycle

    cfg = config if config is not None else load_config()
    paper = (cfg.get("papers") or {}).get(key)
    if not paper:
        return False, "not in data/town_sources.json"
    pub = (cfg.get("publishers") or {}).get(paper.get("publisher", ""), {})
    if not globally_enabled():
        return False, f"switched off ({ENV_SWITCH}=0)"
    if not pub.get("enabled"):
        return False, pub.get("blocked") or "publisher switched off in data/town_sources.json"
    if not paper.get("sitemaps"):
        return False, "no sitemap listed"
    if (paper.get("host") or "").lower() not in _ls.ALLOWED_HOSTS:
        return False, "host not on the sitemap allowlist"
    return True, pub.get("basis", "")


def sources_for_town(town: str, config: Optional[dict] = None,
                     province: Optional[str] = None) -> TownSources:
    """Papers a town's run reads. Unknown towns fall back to the province's
    default list when ``province`` is given; otherwise they get none."""
    cfg = config if config is not None else load_config()
    hit = find_town(town, cfg)
    if hit:
        name, entry = hit
        prov = entry.get("province", province or "")
        keys = entry.get("papers") or (cfg.get("province_defaults") or {}).get(prov, [])
    else:
        name, prov = town, province or ""
        keys = (cfg.get("province_defaults") or {}).get(prov, [])
    out = TownSources(name, prov)
    for key in keys:
        ok, why = paper_status(key, cfg)
        if ok:
            out.readable.append({"key": key, **cfg["papers"][key]})
        else:
            out.blocked.append((key, why))
    return out


def blocked_hosts(config: Optional[dict] = None) -> dict:
    """Batch 8: {host: publisher key} for every switched-off publisher that
    lists its hosts. Town mode leaves out any Civic Intelligence feed on
    these hosts, so a town run makes no request to a publisher whose terms
    bar automated monitoring."""
    cfg = config if config is not None else load_config()
    out = {}
    for key, pub in (cfg.get("publishers") or {}).items():
        if pub.get("enabled", True):
            continue
        for host in pub.get("hosts") or []:
            out[host.lower()] = key
    return out


def all_readable_papers(config: Optional[dict] = None) -> list:
    cfg = config if config is not None else load_config()
    out = []
    for key in (cfg.get("papers") or {}):
        if paper_status(key, cfg)[0]:
            out.append({"key": key, **cfg["papers"][key]})
    return out
