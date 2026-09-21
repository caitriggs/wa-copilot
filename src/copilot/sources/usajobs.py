"""
USAJOBS source — official public API.
=====================================

Auth is two headers (the User-Agent gotcha trips everyone up):
    Authorization-Key: <your API key>          (secret_ref: usajobs)
    User-Agent:        <your registered email> (secret_ref: usajobs_email)
    Host:              data.usajobs.gov
Endpoint: GET https://data.usajobs.gov/api/search  (paging via ResultsPerPage, max 500)
Docs: https://developer.usajobs.gov/api-reference/

If the key/email aren't configured, fetch() returns [] with a note (never aborts the run).
"""

from __future__ import annotations

from ..models import JobPosting

API_URL = "https://data.usajobs.gov/api/search"


def _to_float(v):
    try:
        return float(str(v).replace(",", "").replace("$", "")) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _description(d: dict) -> str:
    """JobSummary + the Qualifications/specialized-experience narrative (QualificationSummary),
    concatenated rather than either-or — a posting's actual requirements (e.g. a specialized
    domain-experience statement) live in QualificationSummary and are usually distinct text from
    the JobSummary blurb. Both matter for relevance/qualification screening in discover.py."""
    summary = (d.get("UserArea", {}).get("Details", {}).get("JobSummary", "") or "").strip()
    quals = (d.get("QualificationSummary", "") or "").strip()
    parts = [p for p in (summary, f"Qualifications:\n{quals}" if quals else "") if p]
    return "\n\n".join(parts)


def _parse(mi: dict, keywords: list[str]) -> JobPosting:
    d = mi.get("MatchedObjectDescriptor", {}) or {}
    pay = (d.get("PositionRemuneration") or [{}])[0]
    locs = d.get("PositionLocationDisplay") or ", ".join(
        l.get("LocationName", "") for l in (d.get("PositionLocation") or [])
    )
    return JobPosting(
        source="usajobs",
        title=d.get("PositionTitle", ""),
        employer=(d.get("OrganizationName") or d.get("DepartmentName") or ""),
        location=locs,
        url=d.get("PositionURI", ""),
        remote=False,
        comp_min=_to_float(pay.get("MinimumRange")),
        comp_max=_to_float(pay.get("MaximumRange")),
        comp_source="posting",
        posted_date=(d.get("PublicationStartDate", "") or "")[:10],
        description=_description(d),
        keywords=list(keywords),
    )


def fetch(cfg) -> list[JobPosting]:
    if not cfg.get("sources.usajobs.enabled", False):
        return []
    try:
        import requests
    except ImportError:
        print("  [usajobs] 'requests' not installed; skipping.")
        return []

    key = cfg.get_secret("usajobs")
    email = cfg.get_secret("usajobs_email")
    if not key or not email:
        print("  [usajobs] no API key/email configured; skipping. "
              f"Set with: python scripts/setup_user.py --user {cfg.user} --set-secret usajobs")
        return []

    headers = {"Host": "data.usajobs.gov", "User-Agent": email, "Authorization-Key": key}
    titles = cfg.get("search.titles", []) or [""]
    locations = cfg.get("search.locations", []) or [""]
    keywords = cfg.get("search.keywords_include", []) or []

    out: list[JobPosting] = []
    seen = set()
    for title in titles:
        params = {"Keyword": title, "ResultsPerPage": 50}
        loc = next((l for l in locations if l and l.lower() != "remote"), "")
        if loc:
            params["LocationName"] = loc
        try:
            resp = requests.get(API_URL, headers=headers, params=params, timeout=30)
            resp.raise_for_status()
            items = resp.json().get("SearchResult", {}).get("SearchResultItems", []) or []
        except Exception as e:  # noqa: BLE001
            print(f"  [usajobs] query {title!r} failed: {type(e).__name__}: {e}")
            continue
        for it in items:
            jp = _parse(it, keywords)
            if jp.dedup_key not in seen:
                seen.add(jp.dedup_key)
                out.append(jp)
    print(f"  [usajobs] {len(out)} postings")
    return out
