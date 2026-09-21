"""
Careerjet source — free job-aggregator JSON API (broad private + some public-sector coverage).
===============================================================================================

Careerjet's Public Search API aggregates postings from across the web and is free with an
affiliate id. Complements USAJOBS/Adzuna/Jooble for private + local-gov roles.

Auth is an affiliate id (`affid`) plus a few required request params (user_ip/user_agent/url — the
API validates their presence). Register for an affid at https://www.careerjet.com/partners/ , then:
    python scripts/setup_user.py --user <you> --set-secret careerjet_affid
Endpoint: GET https://public.api.careerjet.net/search
Docs: https://www.careerjet.com/partners/api/

NOTE ON DESCRIPTIONS: Careerjet returns a SNIPPET (HTML) per job, not the full posting text, plus a
link to the source. We strip the HTML and pass the snippet through as the description; the LLM
qualification rubric uses whatever text is available (snippet-level here). Aggregator limitation,
not a bug to fix by scraping.

If the affid isn't configured, fetch() returns [] with a note (never aborts the weekly run).
"""

from __future__ import annotations

import re
from email.utils import parsedate_to_datetime

from ..models import JobPosting

API_URL = "https://public.api.careerjet.net/search"
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", s or "")).strip()


def _iso_date(s: str) -> str:
    """Careerjet dates are RFC-822 ('Mon, 10 Aug 2026 00:00:00 GMT'); return YYYY-MM-DD or ''."""
    if not s:
        return ""
    try:
        return parsedate_to_datetime(s).date().isoformat()
    except (TypeError, ValueError):
        return s[:10]


def _salary(text: str) -> tuple[float | None, float | None]:
    """Best-effort (min, max) from Careerjet's free-text salary string; (None, None) if unparseable."""
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
    loc = (job.get("locations") or "").strip()
    desc = _strip_html(job.get("description") or "")
    lo, hi = _salary(job.get("salary") or "")
    hay = f"{title} {loc} {desc}".lower()
    return JobPosting(
        source="careerjet",
        title=title,
        employer=(job.get("company") or "").strip(),
        location=loc,
        url=(job.get("url") or "").strip(),
        remote="remote" in hay,
        comp_min=lo,
        comp_max=hi,
        comp_source="posting" if lo else "",
        posted_date=_iso_date(job.get("date", "")),
        description=desc,
        keywords=list(keywords),
    )


def parse_response(payload: dict, keywords=None) -> list[JobPosting]:
    """Pure parser over a Careerjet JSON response (unit-tested without network). Only JOBS-type
    responses carry postings; LOCATIONS/errors yield nothing."""
    keywords = keywords or []
    if payload.get("type") not in (None, "JOBS"):
        return []
    out, seen = [], set()
    for job in (payload.get("jobs") or []):
        jp = _parse(job, keywords)
        if jp.title and jp.dedup_key not in seen:
            seen.add(jp.dedup_key)
            out.append(jp)
    return out


def fetch(cfg) -> list[JobPosting]:
    conf = cfg.get("sources.careerjet", {}) or {}
    if not conf.get("enabled", False):
        return []
    try:
        import requests
    except ImportError:
        print("  [careerjet] 'requests' not installed; skipping.")
        return []

    affid = cfg.get_secret("careerjet_affid")
    if not affid:
        print("  [careerjet] no affiliate id configured; skipping. "
              f"Set with: python scripts/setup_user.py --user {cfg.user} --set-secret careerjet_affid")
        return []

    locale = str(conf.get("locale_code", "en_US"))
    per_page = int(conf.get("results_per_page", 25))
    titles = cfg.get("search.titles", []) or [""]
    locations = cfg.get("search.locations", []) or [""]
    keywords = cfg.get("search.keywords_include", []) or []
    loc = next((l for l in locations if l and l.lower() != "remote"), "")

    out, seen = [], set()
    for title in titles:
        params = {
            "affid": affid, "keywords": title, "locale_code": locale,
            "pagesize": per_page, "page": 1, "sort": "date",
            # These are required by the API's validation for a server-side call.
            "user_ip": conf.get("user_ip", "1.1.1.1"),
            "user_agent": "wa-copilot/1.0",
            "url": "https://www.careerjet.com/",
        }
        if loc:
            params["location"] = loc
        try:
            resp = requests.get(API_URL, params=params, timeout=30)
            resp.raise_for_status()
            postings = parse_response(resp.json(), keywords)
        except Exception as e:  # noqa: BLE001 — don't leak params (they carry the affid)
            print(f"  [careerjet] query {title!r} failed: {type(e).__name__}")
            continue
        for jp in postings:
            if jp.dedup_key not in seen:
                seen.add(jp.dedup_key)
                out.append(jp)
    print(f"  [careerjet] {len(out)} postings")
    return out
