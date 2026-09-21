from copilot.sources import email_alerts as ea

LINKEDIN_HTML = """
<html><body>
  <a href="https://www.linkedin.com/comm/jobs/view/3812345678/?trk=eml-x">Senior Operations Manager</a>
  Acme Corp · Seattle, WA
  <a href="https://www.linkedin.com/comm/jobs/view/3812999999/?trk=eml-y">Program Manager</a>
  Globex · Remote (WA)
  <a href="https://www.linkedin.com/help/answer">Help Center</a>
  <a href="https://www.linkedin.com/comm/jobs/view/3812345678/?trk=dup">Senior Operations Manager</a>
</body></html>
"""

INDEED_HTML = """
<html><body>
  <a href="https://www.indeed.com/rc/clk?jk=abc123&fccid=z">Warehouse Operations Manager - Kent, WA</a>
  Cascade Logistics - Kent, WA
  <a href="https://www.indeed.com/viewjob?jk=def456">Ops Coordinator</a>
  Northwest Freight
  <a href="https://www.indeed.com/account/login">Unsubscribe</a>
</body></html>
"""

WORKSOURCE_HTML = """
<html><body>
  <a href="https://www.worksourcewa.com/jobsearch/job/12345">Forklift Operator</a>
  Puget Sound Warehousing · Tacoma, WA
</body></html>
"""


def test_linkedin_parse_and_dedup():
    jobs = ea.parse_alert_email("linkedin", LINKEDIN_HTML, keywords=["operations"])
    titles = [j.title for j in jobs]
    assert "Senior Operations Manager" in titles
    assert "Program Manager" in titles
    assert "Help Center" not in titles                 # non-job link ignored
    assert len(jobs) == 2                                # duplicate view/3812345678 collapsed
    j0 = next(j for j in jobs if j.title == "Senior Operations Manager")
    assert j0.employer == "Acme Corp"
    assert j0.location == "Seattle, WA"
    assert j0.source == "email:linkedin"
    assert "operations" in j0.keywords


def test_indeed_parse_split_on_dash():
    jobs = ea.parse_alert_email("indeed", INDEED_HTML)
    assert len(jobs) == 2                                # login/unsubscribe link ignored
    j = next(j for j in jobs if j.title.startswith("Warehouse"))
    assert j.employer == "Cascade Logistics"
    assert j.location == "Kent, WA"


def test_worksource_parse():
    jobs = ea.parse_alert_email("worksourcewa", WORKSOURCE_HTML)
    assert len(jobs) == 1
    assert jobs[0].title == "Forklift Operator"
    assert jobs[0].employer == "Puget Sound Warehousing"


def test_generic_anchor_text_skipped():
    html = '<a href="https://www.linkedin.com/comm/jobs/view/1/">View job</a>'
    assert ea.parse_alert_email("linkedin", html) == []


def test_unknown_provider_returns_empty():
    assert ea.parse_alert_email("monster", INDEED_HTML) == []


def test_fetch_skips_when_disabled(user_env):
    # user_env config has email_alerts disabled by default -> no network, returns []
    assert ea.fetch(user_env) == []
