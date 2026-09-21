"""Tests for scripts/fetch_approvals.py — the backup apply email (jobs + CoWork prompt + CSV link)."""

import importlib.util
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "fetch_approvals.py"


def _load():
    spec = importlib.util.spec_from_file_location("fetch_approvals", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fetch_approvals = _load()


def test_send_apply_email_links_csv_and_lists_jobs(tmp_path, monkeypatch):
    from copilot import paths
    import copilot.mailer as mailer

    user = "ftu"
    paths.ensure_scaffold(user, tmp_path)
    paths.config_path(user, tmp_path).write_text(
        "user: ftu\n"
        "search: { titles: ['x'] }\n"
        "email: { enabled: true, to: 'max@example.com' }\n"
        "applicant: { full_name: 'Alex Rivera', email: 'alex@x.com' }\n",
        encoding="utf-8")
    csv_path = paths.user_root(user, tmp_path) / "log" / "submitted_jobs.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text("date,job_title,company\n2026-08-22,SDET,Acme\n", encoding="utf-8")

    captured = {}

    def _capture(cfg, subject, text, html_body=None, attachments=None):
        captured.update(subject=subject, text=text, html=html_body, attachments=attachments)

    monkeypatch.setattr(mailer, "send", _capture)

    email_jobs = [{"title": "SDET, Platform", "org": "Acme", "location": "Seattle, WA",
                   "url": "https://x/1"}]
    fetch_approvals.send_apply_email(user, "2026-08-22", email_jobs, csv_path,
                                     "https://dash.example", data_root=str(tmp_path))

    # CSV is linked to the live Worker route, NOT attached (single source of truth).
    assert not captured.get("attachments")
    csv_url = "https://dash.example/api/approvals/csv?week=2026-08-22"
    assert csv_url in captured["html"] and csv_url in captured["text"]
    assert "SDET, Platform" in captured["html"]          # job listed
    assert "Alex Rivera" in captured["html"]              # CoWork prompt filled from applicant


def test_send_apply_email_skipped_when_email_disabled(tmp_path, monkeypatch):
    from copilot import paths
    import copilot.mailer as mailer

    user = "ftu2"
    paths.ensure_scaffold(user, tmp_path)
    paths.config_path(user, tmp_path).write_text(
        "user: ftu2\nsearch: { titles: ['x'] }\nemail: { enabled: false, to: '' }\n", encoding="utf-8")
    csv_path = paths.user_root(user, tmp_path) / "log" / "submitted_jobs.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text("date\n2026\n", encoding="utf-8")

    called = {"n": 0}
    monkeypatch.setattr(mailer, "send", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    fetch_approvals.send_apply_email(user, "2026-08-22", [{"title": "x", "url": "u"}], csv_path,
                                     "https://d", data_root=str(tmp_path))
    assert called["n"] == 0                                # email disabled -> no send


def test_send_apply_email_no_jobs_is_noop(tmp_path, monkeypatch):
    import copilot.mailer as mailer
    monkeypatch.setattr(mailer, "send", lambda *a, **k: (_ for _ in ()).throw(AssertionError("sent")))
    fetch_approvals.send_apply_email("u", "w", [], tmp_path / "x.csv", "https://d", data_root=str(tmp_path))


def test_send_apply_email_threads_resume_filenames_into_cowork_prompt(tmp_path, monkeypatch):
    """resume_file on each email job must reach the CoWork prompt's résumé-filenames list — this
    is what lets the job seeker's own CoWork know exactly which local PDF to attach per job."""
    from copilot import paths
    import copilot.mailer as mailer

    user = "ftu3"
    paths.ensure_scaffold(user, tmp_path)
    paths.config_path(user, tmp_path).write_text(
        "user: ftu3\nsearch: { titles: ['x'] }\nemail: { enabled: true, to: 'max@example.com' }\n"
        "applicant: { full_name: 'Alex Rivera' }\n", encoding="utf-8")
    csv_path = paths.user_root(user, tmp_path) / "log" / "submitted_jobs.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text("date\n2026\n", encoding="utf-8")

    captured = {}
    monkeypatch.setattr(mailer, "send",
                        lambda cfg, subject, text, html_body=None, attachments=None:
                        captured.update(html=html_body))

    email_jobs = [{"title": "SDET", "org": "Acme", "url": "https://x/1", "resume_file": "qa-lead.pdf"}]
    fetch_approvals.send_apply_email(user, "2026-08-22", email_jobs, csv_path,
                                     "https://dash.example", data_root=str(tmp_path))
    assert "qa-lead.pdf" in captured["html"]
    assert "~/Desktop/wa-unemployment-copilot/" in captured["html"]
    assert "~/Downloads/" in captured["html"]


# --------------------------------------------------------------------------- resume_file column


def test_load_resume_filenames_maps_label_to_basename(tmp_path):
    from copilot import paths

    user = "resu"
    paths.ensure_scaffold(user, tmp_path)
    paths.config_path(user, tmp_path).write_text(
        "user: resu\n"
        "search: { titles: ['x'] }\n"
        "profile:\n"
        "  resumes:\n"
        "    - { label: 'Project Manager', path: 'profile/project-manager.pdf' }\n"
        "    - { label: 'QA Lead', path: 'profile/qa-lead.pdf' }\n",
        encoding="utf-8")
    mapping = fetch_approvals._load_resume_filenames(user, str(tmp_path))
    assert mapping == {"Project Manager": "project-manager.pdf", "QA Lead": "qa-lead.pdf"}


def test_load_resume_filenames_returns_empty_on_missing_config(tmp_path):
    assert fetch_approvals._load_resume_filenames("nope", str(tmp_path)) == {}


def test_build_rows_includes_resume_file_from_best_resume():
    items_by_id = {"j1": {"title": "PM", "employer": "Acme", "url": "u1", "best_resume": "Project Manager"},
                   "j2": {"title": "QA", "employer": "Acme", "url": "u2", "best_resume": "Unknown Resume"}}
    resume_by_label = {"Project Manager": "project-manager.pdf"}
    rows = fetch_approvals.build_rows(["j1", "j2"], items_by_id, "2026-08-22", resume_by_label)
    assert rows[0]["resume_file"] == "project-manager.pdf"
    assert rows[1]["resume_file"] == ""            # no mapping for that label -> blank, not a guess


def test_build_rows_resume_file_blank_when_no_mapping_given():
    items_by_id = {"j1": {"title": "PM", "employer": "Acme", "url": "u1", "best_resume": "Project Manager"}}
    rows = fetch_approvals.build_rows(["j1"], items_by_id, "2026-08-22")
    assert rows[0]["resume_file"] == ""


def test_migrate_csv_header_backfills_old_rows(tmp_path):
    csv_path = tmp_path / "submitted_jobs.csv"
    csv_path.write_text(
        "date,job_title,company,application_link,status,week,job_id\n"
        "2026-08-16,SDET,Acme,https://x/1,to apply,2026-08-22,abc123\n",
        encoding="utf-8")
    fetch_approvals._migrate_csv_header(csv_path)
    with csv_path.open(newline="", encoding="utf-8") as f:
        rows = list(fetch_approvals.csv.DictReader(f))
    assert fetch_approvals.CSV_FIELDS == ["date", "job_title", "company", "application_link",
                                          "status", "week", "job_id", "resume_file"]
    assert rows[0]["resume_file"] == ""             # backfilled, not fabricated
    assert rows[0]["job_title"] == "SDET"           # old data preserved


def test_migrate_csv_header_is_a_noop_when_already_current(tmp_path):
    csv_path = tmp_path / "submitted_jobs.csv"
    original = ("date,job_title,company,application_link,status,week,job_id,resume_file\n"
                "2026-08-16,SDET,Acme,https://x/1,to apply,2026-08-22,abc123,qa-lead.pdf\n")
    csv_path.write_text(original, encoding="utf-8")
    fetch_approvals._migrate_csv_header(csv_path)
    assert csv_path.read_text(encoding="utf-8") == original


def test_migrate_csv_header_noop_on_missing_file(tmp_path):
    fetch_approvals._migrate_csv_header(tmp_path / "does-not-exist.csv")   # must not raise
