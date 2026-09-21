"""
Remotive source — official free remote-jobs API (no key). Good games-adjacent tech/QA coverage.
================================================================================================

Remotive publishes a clean public JSON API of remote jobs (software-dev, QA, etc.) — one of the
upstreams the games board ASGC itself aggregates. Free, documented, no auth. Returns the FULL job
description (HTML), so unlike snippet-only aggregators it feeds the LLM qualification rubric
rich text.

Endpoint: GET https://remotive.com/api/remote-jobs?search=<keyword>&limit=<n>
Docs: https://remotive.com/api-documentation

All Remotive jobs are remote (remote=True). No key required — just enable the source.
"""

from __future__ import annotations

import re

from ..models import JobPosting

API_URL = "https://remotive.com/api/remote-jobs"
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", s or "")).strip()


def _salary(text: str) -> tuple[float | None, float | None]:
    """Best-effort (min, max) from Remotive's free-text salary; (None, None) if unparseable."""
    text = text or ""
    if re.search(r"hour|/hr|hourly|per hr", text.lower()):
        rates = [float(n) for n in re.findall(r"(\d{1,3}(?:\.\d+)?)", text)]
        nums = sorted({round(r * 2080) for r in rates if 5 <= r <= 400})
    else:
        ks = [float(n) * 1000 for n in re.findall(r"(\d{2,3})\s*[kK]\b", text)]
        plains = [float(n) for n in re.findall(r"(\d{4,7})", text.replace(",", ""))]
        nums = sorted({n for n in ks + plains if n >= 1000})
    if not nums:
        return None, None
    return nums[0], (nums[-1] if len(nums) > 1 else None)


def _parse(job: dict, keywords: list[str]) -> JobPosting:
    lo, hi = _salary(job.get("salary") or "")
    return JobPosting(
        source="remotive",
        title=(job.get("title") or "").strip(),
        employer=(job.get("company_name") or "").strip(),
        location=(job.get("candidate_required_location") or "Remote").strip(),
        url=(job.get("url") or "").strip(),
        remote=True,                                   # every Remotive posting is remote
        comp_min=lo,
        comp_max=hi,
        comp_source="posting" if lo else "",
        posted_date=(job.get("publication_date", "") or "")[:10],
        description=_strip_html(job.get("description") or ""),
        keywords=list(keywords),
    )


def parse_response(payload: dict, keywords=None) -> list[JobPosting]:
    """Pure parser over a Remotive JSON response (unit-tested without network)."""
    keywords = keywords or []
    out, seen = [], set()
    for job in (payload.get("jobs") or []):
        jp = _parse(job, keywords)
        if jp.title and jp.dedup_key not in seen:
            seen.add(jp.dedup_key)
            out.append(jp)
    return out


def fetch(cfg) -> list[JobPosting]:
    conf = cfg.get("sources.remotive", {}) or {}
    if not conf.get("enabled", False):
        return []
    try:
        import requests
    except ImportError:
        print("  [remotive] 'requests' not installed; skipping.")
        return []

    limit = int(conf.get("results_per_page", 25))
    titles = cfg.get("search.titles", []) or [""]
    keywords = cfg.get("search.keywords_include", []) or []

    out, seen = [], set()
    for title in titles:
        params = {"limit": limit}
        if title:
            params["search"] = title
        try:
            resp = requests.get(API_URL, params=params, timeout=30,
                                headers={"User-Agent": "wa-copilot/1.0"})
            resp.raise_for_status()
            postings = parse_response(resp.json(), keywords)
        except Exception as e:  # noqa: BLE001
            print(f"  [remotive] query {title!r} failed: {type(e).__name__}")
            continue
        for jp in postings:
            if jp.dedup_key not in seen:
                seen.add(jp.dedup_key)
                out.append(jp)
    print(f"  [remotive] {len(out)} postings")
    return out
