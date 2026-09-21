"""
Jooble source — free job-aggregator JSON API (broad private + some public-sector coverage).
============================================================================================

Jooble aggregates postings from thousands of sites (company career pages, boards, some gov) and
exposes a free, ToS-friendly JSON API. Good complement to USAJOBS/Adzuna for private + local-gov
roles that a federal-only feed can't surface.

Auth is an API key embedded in the request PATH (POST with a JSON body):
    POST https://jooble.org/api/<apikey>   body: {"keywords": "...", "location": "...", "page": "1"}
Register at https://jooble.org/api/about , then store the key:
    python scripts/setup_user.py --user <you> --set-secret jooble
Docs: https://jooble.org/api/about

NOTE ON DESCRIPTIONS: like every aggregator, Jooble returns a SNIPPET (not the full posting text),
plus a link to the source. We pass the snippet through as the description; the LLM qualification
rubric (discover.py) uses whatever text is available — richer for USAJOBS (full Qualifications),
snippet-level here. That's an inherent aggregator limitation, not a bug to fix by scraping.

If the key isn't configured, fetch() returns [] with a note (never aborts the weekly run).
"""

from __future__ import annotations

import re

from ..models import JobPosting

API_TMPL = "https://jooble.org/api/{key}"
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", s or "")).strip()


def _salary(text: str) -> tuple[float | None, float | None]:
    """Best-effort (min, max) from Jooble's free-text salary string (e.g. '$120,000 - $140,000
    per year', '$60k', '$55/hr'). Returns (None, None) when nothing parseable — the pipeline treats
    unknown comp neutrally rather than dropping the posting."""
    text = text or ""
    if re.search(r"hour|/hr|hourly|per hr", text.lower()):           # hourly rate -> annualize
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
    title = (job.get("title") or "").strip()
    loc = (job.get("location") or "").strip()
    snippet = _strip_html(job.get("snippet") or "")   # Jooble snippets embed HTML tags
    lo, hi = _salary(job.get("salary") or "")
    hay = f"{title} {loc} {snippet}".lower()
    return JobPosting(
        source="jooble",
        title=title,
        employer=(job.get("company") or "").strip(),
        location=loc,
        url=(job.get("link") or "").strip(),
        remote="remote" in hay,
        comp_min=lo,
        comp_max=hi,
        comp_source="posting" if lo else "",
        posted_date=(job.get("updated", "") or "")[:10],
        description=snippet,
        keywords=list(keywords),
    )


def parse_response(payload: dict, keywords=None) -> list[JobPosting]:
    """Pure parser over a Jooble JSON response (unit-tested without network)."""
    keywords = keywords or []
    out, seen = [], set()
    for job in (payload.get("jobs") or []):
        jp = _parse(job, keywords)
        if jp.title and jp.dedup_key not in seen:
            seen.add(jp.dedup_key)
            out.append(jp)
    return out


def fetch(cfg) -> list[JobPosting]:
    conf = cfg.get("sources.jooble", {}) or {}
    if not conf.get("enabled", False):
        return []
    try:
        import requests
    except ImportError:
        print("  [jooble] 'requests' not installed; skipping.")
        return []

    key = cfg.get_secret("jooble")
    if not key:
        print("  [jooble] no API key configured; skipping. "
              f"Set with: python scripts/setup_user.py --user {cfg.user} --set-secret jooble")
        return []

    url = API_TMPL.format(key=key)
    titles = cfg.get("search.titles", []) or [""]
    locations = cfg.get("search.locations", []) or [""]
    keywords = cfg.get("search.keywords_include", []) or []
    loc = next((l for l in locations if l and l.lower() != "remote"), "")

    out, seen = [], set()
    for title in titles:
        body = {"keywords": title, "page": "1"}
        if loc:
            body["location"] = loc
        try:
            resp = requests.post(url, json=body, timeout=30)
            resp.raise_for_status()
            postings = parse_response(resp.json(), keywords)
        except Exception as e:  # noqa: BLE001 — never leak the URL (it carries the API key in the path)
            print(f"  [jooble] query {title!r} failed: {type(e).__name__}")
            continue
        for jp in postings:
            if jp.dedup_key not in seen:
                seen.add(jp.dedup_key)
                out.append(jp)
    print(f"  [jooble] {len(out)} postings")
    return out
