from copilot.sources import careerjet

PAYLOAD = {
    "type": "JOBS",
    "hits": 2,
    "pages": 1,
    "jobs": [
        {
            "title": "SDET",
            "company": "Cascade Robotics",
            "locations": "Bellevue, WA",
            "description": "<p>Own the <b>automation</b> harness. Remote OK.</p>",
            "salary": "$130,000.00 - $150,000.00 per year",
            "url": "https://www.careerjet.com/jobad/abc",
            "date": "Mon, 10 Aug 2026 00:00:00 GMT",
            "site": "indeed.com",
        },
        {
            "title": "QA Manager",
            "company": "Globex",
            "locations": "Seattle, WA",
            "description": "Lead QA.",
            "salary": "",
            "url": "https://www.careerjet.com/jobad/def",
            "date": "",
        },
    ],
}


def test_parse_response_maps_fields_and_strips_html():
    jobs = careerjet.parse_response(PAYLOAD, keywords=["automation"])
    assert len(jobs) == 2
    j = jobs[0]
    assert j.source == "careerjet"
    assert j.title == "SDET"
    assert j.employer == "Cascade Robotics"
    assert j.location == "Bellevue, WA"
    assert j.comp_min == 130000 and j.comp_max == 150000
    assert j.comp_source == "posting"
    assert j.remote is True
    assert j.posted_date == "2026-08-10"           # RFC-822 -> ISO
    assert "<p>" not in j.description and "automation" in j.description.lower()
    assert "automation" in j.keywords


def test_parse_response_handles_missing_salary_and_date():
    j = careerjet.parse_response(PAYLOAD)[1]
    assert j.comp_min is None and j.comp_source == ""
    assert j.posted_date == ""


def test_parse_response_non_jobs_type_empty():
    assert careerjet.parse_response({"type": "LOCATIONS", "locations": []}) == []


def test_parse_response_empty():
    assert careerjet.parse_response({}) == []


def test_fetch_skips_when_disabled(user_env):
    assert careerjet.fetch(user_env) == []


def test_fetch_gets_and_parses(user_env, monkeypatch):
    import requests
    user_env.data["sources"]["careerjet"] = {"enabled": True, "locale_code": "en_US"}
    monkeypatch.setattr(user_env, "get_secret",
                        lambda ref: "AFFID123" if ref == "careerjet_affid" else None)

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return PAYLOAD

    captured = {}

    def _get(url, params=None, timeout=None):
        captured["params"] = params
        return _Resp()

    monkeypatch.setattr(requests, "get", _get)
    jobs = careerjet.fetch(user_env)
    assert len(jobs) == 2
    # required params present for the API's validation
    for k in ("affid", "keywords", "user_ip", "user_agent", "url"):
        assert k in captured["params"]


def test_error_never_leaks_affid(user_env, monkeypatch, capsys):
    import requests
    user_env.data["sources"]["careerjet"] = {"enabled": True}
    monkeypatch.setattr(user_env, "get_secret",
                        lambda ref: "SECRETAFFID" if ref == "careerjet_affid" else None)

    def _boom(*a, **k):
        raise requests.HTTPError("500 ...affid=SECRETAFFID...")

    monkeypatch.setattr(requests, "get", _boom)
    careerjet.fetch(user_env)
    assert "SECRETAFFID" not in capsys.readouterr().out
