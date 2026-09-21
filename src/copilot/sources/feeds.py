"""
Generic saved-search RSS/Atom ingest.
======================================

For sources without an official API (LinkedIn, Indeed, WorkSourceWA), the reliable, ToS-respectful
path is: create a saved search on the site, get its RSS/email alert, and ingest that here. This is
also how to pull REAL state/local government postings: WorkSourceWA itself has no RSS (see
worksourcewa.py), but many WA state agencies, counties, and cities run their official career pages
on the governmentjobs.com/schooljobs.com platform (NEOGOV), which publishes a genuine RSS feed per
agency at `https://www.schooljobs.com/SearchEngine/JobsFeed?agency=<agency-slug>` (the slug is the
agency's own governmentjobs.com/careers/<slug> URL segment) — just add those URLs here.

Reads every URL in config `sources.feeds.rss_urls` (plus `sources.worksourcewa.saved_search_rss`
if set). Requires the optional `feedparser` package; without it, returns [] with a note.

NEOGOV/GovernmentJobs.com feeds carry richer per-item fields than a bare RSS <item> — a location
and hiring department in a `joblisting:` XML namespace, which feedparser exposes as
`joblisting_location` / `joblisting_department` — plus the feed's own <channel><title> naming the
agency (e.g. "City of Tacoma, WA"). Used when present so postings from these feeds aren't left with
a blank employer/location (which would also make dedup_key — employer|title|location — collide
across unrelated postings); any other RSS/Atom feed without these fields degrades gracefully to
`entry.author` (or blank), same as before.

Email-alert ingest (routing alert emails to a Gmail label and reading them) is a Phase-2 add-on;
this module covers the RSS half now.
"""

from __future__ import annotations

from ..models import JobPosting


def _feed_source_name(url: str) -> str:
    u = url.lower()
    for host in ("linkedin", "indeed", "worksource", "google", "ziprecruiter", "schooljobs", "governmentjobs"):
        if host in u:
            return f"feeds:{host}"
    return "feeds"


def _employer(entry, feed_title: str) -> str:
    """Best available employer name: the entry's own author if a feed provides one, else a NEOGOV
    agency feed's department + agency name (e.g. "City of Tacoma, WA — Water"), else just the
    feed's channel title (still better than leaving it blank), else ''."""
    author = (entry.get("author", "") or "").strip()
    if author:
        return author
    department = (entry.get("joblisting_department", "") or "").strip()
    feed_title = (feed_title or "").strip()
    if department and feed_title and department.lower() not in feed_title.lower():
        return f"{feed_title} — {department}"
    return feed_title or department


def fetch(cfg) -> list[JobPosting]:
    urls = list(cfg.get("sources.feeds.rss_urls", []) or [])
    ws_rss = cfg.get("sources.worksourcewa.saved_search_rss", "")
    if ws_rss:
        urls.append(ws_rss)
    urls = [u for u in urls if u]
    if not urls:
        return []
    try:
        import feedparser  # type: ignore
    except ImportError:
        print("  [feeds] 'feedparser' not installed; skipping RSS ingest.")
        return []

    keywords = cfg.get("search.keywords_include", []) or []
    out: list[JobPosting] = []
    for url in urls:
        try:
            parsed = feedparser.parse(url)
        except Exception as e:  # noqa: BLE001
            print(f"  [feeds] {url} failed: {type(e).__name__}: {e}")
            continue
        src = _feed_source_name(url)
        feed_title = (parsed.get("feed", {}) or {}).get("title", "")
        for entry in parsed.get("entries", []):
            title = entry.get("title", "")
            out.append(JobPosting(
                source=src,
                title=title,
                employer=_employer(entry, feed_title),
                location=entry.get("joblisting_location", "") or "",
                url=entry.get("link", ""),
                posted_date=(entry.get("published", "") or "")[:10],
                description=entry.get("summary", ""),
                keywords=list(keywords),
            ))
    print(f"  [feeds] {len(out)} postings from {len(urls)} feed(s)")
    return out
