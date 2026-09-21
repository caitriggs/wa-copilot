from copilot.sources import jooble

PAYLOAD = {
    "totalCount": 2,
    "jobs": [
        {
            "title": "QA Automation Engineer",
            "company": "Cascade Robotics",
            "location": "Seattle, WA",
            "snippet": "Build selenium + pytest automation. Remote friendly.",
            "salary": "$120,000 - $140,000 per year",
            "link": "https://jooble.org/away/123",
            "updated": "2026-08-10T00:00:00.0000000",
            "type": "Full-time",
            "source": "indeed.com",
        },
        {
            "title": "QA Test Lead",
            "company": "Globex",
            "location": "Tacoma, WA",
            "snippet": "Lead a QA team.",
            "salary": "",
            "link": "https://jooble.org/away/456",
            "updated": "2026-08-09T00:00:00.0000000",
        },
    ],
}


def test_parse_response_maps_fields():
    jobs = jooble.parse_response(PAYLOAD, keywords=["selenium"])
    assert len(jobs) == 2
    j = jobs[0]
    assert j.source == "jooble"
    assert j.title == "QA Automation Engineer"
    assert j.employer == "Cascade Robotics"
    assert j.location == "Seattle, WA"
    assert j.url == "https://jooble.org/away/123"
    assert j.comp_min == 120000 and j.comp_max == 140000
    assert j.comp_source == "posting"
    assert j.remote is True                       # "remote" in the snippet
    assert j.posted_date == "2026-08-10"
    assert "selenium" in j.keywords
    assert "selenium" in j.description.lower()     # snippet carried as description


def test_parse_response_handles_missing_salary():
    j = jooble.parse_response(PAYLOAD)[1]
    assert j.comp_min is None and j.comp_max is None
    assert j.comp_source == ""


def test_parse_response_empty():
    assert jooble.parse_response({}) == []


def test_salary_hourly_annualized():
    lo, hi = jooble._salary("$55/hr")
    assert lo == 55 * 2080 and hi is None


def test_fetch_skips_when_disabled(user_env):
    assert jooble.fetch(user_env) == []           # disabled by default -> no network


def test_fetch_posts_and_parses(user_env, monkeypatch):
    import requests
    user_env.data["sources"]["jooble"] = {"enabled": True}
    monkeypatch.setattr(user_env, "get_secret", lambda ref: "KEY123" if ref == "jooble" else None)

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return PAYLOAD

    captured = {}

    def _post(url, json=None, timeout=None):
        captured["url"] = url
        captured["body"] = json
        return _Resp()

    monkeypatch.setattr(requests, "post", _post)
    jobs = jooble.fetch(user_env)
    assert len(jobs) == 2
    assert "KEY123" in captured["url"]            # key is in the path
    assert "keywords" in captured["body"]


def test_error_never_leaks_api_key(user_env, monkeypatch, capsys):
    import requests
    user_env.data["sources"]["jooble"] = {"enabled": True}
    monkeypatch.setattr(user_env, "get_secret", lambda ref: "SECRETKEY" if ref == "jooble" else None)

    def _boom(*a, **k):
        raise requests.HTTPError("500 for https://jooble.org/api/SECRETKEY ...")

    monkeypatch.setattr(requests, "post", _boom)
    jooble.fetch(user_env)
    assert "SECRETKEY" not in capsys.readouterr().out   # URL (with key) never printed
