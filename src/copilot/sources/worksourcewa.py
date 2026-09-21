"""
WorkSourceWA source.
====================

WorkSourceWA (worksourcewa.com) is WA's official job board and doing activity there directly
counts as an ESD-approved job-search activity. It has saved searches with job notifications, but
no confirmed public API/RSS, and a site redesign is pending — so we do NOT hard-depend on a feed.

- If you have a real saved-search RSS URL, put it in `sources.worksourcewa.saved_search_rss`; it
  is ingested by sources/feeds.py.
- Otherwise, use its email alerts (email-alert ingest is a Phase-2 add-on).

fetch() here intentionally returns [] and points at the feed path, rather than scraping the site.
"""

from __future__ import annotations

from ..models import JobPosting


def fetch(cfg) -> list[JobPosting]:
    if not cfg.get("sources.worksourcewa.enabled", False):
        return []
    rss = cfg.get("sources.worksourcewa.saved_search_rss", "")
    if rss:
        # Handled by feeds.py (which also reads worksourcewa.saved_search_rss) to avoid double work.
        print("  [worksourcewa] saved_search_rss set — ingested via the feeds source.")
    else:
        print("  [worksourcewa] no API/RSS; do activity on worksourcewa.com directly "
              "(it counts as an ESD activity) or add a saved-search RSS URL to your config.")
    return []
