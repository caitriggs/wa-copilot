from copilot.sources import weworkremotely as wwr

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>We Work Remotely: Remote Programming Jobs</title>
  <item>
    <title>Cascade Robotics: SDET, Platform</title>
    <region>USA Only</region>
    <category>Full-Stack Programming</category>
    <description>&lt;p&gt;Own the &lt;b&gt;automation&lt;/b&gt; harness.&lt;/p&gt;</description>
    <link>https://weworkremotely.com/remote-jobs/x</link>
    <pubDate>Mon, 10 Aug 2026 00:00:00 +0000</pubDate>
  </item>
  <item>
    <title>QA Manager</title>
    <region>Anywhere in the World</region>
    <description>Lead QA.</description>
    <link>https://weworkremotely.com/remote-jobs/y</link>
    <pubDate></pubDate>
  </item>
</channel></rss>
"""


def test_parse_splits_company_title_and_strips_html():
    jobs = wwr.parse_response(RSS, keywords=["automation"])
    assert len(jobs) == 2
    j = jobs[0]
    assert j.source == "weworkremotely"
    assert j.employer == "Cascade Robotics"
    assert j.title == "SDET, Platform"
    assert j.location == "USA Only"
    assert j.remote is True
    assert j.posted_date == "2026-08-10"           # RFC-822 -> ISO
    assert "<p>" not in j.description and "automation" in j.description.lower()
    assert "automation" in j.keywords


def test_parse_title_without_company_and_missing_date():
    j = wwr.parse_response(RSS)[1]
    assert j.employer == "" and j.title == "QA Manager"
    assert j.location == "Anywhere in the World"
    assert j.posted_date == ""


def test_parse_bad_xml_empty():
    assert wwr.parse_response("<not xml") == []
    assert wwr.parse_response("") == []


def test_fetch_skips_when_disabled(user_env):
    assert wwr.fetch(user_env) == []


def test_fetch_gets_and_parses(user_env, monkeypatch):
    import requests
    user_env.data["sources"]["weworkremotely"] = {"enabled": True,
                                                   "categories": ["remote-programming-jobs"]}

    class _Resp:
        text = RSS

        def raise_for_status(self):
            pass

    def _get(url, timeout=None, headers=None):
        assert "remote-programming-jobs" in url
        return _Resp()

    monkeypatch.setattr(requests, "get", _get)
    assert len(wwr.fetch(user_env)) == 2
