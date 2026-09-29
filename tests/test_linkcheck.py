"""Freshness + dead-link screening (linkcheck.py and its discover.py wiring). No network."""

from datetime import date

from copilot import config, discover, linkcheck
from copilot.models import JobPosting


def test_parse_posted_iso_and_rfc822():
    assert linkcheck.parse_posted("2026-09-20T10:00:00") == date(2026, 9, 20)
    assert linkcheck.parse_posted("Sun, 20 Sep 2026 10:00:00 +0000") == date(2026, 9, 20)
    assert linkcheck.parse_posted("garbage") is None


def test_is_fresh():
    today = date(2026, 9, 29)
    assert linkcheck.is_fresh("2026-09-20", 21, today)
    assert not linkcheck.is_fresh("2026-06-01", 21, today)
    assert linkcheck.is_fresh("", 21, today)            # unknown date -> kept
    assert linkcheck.is_fresh("2026-06-01", 0, today)   # 0 disables


def test_classify_page():
    assert linkcheck.classify_page(404, "") == "dead"
    assert linkcheck.classify_page(410, "") == "dead"
    assert linkcheck.classify_page(200, "<h1>This job is no longer available</h1>") == "dead"
    assert linkcheck.classify_page(200, "Sorry, the job has expired.") == "dead"
    assert linkcheck.classify_page(200, "<h1>Technical Program Manager</h1> Apply now") == "alive"
    assert linkcheck.classify_page(403, "") == "unknown"   # bot-blocked -> never dropped
    assert linkcheck.classify_page(503, "") == "unknown"


def _jp(title, url):
    return JobPosting(source="jooble", title=title, employer="Acme", url=url)


def test_drop_dead_links_keeps_unknown_and_below_cutoff(monkeypatch):
    cfg = config.Config("u", {"discover": {"verify_top": 3}})
    ranked = [_jp("A", "https://x/a"), _jp("B", "https://x/b"), _jp("C", "https://x/c"),
              _jp("D", "https://x/d")]
    status = {"https://x/a": "alive", "https://x/b": "dead", "https://x/c": "unknown",
              "https://x/d": "dead"}   # D is below the cutoff -> never checked, kept
    monkeypatch.setattr(linkcheck, "check_url", lambda url, timeout=15.0: status[url])
    kept = discover._drop_dead_links(cfg, ranked)
    assert [j.title for j in kept] == ["A", "C", "D"]


def test_drop_dead_links_can_be_disabled(monkeypatch):
    cfg = config.Config("u", {"discover": {"verify_links": False}})
    monkeypatch.setattr(linkcheck, "check_url", lambda url, timeout=15.0: "dead")
    ranked = [_jp("A", "https://x/a")]
    assert discover._drop_dead_links(cfg, ranked) == ranked
