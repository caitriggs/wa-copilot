"""
Freshness + dead-link screening for ranked postings.
====================================================

WHY: aggregator links (Jooble especially) are the aggregator's own redirect pages, not the
employer's posting. Once the underlying job closes, the link lands on a "no longer available" page
— and the search API happily keeps returning the stale listing. A user who approves a pick and
clicks through to a dead page has wasted their time, so discover() screens for this BEFORE the
picks go out:

  1. `is_fresh()` — drop postings whose posted/updated date is older than
     `discover.max_age_days` (default 21). Unknown/unparseable dates are kept (neutral) unless
     `discover.drop_undated: true`.
  2. `check_url()` — for the top of the ranked list only (bounded; see discover.py), fetch each
     posting's link and classify it "dead" (HTTP 404/410, or the landing page says the job is
     expired / no longer available), "alive", or "unknown" (network error, bot-block 403/429,
     timeout). Only "dead" is dropped — "unknown" fails OPEN so a blocked checker can never
     empty the list.

This only requests the exact links the sources' official APIs handed us, one GET each, for a
handful of top picks — no crawling, no scraping of listing content.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime

DEFAULT_MAX_AGE_DAYS = 21

# Phrases aggregator/ATS pages show for a closed posting. Matched case-insensitively against the
# first ~200 KB of the landing page.
_DEAD_PATTERNS = re.compile(
    r"no longer (?:available|accepting|active|open)|job (?:has )?expired|"
    r"(?:this|the) (?:job|position|posting|vacancy) (?:has been|is|was) (?:closed|filled|removed|expired)|"
    r"job (?:not found|is closed)|position (?:has been )?filled|posting (?:has )?(?:closed|expired)|"
    r"vacancy (?:is )?(?:closed|expired)",
    re.I,
)
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0 Safari/537.36 wa-copilot-linkcheck")


def parse_posted(value: str) -> date | None:
    """ISO (YYYY-MM-DD[...]) or RFC-822 date -> date; None when unparseable."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(value).date()
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def is_fresh(posted_date: str, max_age_days: int, today: date | None = None,
             drop_undated: bool = False) -> bool:
    """True if posted within `max_age_days` (0/None disables the cutoff). A missing/unparseable
    date is kept unless `drop_undated` (config `discover.drop_undated`) — the strict mode for
    "must be posted in the last N days", where an undated listing can't prove it."""
    if not max_age_days or max_age_days <= 0:
        return True
    d = parse_posted(posted_date)
    if d is None:
        return not drop_undated
    return d >= (today or date.today()) - timedelta(days=int(max_age_days))


def classify_page(status: int, body: str) -> str:
    """Pure classifier (unit-tested): 'dead' | 'alive' | 'unknown'."""
    if status in (404, 410):
        return "dead"
    if status in (401, 403, 429) or status >= 500:
        return "unknown"
    if 200 <= status < 400:
        return "dead" if _DEAD_PATTERNS.search(body or "") else "alive"
    return "unknown"


def check_url(url: str, timeout: float = 15.0) -> str:
    if not url or not url.lower().startswith(("http://", "https://")):
        return "unknown"
    try:
        import requests
    except ImportError:
        return "unknown"
    try:
        resp = requests.get(url, timeout=timeout, allow_redirects=True,
                            headers={"User-Agent": _UA, "Accept": "text/html,*/*"}, stream=True)
        raw = resp.raw.read(200_000, decode_content=True) or b""
        resp.close()
        return classify_page(resp.status_code, raw.decode(resp.encoding or "utf-8", "replace"))
    except Exception:  # noqa: BLE001 — a checker hiccup must never drop a posting
        return "unknown"
