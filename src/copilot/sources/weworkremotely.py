"""
We Work Remotely source — official category RSS feeds. Games-adjacent remote tech/QA coverage.
==============================================================================================

We Work Remotely publishes official per-category RSS feeds — one of the upstreams the games board
ASGC aggregates. No key required. Each item's title is "Company: Job Title", with a <region>
(location) and a full HTML <description>.

Feeds: https://weworkremotely.com/categories/<slug>.rss
  default slug: remote-programming-jobs  (QA/SDET/games engineering roles live here); add more via
  config sources.weworkremotely.categories (e.g. remote-devops-sysadmin-jobs).

Parsed with stdlib xml.etree (no feedparser dependency). All postings are remote (remote=True).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from ..models import JobPosting

FEED_TMPL = "https://weworkremotely.com/categories/{slug}.rss"
DEFAULT_CATEGORIES = ["remote-programming-jobs"]
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", s or "")).strip()


def _iso_date(s: str) -> str:
    if not s:
        return ""
    try:
        return parsedate_to_datetime(s).date().isoformat()
    except (TypeError, ValueError):
        return s[:10]


def _split_title(raw: str) -> tuple[str, str]:
    """WWR item titles are 'Company: Job Title' -> (employer, title). No colon -> ('', title)."""
    raw = (raw or "").strip()
    if ": " in raw:
        emp, _, title = raw.partition(": ")
        return emp.strip(), title.strip()
    return "", raw


def parse_response(xml_text: str, keywords=None) -> list[JobPosting]:
    """Pure parser over a WWR category RSS document (unit-tested without network)."""
    keywords = keywords or []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out, seen = [], set()
    for item in root.iter("item"):
        emp, title = _split_title(item.findtext("title") or "")
        if not title:
            continue
        region = (item.findtext("region") or "Remote").strip()
        jp = JobPosting(
            source="weworkremotely",
            title=title,
            employer=emp,
            location=region,
            url=(item.findtext("link") or "").strip(),
            remote=True,
            comp_min=None,
            comp_max=None,
            comp_source="",
            posted_date=_iso_date(item.findtext("pubDate") or ""),
            description=_strip_html(item.findtext("description") or ""),
            keywords=list(keywords),
        )
        if jp.dedup_key not in seen:
            seen.add(jp.dedup_key)
            out.append(jp)
    return out


def fetch(cfg) -> list[JobPosting]:
    conf = cfg.get("sources.weworkremotely", {}) or {}
    if not conf.get("enabled", False):
        return []
    try:
        import requests
    except ImportError:
        print("  [weworkremotely] 'requests' not installed; skipping.")
        return []

    categories = conf.get("categories") or DEFAULT_CATEGORIES
    keywords = cfg.get("search.keywords_include", []) or []

    out, seen = [], set()
    for slug in categories:
        try:
            resp = requests.get(FEED_TMPL.format(slug=slug), timeout=30,
                                headers={"User-Agent": "wa-copilot/1.0"})
            resp.raise_for_status()
            postings = parse_response(resp.text, keywords)
        except Exception as e:  # noqa: BLE001
            print(f"  [weworkremotely] feed {slug!r} failed: {type(e).__name__}")
            continue
        for jp in postings:
            if jp.dedup_key not in seen:
                seen.add(jp.dedup_key)
                out.append(jp)
    print(f"  [weworkremotely] {len(out)} postings")
    return out
