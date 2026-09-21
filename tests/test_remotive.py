from copilot.sources import remotive

PAYLOAD = {
    "job-count": 2,
    "jobs": [
        {
            "id": 1,
            "title": "QA Automation Engineer",
            "company_name": "Cascade Robotics",
            "category": "Software Development",
            "job_type": "full_time",
            "publication_date": "2026-08-10T06:25:53",
            "candidate_required_location": "USA",
            "salary": "$120,000 - $140,000",
            "url": "https://remotive.com/remote-jobs/x",
            "description": "<p>Build <b>selenium</b> + pytest automation.</p>",
        },
        {
            "id": 2,
            "title": "QA Tester",
            "company_name": "Globex",
            "publication_date": "2026-08-09T00:00:00",
            "candidate_required_location": "Worldwide",
            "salary": "",
            "url": "https://remotive.com/remote-jobs/y",
            "description": "Manual QA.",
        },
    ],
}


def test_parse_response_maps_fields_and_strips_html():
    jobs = remotive.parse_response(PAYLOAD, keywords=["selenium"])
    assert len(jobs) == 2
    j = jobs[0]
    assert j.source == "remotive"
    assert j.title == "QA Automation Engineer"
    assert j.employer == "Cascade Robotics"
    assert j.location == "USA"
    assert j.remote is True
    assert j.comp_min == 120000 and j.comp_max == 140000 and j.comp_source == "posting"
    assert j.posted_date == "2026-08-10"
    assert "<p>" not in j.description and "selenium" in j.description.lower()
    assert "selenium" in j.keywords


def test_parse_response_missing_salary():
    j = remotive.parse_response(PAYLOAD)[1]
    assert j.comp_min is None and j.comp_source == ""
    assert j.location == "Worldwide" and j.remote is True


def test_parse_response_empty():
    assert remotive.parse_response({}) == []


def test_fetch_skips_when_disabled(user_env):
    assert remotive.fetch(user_env) == []


def test_fetch_gets_and_parses_no_key_needed(user_env, monkeypatch):
    import requests
    user_env.data["sources"]["remotive"] = {"enabled": True, "results_per_page": 10}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return PAYLOAD

    def _get(url, params=None, timeout=None, headers=None):
        return _Resp()

    monkeypatch.setattr(requests, "get", _get)
    jobs = remotive.fetch(user_env)      # no secret configured -> still works (keyless API)
    assert len(jobs) == 2
