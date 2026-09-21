"""
Adzuna source — licensed job-search aggregator API (broad private-sector coverage).
===================================================================================

Adzuna aggregates postings from across the web (including many Indeed/LinkedIn-sourced roles) and
exposes a clean, ToS-friendly API — a good way to get live private-sector jobs immediately,
without waiting on alert emails.

Auth is an app id + key as query params (free tier). Register at https://developer.adzuna.com/ ,
then store them:
    python scripts/setup_user.py --user <you> --set-secret adzuna_id
    python scripts/setup_user.py --user <you> --set-secret adzuna_key

Endpoint: GET https://api.adzuna.com/v1/api/jobs/{country}/search/{page}
Docs: https://developer.adzuna.com/docs/search

If the id/key aren't configured, fetch() returns [] with a note (never aborts the weekly run).
"""

from __future__ import annotations

import re

from ..models import JobPosting

API_TMPL = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", s or "")).strip()


def _to_float(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _parse(result: dict, keywords: list[str]) -> JobPosting:
    loc = (result.get("location") or {}).get("display_name", "")
    hay = f"{result.get('title','')} {loc} {result.get('description','')}".lower()
    return JobPosting(
        source="adzuna",
        title=result.get("title", "") or "",
        employer=(result.get("company") or {}).get("display_name", "") or "",
        location=loc,
        url=result.get("redirect_url", "") or "",
        remote="remote" in hay,
        comp_min=_to_float(result.get("salary_min")),
        comp_max=_to_float(result.get("salary_max")),
        comp_source="posting" if result.get("salary_min") else "estimate",
        posted_date=(result.get("created", "") or "")[:10],
        description=_strip_html(result.get("description", "") or ""),
        keywords=list(keywords),
    )


def parse_response(payload: dict, keywords=None) -> list[JobPosting]:
    """Pure parser over an Adzuna JSON response (unit-tested without network)."""
    keywords = keywords or []
    out, seen = [], set()
    for r in (payload.get("results") or []):
        jp = _parse(r, keywords)
        if jp.title and jp.dedup_key not in seen:
            seen.add(jp.dedup_key)
            out.append(jp)
    return out


def fetch(cfg) -> list[JobPosting]:
    conf = cfg.get("sources.adzuna", {}) or {}
    if not conf.get("enabled", False):
        return []
    try:
        import requests
    except ImportError:
        print("  [adzuna] 'requests' not installed; skipping.")
        return []

    app_id = cfg.get_secret("adzuna_id")
    app_key = cfg.get_secret("adzuna_key")
    if not app_id or not app_key:
        print("  [adzuna] no app id/key configured; skipping. "
              f"Set with: python scripts/setup_user.py --user {cfg.user} --set-secret adzuna_id")
        return []

    country = str(conf.get("country", "us")).lower()
    per_page = int(conf.get("results_per_page", 25))
    titles = cfg.get("search.titles", []) or [""]
    locations = cfg.get("search.locations", []) or [""]
    keywords = cfg.get("search.keywords_include", []) or []
    comp_min = cfg.get("search.comp_min")
    # Adzuna's `salary_min` filter drops every posting whose (often missing/estimated) salary is
    # below the floor — with a high comp_min this silently zeroes the results, which is exactly the
    # "Adzuna returned 0" bug. Leave it OFF by default and let discover.py's comp-fit ranking apply
    # the floor as a soft signal instead; opt back in with sources.adzuna.salary_filter: true.
    use_salary_filter = bool(conf.get("salary_filter", False))

    out, seen = [], set()
    for title in titles:
        params = {"app_id": app_id, "app_key": app_key, "results_per_page": per_page,
                  "what": title, "content-type": "application/json"}
        loc = next((l for l in locations if l and l.lower() != "remote"), "")
        if loc:
            params["where"] = loc
        if comp_min and use_salary_filter:
            params["salary_min"] = int(comp_min)
        try:
            resp = requests.get(API_TMPL.format(country=country, page=1),
                                params=params, timeout=30)
            resp.raise_for_status()
            postings = parse_response(resp.json(), keywords)
        except requests.HTTPError as e:
            # NEVER print the exception/URL — it contains app_id/app_key as query params.
            code = getattr(getattr(e, "response", None), "status_code", "?")
            print(f"  [adzuna] query {title!r} failed: HTTP {code}")
            continue
        except Exception as e:  # noqa: BLE001
            print(f"  [adzuna] query {title!r} failed: {type(e).__name__}")
            continue
        for jp in postings:
            if jp.dedup_key not in seen:
                seen.add(jp.dedup_key)
                out.append(jp)
    print(f"  [adzuna] {len(out)} postings")
    return out
