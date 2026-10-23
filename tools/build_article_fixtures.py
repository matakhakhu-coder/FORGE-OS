"""Build offline article fixtures for the Batch 6 bench cases.

Run once with the network:  python -m tools.build_article_fixtures
After that, later steps and the bench read the fixtures and need no network.

Two outputs:
  * data/bench/article_fixtures.json (committed): for each case, what the reader
    did with each link (read, feed-date-only, blocked and why), the article's own
    published date, the incident date it pulled from the text and the dated
    phrases it found. No article text and no names are committed.
  * data/bench/article_fixtures_local/ (git-ignored): the saved page HTML for
    links the reader was allowed to fetch, so tests on this machine can re-parse
    them offline. Article text stays local because storing full articles is the
    copying risk the Batch 7 check flagged.

Outlets that need written permission (Caxton's citizen.co.za, the Newcastillian)
or that are lead-only (Limpopo Mirror, Zoutpansberger) are not fetched. For those
cases the fixture records the facts the owner already checked by hand in the
23 October sweep notes, marked as such, so date logic can still be tested.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
from datetime import datetime, timezone

from forage.collectors import article_reader as ar

REPO = ar.REPO_ROOT
CASES = os.path.join(REPO, "data", "bench", "collector_bench_cases.json")
REPLAY = os.path.join(REPO, "data", "bench", "collector_bench_replay.jsonl.gz")
OUT = os.path.join(REPO, "data", "bench", "article_fixtures.json")
LOCAL = os.path.join(REPO, "data", "bench", "article_fixtures_local")

TARGET_CASES = [
    "lt_nrcs_feed_date",
    "nc_p1_counterfeit",
    "nc_p2_quad_bike",
    "plk_88_firearms",
    "plk_airport_officers",
    "nc_oosthuizen_court",
    "nc_mlambo_court",
    "musina_carrim",
]

# Direct article links found by search for cases whose bench entry has no URL.
EXTRA_LINKS = {
    "lt_nrcs_feed_date": ["https://www.limpopomirror.co.za/article/nrcs-seizes-nearly-r1m-in-non-compliant-goods-in-louis-trichardt"],
    "nc_oosthuizen_court": ["https://tabloidmedia.co.za/henrico-oosthuizen-murder-case-postponed-to-march-2027/"],
}

# Facts the owner already verified by hand (sweep notes, 23 Oct 2026), in our
# own words. Used only where the reader may not fetch the article.
MANUAL_FACTS = {
    "lt_nrcs_feed_date": {
        "source_note": "louis-trichardt-thohoyandou-sweep-2026-10-23.md",
        "outlet": "Limpopo Mirror / Zoutpansberger",
        "feed_date": "2026-10-08", "incident_date": "2026-09-15",
        "summary": "Regulator inspection seized non-compliant goods worth about R976,000; Google News dated it 8 Oct, the article puts the inspection on 15 Sep.",
    },
    "nc_p1_counterfeit": {
        "source_note": "newcastle-sweep-2026-10-23.md",
        "outlet": "Newcastillian News", "published": "2026-10-21", "incident_date": None,
        "summary": "Police units seized about R1.05 million in suspected counterfeit and illicit goods from shops under search warrants; no arrests or charges reported; operation date not given. A reader comment on the page used the word 'fraudulent'.",
    },
    "nc_p2_quad_bike": {
        "source_note": "newcastle-sweep-2026-10-23.md",
        "outlet": "Newcastle Advertiser (Caxton)", "published": "2026-10-05", "incident_date": "2026-09-30",
        "summary": "Two suspected syndicate members arrested while allegedly stripping a stolen quad bike in an open field in Schuinshoogte; private security company source.",
    },
    "plk_88_firearms": {
        "source_note": "polokwane-sweep-2026-10-23.md",
        "outlet": "Review Online (Caxton)", "published": "2026-10-01", "incident_date": "2026-09-27",
        "conduct_date": "2026-05/2026-07",
        "summary": "A former police volunteer allegedly posed as a firearm inspector between May and July; arrested about 27 Sep and charged with theft; one suspect so far.",
    },
    "plk_airport_officers": {
        "source_note": "polokwane-sweep-2026-10-23.md",
        "outlet": "Review Online (Caxton)", "published": "2026-10-19", "incident_date": "2026-10-19",
        "conduct_date": "2025-04-17",
        "summary": "Arrests on the evening of 19 Oct over 46 undocumented nationals who never reached the police station on 17 Apr 2025; bail on 20 Oct; back in court 30 Nov.",
    },
    "nc_mlambo_court": {
        "source_note": "newcastle-sweep-2026-10-23.md",
        "outlet": "The Citizen (Caxton)", "published": "2026-10-23", "incident_date": None,
        "conduct_date": "2023",
        "summary": "Court step in the Madadeni High Court on a 2023 case.",
    },
    "musina_carrim": {
        "source_note": "musina-sweep-2026-10-23.md",
        "outlet": "Review Online / Limpopo Mirror", "published": "2026-10-22", "incident_date": "2026-10-21",
        "summary": "Court appearances in Musina over the attempted theft of Absa vehicles intercepted near Beitbridge.",
    },
}

MAX_GNEWS_LINKS_PER_CASE = 2  # each one is a request to Google; keep it small


def _load_cases() -> dict:
    with open(CASES, encoding="utf-8") as fh:
        return {c["id"]: c for c in json.load(fh)["cases"]}


def _replay_links(match: str) -> list[tuple[str, str, str]]:
    rx = re.compile(match, re.I)
    out, seen = [], set()
    with gzip.open(REPLAY, "rt", encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            if r.get("_meta"):
                continue
            if not rx.search(r.get("title") or ""):
                continue
            link = (r.get("metadata") or {}).get("link")
            if link and link not in seen:
                seen.add(link)
                out.append((link, r.get("title") or "", (r.get("timestamp") or "")[:10]))
    return out


def _facts(reading: ar.ArticleReading) -> dict:
    """What gets committed: dates and flags, no article text, no context windows."""
    return {
        "status": reading.status,
        "reason": reading.reason,
        "resolution": reading.resolution,
        "article_url": reading.article_url,
        "feed_date": reading.feed_date,
        "published": reading.published,
        "published_source": reading.published_source,
        "incident_date": reading.incident_date,
        "incident_basis": reading.incident_basis,
        "date_mentions": [
            {k: m[k] for k in ("phrase", "date", "precision", "kind", "cue")}
            for m in reading.date_mentions
        ],
        "body_chars": len(reading.body_text or ""),
        "body_sha256": hashlib.sha256((reading.body_text or "").encode()).hexdigest() if reading.body_text else None,
    }


def build(network: bool = True) -> dict:
    cases = _load_cases()
    fetcher = ar.PoliteFetcher(min_interval_s=10.0)
    reader = ar.ArticleReader(fetcher, cache=None)
    os.makedirs(LOCAL, exist_ok=True)
    result = {
        "built": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": "Batch 6 step 1. Reader outcome per link for the bench cases Batch 6 targets. "
                "No article text is committed; saved pages for fetched links are in "
                "data/bench/article_fixtures_local/ (git-ignored).",
        "cases": {},
    }
    for cid in TARGET_CASES:
        case = cases[cid]
        links: list[tuple[str, str, str]] = []
        if case.get("url"):
            links.append((case["url"], case.get("headline") or "", case.get("feed_date") or ""))
        for u in EXTRA_LINKS.get(cid, []):
            links.append((u, case.get("headline") or "", ""))
        gn = [l for l in _replay_links(case["match"]) if ar.is_google_news(l[0])]
        links.extend(gn[:MAX_GNEWS_LINKS_PER_CASE])
        entries = []
        for url, title, feed_date in links:
            policy, why = ar.outlet_policy(url, fetcher.permissions)
            if not network and (ar.is_google_news(url) or policy == ar.POLICY_OPEN):
                continue
            if ar.is_google_news(url):
                res = fetcher.resolve(url)
                if res.status == "feed_date_only" or not res.url:
                    reading = ar.ArticleReading(url, "feed_date_only", res.reason, res.status,
                                                feed_date=feed_date or None)
                else:
                    reading = reader.read_signal(url, feed_date or None, shortlisted=True)
            else:
                fr = fetcher.fetch(url) if policy == ar.POLICY_OPEN else ar.FetchResult(
                    url, "blocked_policy", reason=f"{policy}: {why}")
                if fr.status == "ok":
                    reading = ar.parse_article(fr.html or "", fr.final_url or url, feed_date or None)
                    reading.resolution = "direct"
                    name = hashlib.sha1(url.encode()).hexdigest()[:16] + ".html"
                    with open(os.path.join(LOCAL, name), "w", encoding="utf-8") as fh:
                        fh.write(fr.html or "")
                    reading.reason = f"saved locally as {name}"
                else:
                    status = "blocked" if fr.status.startswith("blocked") else "error"
                    reading = ar.ArticleReading(url, status, fr.reason, "direct", article_url=url,
                                                feed_date=feed_date or None)
            entry = {"link": url, "headline": title}
            entry.update(_facts(reading))
            entries.append(entry)
        result["cases"][cid] = {
            "headline": case.get("headline"),
            "bench_incident_date": case.get("incident_date"),
            "links": entries,
            "manual_facts": MANUAL_FACTS.get(cid),
        }
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    data = build(network=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    for cid, c in data["cases"].items():
        print(cid, [(e["status"], e.get("published"), e.get("incident_date")) for e in c["links"]])


if __name__ == "__main__":
    main()
