from copilot.sources import adzuna

PAYLOAD = {
    "count": 2,
    "results": [
        {
            "title": "Operations Manager",
            "company": {"display_name": "Cascade Logistics"},
            "location": {"display_name": "Seattle, WA", "area": ["US", "Washington", "Seattle"]},
            "redirect_url": "https://www.adzuna.com/land/ad/123",
            "salary_min": 95000, "salary_max": 120000,
            "created": "2026-07-20T12:00:00Z",
            "description": "Lead logistics and fleet operations. Remote-friendly.",
        },
        {
            "title": "Program Manager",
            "company": {"display_name": "Globex"},
            "location": {"display_name": "Tacoma, WA"},
            "redirect_url": "https://www.adzuna.com/land/ad/456",
            "created": "2026-07-19T00:00:00Z",
            "description": "Own program delivery.",
        },
    ],
}


def test_parse_response_maps_fields():
    jobs = adzuna.parse_response(PAYLOAD, keywords=["logistics"])
    assert len(jobs) == 2
    j = jobs[0]
    assert j.source == "adzuna"
    assert j.title == "Operations Manager"
    assert j.employer == "Cascade Logistics"
    assert j.location == "Seattle, WA"
    assert j.comp_min == 95000 and j.comp_max == 120000
    assert j.comp_source == "posting"
    assert j.remote is True                     # "remote" in description
    assert j.posted_date == "2026-07-20"
    assert "logistics" in j.keywords


def test_parse_response_handles_missing_salary():
    j = adzuna.parse_response(PAYLOAD)[1]
    assert j.comp_min is None
    assert j.comp_source == "estimate"


def test_parse_response_empty():
    assert adzuna.parse_response({}) == []


def test_fetch_skips_when_disabled(user_env):
    # user_env config has adzuna disabled by default -> no network, returns []
    assert adzuna.fetch(user_env) == []


def _capture_params(user_env, monkeypatch):
    import requests
    monkeypatch.setattr(user_env, "get_secret", lambda ref: "X" if "adzuna" in ref else None)

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": []}

    captured = {}

    def _get(url, params=None, timeout=None):
        captured["params"] = params
        return _Resp()

    monkeypatch.setattr(requests, "get", _get)
    return captured


def test_salary_filter_off_by_default_does_not_send_salary_min(user_env, monkeypatch):
    # comp_min set + salary_filter default false -> Adzuna is NOT asked to pre-filter by salary
    # (the "returns 0" bug). The floor is applied downstream by discover, not here.
    user_env.data["sources"]["adzuna"]["enabled"] = True
    user_env.data["search"]["comp_min"] = 130000
    captured = _capture_params(user_env, monkeypatch)
    adzuna.fetch(user_env)
    assert "salary_min" not in captured["params"]


def test_salary_filter_opt_in_sends_salary_min(user_env, monkeypatch):
    user_env.data["sources"]["adzuna"]["enabled"] = True
    user_env.data["sources"]["adzuna"]["salary_filter"] = True
    user_env.data["search"]["comp_min"] = 130000
    captured = _capture_params(user_env, monkeypatch)
    adzuna.fetch(user_env)
    assert captured["params"]["salary_min"] == 130000


def test_error_never_leaks_api_key(user_env, monkeypatch, capsys):
    """An Adzuna HTTP error must not print the URL (which carries app_id/app_key)."""
    import requests

    user_env.data["sources"]["adzuna"]["enabled"] = True
    monkeypatch.setattr(user_env, "get_secret",
                        lambda ref: "SECRETKEY123" if "adzuna" in ref else None)

    class _Resp:
        status_code = 503

    def _boom(*a, **k):
        err = requests.HTTPError("503 Server Error for url ...app_key=SECRETKEY123...")
        err.response = _Resp()
        raise err

    monkeypatch.setattr(requests, "get", _boom)
    adzuna.fetch(user_env)
    out = capsys.readouterr().out
    assert "SECRETKEY123" not in out
    assert "HTTP 503" in out
