"""Tests for src/copilot/sources/feeds.py — generic RSS/Atom ingest, including the NEOGOV/
GovernmentJobs.com agency-feed field enrichment (location/employer) used to pull real state/local
WA government postings. feedparser.parse is monkeypatched everywhere — no network calls."""

import feedparser

from copilot import config
from copilot.sources import feeds


def _cfg(rss_urls=None, ws_rss=""):
    return config.Config("u", {
        "search": {"titles": ["x"], "keywords_include": ["ops"]},
        "sources": {"feeds": {"rss_urls": rss_urls or []},
                    "worksourcewa": {"saved_search_rss": ws_rss}},
    })


class _FakeParsed(dict):
    """Mimics feedparser's FeedParserDict enough for feeds.py's .get() usage."""


def _parsed(feed_title="", entries=None):
    return _FakeParsed(feed={"title": feed_title}, entries=entries or [])


def test_no_urls_returns_empty(monkeypatch):
    calls = []
    monkeypatch.setattr(feedparser, "parse", lambda url: calls.append(url) or _parsed())
    assert feeds.fetch(_cfg()) == []
    assert calls == []                                          # never even tries to fetch


def test_worksourcewa_saved_search_rss_is_appended(monkeypatch):
    seen = []
    monkeypatch.setattr(feedparser, "parse", lambda url: seen.append(url) or _parsed())
    feeds.fetch(_cfg(rss_urls=["https://a.example/feed"], ws_rss="https://worksourcewa.example/saved"))
    assert seen == ["https://a.example/feed", "https://worksourcewa.example/saved"]


def test_generic_feed_uses_author_and_blank_location(monkeypatch):
    entry = {"title": "SDET", "author": "Acme Corp", "link": "https://x/1",
             "published": "2026-08-01T00:00:00Z", "summary": "desc"}
    monkeypatch.setattr(feedparser, "parse", lambda url: _parsed(feed_title="", entries=[entry]))
    out = feeds.fetch(_cfg(rss_urls=["https://a.example/feed"]))
    assert len(out) == 1
    jp = out[0]
    assert jp.title == "SDET"
    assert jp.employer == "Acme Corp"
    assert jp.location == ""
    assert jp.url == "https://x/1"
    assert jp.posted_date == "2026-08-01"


def test_neogov_feed_fills_location_and_employer_from_department_and_agency(monkeypatch):
    """The exact shape schooljobs.com/SearchEngine/JobsFeed returns: no <author>, but a
    joblisting_location + joblisting_department per item and an agency name on the channel."""
    entry = {"title": "Water Transmission & Distribution Assistant Division Manager",
             "link": "https://www.schooljobs.com/careers/tacoma/jobs/5429504",
             "published": "2026-08-10T00:00:00Z", "summary": "desc",
             "joblisting_location": "Tacoma", "joblisting_department": "Water"}
    monkeypatch.setattr(feedparser, "parse",
                        lambda url: _parsed(feed_title="City of Tacoma, WA", entries=[entry]))
    out = feeds.fetch(_cfg(rss_urls=["https://www.schooljobs.com/SearchEngine/JobsFeed?agency=tacoma"]))
    assert len(out) == 1
    jp = out[0]
    assert jp.location == "Tacoma"
    assert jp.employer == "City of Tacoma, WA — Water"
    assert jp.source == "feeds:schooljobs"


def test_neogov_feed_falls_back_to_agency_name_without_department(monkeypatch):
    entry = {"title": "Program Manager", "link": "https://x/1", "joblisting_location": "Seattle"}
    monkeypatch.setattr(feedparser, "parse",
                        lambda url: _parsed(feed_title="City of Seattle, WA", entries=[entry]))
    out = feeds.fetch(_cfg(rss_urls=["https://www.schooljobs.com/SearchEngine/JobsFeed?agency=seattle"]))
    assert out[0].employer == "City of Seattle, WA"
    assert out[0].location == "Seattle"


def test_department_not_duplicated_when_already_in_agency_name(monkeypatch):
    entry = {"title": "X", "link": "https://x/1", "joblisting_department": "Water"}
    monkeypatch.setattr(feedparser, "parse",
                        lambda url: _parsed(feed_title="Tacoma Water Utility", entries=[entry]))
    out = feeds.fetch(_cfg(rss_urls=["https://x/feed"]))
    assert out[0].employer == "Tacoma Water Utility"            # not "Tacoma Water Utility — Water"


def test_one_feed_failing_does_not_block_the_others(monkeypatch):
    def fake_parse(url):
        if "bad" in url:
            raise RuntimeError("boom")
        return _parsed(entries=[{"title": "OK", "link": "https://x/1"}])
    monkeypatch.setattr(feedparser, "parse", fake_parse)
    out = feeds.fetch(_cfg(rss_urls=["https://bad.example/feed", "https://good.example/feed"]))
    assert len(out) == 1
    assert out[0].title == "OK"


def test_feedparser_not_installed_skips_gracefully(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "feedparser":
            raise ImportError("no feedparser")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert feeds.fetch(_cfg(rss_urls=["https://a.example/feed"])) == []
