"""Tests for scripts/publish_week.py — cover-letter generation is always mocked/stubbed here.

Never exercises the real Anthropic API: draft.llm.enabled is left False (template fallback) in
most cases, and where LLM generation is explicitly tested, copilot.llm.generate_cover_letter is
monkeypatched so no network call is possible.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "publish_week.py"


def _load_publish_week():
    spec = importlib.util.spec_from_file_location("publish_week", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


publish_week = _load_publish_week()


def _posting(title, employer, score, dedup_key_hint=""):
    from copilot.models import JobPosting
    jp = JobPosting(source="usajobs", title=title, employer=employer, location="Seattle, WA",
                     url=f"https://example.com/{dedup_key_hint or title}",
                     description="A great role doing great things.")
    d = jp.to_dict()
    d["score"] = score
    d["reasons"] = ["baseline match on your titles/locations"]
    return d


def _write_cache(user_dir: Path, week: str, postings: list[dict]):
    cache_dir = user_dir / "postings_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    import json
    (cache_dir / f"{week}.json").write_text(
        json.dumps({"week": week, "postings": postings}), encoding="utf-8"
    )


@pytest.fixture()
def week():
    return "2026-08-01"


@pytest.fixture()
def seeded_user(user_env, data_root, week):
    """7 ranked postings under the scaffolded testuser, best-first by score."""
    user_dir = data_root / "testuser"
    postings = [_posting(f"Role {i}", f"Employer {i}", score=10 - i, dedup_key_hint=str(i))
                for i in range(7)]
    _write_cache(user_dir, week, postings)
    return user_dir


def test_syntax_compiles():
    import py_compile
    py_compile.compile(str(SCRIPT_PATH), doraise=True)


def test_job_from_posting_carries_match_pct_and_top_factor():
    """match_pct/top_factor are computed once in discover.py and must pass through unchanged into
    the job dict that's sent to the Worker (and, from there, into the email) — no recomputation."""
    item = _posting("Program Manager", "Acme", score=7.12)
    item["match_pct"] = 71
    item["top_factor"] = "title match"
    job = publish_week._job_from_posting(item)
    assert job["match_pct"] == 71
    assert job["top_factor"] == "title match"
    assert job["match_score"] == 7.12   # raw score still carried, for internal sort only


def test_job_from_posting_defaults_when_missing():
    item = _posting("Program Manager", "Acme", score=7.12)
    job = publish_week._job_from_posting(item)
    assert job["match_pct"] == 0
    assert job["top_factor"] == ""


def test_top5_get_letters_rest_dont(seeded_user, data_root, week):
    # draft.llm.enabled defaults False -> _cover_letter falls back to the built-in template,
    # never touching the network. Exercises the real generate_top_letters/build_payload path.
    payload, generated = publish_week.build_payload("testuser", week, str(data_root))
    jobs = payload["jobs"]
    assert len(jobs) == 7

    ranked = sorted(jobs, key=lambda j: j["match_score"], reverse=True)
    top5, rest = ranked[:5], ranked[5:]
    assert all(j["cover_letter"] for j in top5)
    assert all(not j["cover_letter"] for j in rest)
    assert generated == 5


def test_llm_generation_is_mocked_not_networked(seeded_user, data_root, week, monkeypatch):
    from copilot import config as copilot_config, llm as copilot_llm

    cfg = copilot_config.load("testuser", data_root=str(data_root))
    cfg.data["draft"] = {"llm": {"enabled": True}}
    monkeypatch.setattr(copilot_config, "load", lambda user, data_root=None: cfg)

    calls = []

    def fake_generate(cfg_arg, jp, history):
        calls.append(jp.title)
        return f"Mocked tailored letter for {jp.title}."

    monkeypatch.setattr(copilot_llm, "generate_cover_letter", fake_generate)
    monkeypatch.setattr(copilot_llm, "available", lambda cfg_arg: True)

    payload, generated = publish_week.build_payload("testuser", week, str(data_root))
    top5 = sorted(payload["jobs"], key=lambda j: j["match_score"], reverse=True)[:5]

    assert generated == 5
    assert len(calls) == 5
    assert all("Mocked tailored letter" in j["cover_letter"] for j in top5)


def test_queue_letter_reused_not_regenerated(seeded_user, data_root, week):
    import json
    queue_dir = data_root / "testuser" / "applications" / week
    queue_dir.mkdir(parents=True, exist_ok=True)
    from copilot.models import JobPosting
    jp0 = JobPosting.from_dict(_posting("Role 0", "Employer 0", score=10, dedup_key_hint="0"))
    (queue_dir / "queue.json").write_text(json.dumps({
        "items": [{"dedup_key": jp0.dedup_key, "cover_letter": "Pre-drafted letter."}]
    }), encoding="utf-8")

    payload, generated = publish_week.build_payload("testuser", week, str(data_root))
    top5 = sorted(payload["jobs"], key=lambda j: j["match_score"], reverse=True)[:5]

    assert generated == 4  # role 0 reused, roles 1-4 generated
    reused = next(j for j in top5 if j["title"] == "Role 0")
    assert reused["cover_letter"] == "Pre-drafted letter."


def test_generation_failure_for_one_job_logs_and_continues(seeded_user, data_root, week, monkeypatch, capsys):
    from copilot import draft as copilot_draft

    real_cover_letter = copilot_draft._cover_letter

    def flaky_cover_letter(jp, cfg, history):
        if jp.title == "Role 1":
            raise RuntimeError("simulated generation failure")
        return real_cover_letter(jp, cfg, history)

    # Patch the name publish_week imports (_import_cover_letter_generator does `from .draft import
    # _cover_letter`, so patch at the source module before that import executes).
    monkeypatch.setattr(copilot_draft, "_cover_letter", flaky_cover_letter)

    payload, generated = publish_week.build_payload("testuser", week, str(data_root))
    jobs_by_title = {j["title"]: j for j in payload["jobs"]}

    assert generated == 4  # 5 attempted, 1 failed
    assert jobs_by_title["Role 1"]["cover_letter"] == ""
    assert jobs_by_title["Role 0"]["cover_letter"]
    captured = capsys.readouterr()
    assert "cover-letter generation failed for Role 1" in captured.out


def test_no_postings_raises_filenotfound(user_env, data_root, week):
    with pytest.raises(FileNotFoundError):
        publish_week.build_payload("testuser", week, str(data_root))


def test_weekly_review_view_models_rank_cap_and_fields():
    from datetime import date
    today = date(2026, 8, 1)
    jobs = [
        {"title": "Best", "org": "A", "location": "Seattle, WA", "match_score": 10, "match_pct": 92,
         "top_factor": "title match", "why_matched": "w", "url": "u1", "posted_date": "2026-07-30",
         "remote": False, "description": "d1"},
        {"title": "Mid", "org": "B", "location": "Remote", "match_score": 5, "match_pct": 61,
         "top_factor": "skills match", "why_matched": "w", "url": "u2", "posted_date": "2026-07-28",
         "remote": True, "description": "d2"},
        {"title": "Low", "org": "C", "location": "Tacoma, WA", "match_score": 2, "match_pct": 30,
         "why_matched": "why-fallback", "url": "u3", "posted_date": "", "description": ""},
        {"title": "Dropped", "org": "D", "location": "X", "match_score": 1, "match_pct": 10, "url": "u4"},
    ]
    jobs[0]["reasons"] = ["fits this week's focus: seattle, remote", "pay meets your target minimum"]
    jobs[0]["resume"] = "Test Engineer"
    vms = publish_week.weekly_review_view_models(jobs, top_n=3, today=today)
    assert vms[0]["resume"] == "Test Engineer"   # résumé badge flows to the view model
    assert [v["title"] for v in vms] == ["Best", "Mid", "Low"]   # ranked, top 3
    # match_pct is read straight from the job dict (computed once in discover.py) — an absolute
    # 0-100 value, not relative to the week's strongest match.
    assert vms[0]["match_pct"] == 92
    assert vms[1]["match_pct"] == 61
    assert vms[2]["match_pct"] == 30
    assert vms[0]["posted_label"] == "posted 2d ago"
    assert vms[0]["match_reason"] == "Surfaced for: title match"   # top_factor, prefixed
    assert vms[1]["match_reason"] == "Surfaced for: skills match"
    assert vms[2]["match_reason"] == "why-fallback"              # no top_factor -> legacy fallback
    assert vms[1]["mode"] is None                                # location already 'Remote'
    assert vms[2]["posted_label"] is None                        # no posted_date
    assert vms[2]["blurb"] == "why-fallback"                     # blurb also falls back to why_matched


def test_weekly_review_view_models_match_pct_matches_across_calls():
    """The same job dict must produce the SAME match_pct every time — no per-week/relative
    recomputation — which is what keeps the dashboard and the email in agreement."""
    job = {"title": "X", "org": "A", "location": "Seattle, WA", "match_score": 7,
          "match_pct": 74, "top_factor": "qualification fit", "url": "u1"}
    vm_a = publish_week.weekly_review_view_models([job], top_n=1)[0]
    vm_b = publish_week.weekly_review_view_models([job, dict(job, title="Y", match_score=99)], top_n=1)[0]
    assert vm_a["match_pct"] == 74
    # "X" no longer ranks first once "Y" (a higher match_score) is added, but if it did, its
    # match_pct would still read 74 regardless of what else is in the pool.
    other = next(v for v in publish_week.weekly_review_view_models(
        [job, dict(job, title="Y", match_score=1)], top_n=2) if v["title"] == "X")
    assert other["match_pct"] == 74


def test_short_reason_trims_lists_and_qualifiers():
    assert publish_week._short_reason("matches your skills: ops, s&op, kpi") == "matches your skills"
    assert publish_week._short_reason(
        "title overlaps your past roles (a, b) — a plausible stretch") == "title overlaps your past roles"
    assert publish_week._short_reason("pay meets your target minimum") == "pay meets your target minimum"


def test_work_mode_and_truncate_helpers():
    assert publish_week._work_mode({"remote": True, "location": "Seattle, WA"}) == "Remote"
    assert publish_week._work_mode({"remote": True, "location": "Remote"}) is None
    assert publish_week._work_mode({"remote": False, "location": "Seattle, WA"}) is None
    long = "word " * 60
    out = publish_week._truncate(long, limit=40)
    assert len(out) <= 41 and out.endswith("…")


class _FakeCfg:
    email_enabled = True
    email_to = "max@prod.example"
    email_from = "demo+jobs@gmail.com"

    def get(self, key, default=None):
        return default

    def get_secret(self, ref, default=None):
        return default


def _stub_notify_deps(publish_week, captured, monkeypatch):
    """Point send_review_ready_email at a capturing send() + a minimal cfg, real templates."""
    from copilot.email_render import render_weekly_review, weekly_review_plaintext

    class _MailError(Exception):
        pass

    def _send(cfg, subject, text, html_body=None, attachments=None, to=None, message_id=None):
        captured.update(subject=subject, text=text, html=html_body, to=to, message_id=message_id)

    def _deps():
        return ((lambda user, data_root=None: _FakeCfg()), _send, _MailError,
                render_weekly_review, weekly_review_plaintext)

    monkeypatch.setattr(publish_week, "_import_notify_deps", _deps)


_REVIEW_PAYLOAD = {"jobs": [{"title": "X", "org": "Y", "location": "Seattle, WA",
                             "match_score": 5, "match_pct": 60, "url": "https://ex/x"}],
                   "metrics": {"surfaced": 1}}


def test_review_email_dev_redirects_and_tags_url(monkeypatch):
    captured = {}
    _stub_notify_deps(publish_week, captured, monkeypatch)
    publish_week.send_review_ready_email("max", "2026-08-22", "https://dash.example",
                                         _REVIEW_PAYLOAD, dev=True, dev_to="demo@dev.example")
    assert captured["to"] == "demo@dev.example"                 # redirected to the dev recipient
    assert captured["subject"].startswith("[DEV] ")
    assert "https://dash.example/?dev=1" in captured["html"]    # dashboard link marks a non-recording submit
    assert "https://dash.example/?dev=1" in captured["text"]


def test_review_email_prod_uses_config_recipient(monkeypatch):
    captured = {}
    _stub_notify_deps(publish_week, captured, monkeypatch)
    publish_week.send_review_ready_email("max", "2026-08-22", "https://dash.example",
                                         _REVIEW_PAYLOAD)
    assert captured["to"] is None                               # mailer falls back to cfg.email_to (Alex)
    assert not captured["subject"].startswith("[DEV]")
    assert "?dev=1" not in captured["html"]
    assert "https://dash.example/" in captured["html"]


def test_review_email_bug_button_threads_on_the_sent_message(monkeypatch):
    from urllib.parse import quote
    captured = {}
    _stub_notify_deps(publish_week, captured, monkeypatch)
    publish_week.send_review_ready_email("max", "2026-08-22", "https://dash.example",
                                         _REVIEW_PAYLOAD)
    mid = captured["message_id"]
    assert mid and mid.startswith("<") and mid.endswith(">")
    assert "In-Reply-To=" + quote(mid) in captured["html"]      # bug button points at THIS email
    assert "subject=" + quote("Re: " + captured["subject"]) in captured["html"]


def test_review_email_dev_without_recipient_is_skipped(monkeypatch):
    monkeypatch.delenv("WA_COPILOT_EMAIL_DEV_TO", raising=False)
    captured = {}
    _stub_notify_deps(publish_week, captured, monkeypatch)
    # dev=True but no --dev-to, no config email.dev_to, no env -> no send at all.
    publish_week.send_review_ready_email("max", "2026-08-22", "https://dash.example",
                                         _REVIEW_PAYLOAD, dev=True)
    assert captured == {}


def test_resolve_dev_recipient_precedence(monkeypatch):
    cfg = _FakeCfg()
    monkeypatch.setenv("WA_COPILOT_EMAIL_DEV_TO", "env@example.com")
    assert publish_week.resolve_dev_recipient(cfg, "cli@example.com") == "cli@example.com"
    assert publish_week.resolve_dev_recipient(cfg, None) == "env@example.com"
    monkeypatch.delenv("WA_COPILOT_EMAIL_DEV_TO", raising=False)
    assert publish_week.resolve_dev_recipient(cfg, None) == ""   # nothing configured


def test_dry_run_main_does_not_hit_network(seeded_user, data_root, week, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [
        "publish_week.py", "--user", "testuser", "--week", week,
        "--data-root", str(data_root), "--dry-run",
    ])

    def _no_network(*a, **k):
        raise AssertionError("dry-run must not call the network")

    monkeypatch.setattr(publish_week.urllib.request, "urlopen", _no_network)
    rc = publish_week.main()
    assert rc == 0
    out = capsys.readouterr().out
    assert '"week": "2026-08-01"' in out


def test_publish_failure_still_sends_review_email(seeded_user, data_root, week, monkeypatch):
    """A Worker PUT failure must NOT suppress the review email (2026-09 outage). main() should
    still call send_review_ready_email and signal the publish failure via a non-zero exit."""
    import urllib.error
    monkeypatch.setattr(sys, "argv", [
        "publish_week.py", "--user", "testuser", "--week", week,
        "--data-root", str(data_root), "--worker-url", "https://dash.example",
    ])
    monkeypatch.setenv("WA_COPILOT_PUBLISH_TOKEN", "tok")

    def _boom(*a, **k):
        raise urllib.error.HTTPError("https://dash.example/api/week", 302, "Found", {}, None)
    monkeypatch.setattr(publish_week, "publish", _boom)

    sent = {"n": 0}
    monkeypatch.setattr(publish_week, "send_review_ready_email",
                        lambda *a, **k: sent.__setitem__("n", sent["n"] + 1))

    rc = publish_week.main()
    assert sent["n"] == 1        # email sent despite the publish failure
    assert rc == 2               # but the failure is still surfaced


def test_cf_access_headers_strips_pasted_header_prefixes(monkeypatch):
    """If someone pastes the whole 'CF-Access-Client-Id: <id>' line into the secret, the raw
    token must still be sent — the header name is added by the code, not the value."""
    monkeypatch.setenv("WA_COPILOT_CF_ACCESS_CLIENT_ID", "CF-Access-Client-Id: abc123.access")
    monkeypatch.setenv("WA_COPILOT_CF_ACCESS_CLIENT_SECRET", "CF-Access-Client-Secret:  deadbeefsecret")
    assert publish_week.cf_access_headers() == {
        "CF-Access-Client-Id": "abc123.access",
        "CF-Access-Client-Secret": "deadbeefsecret",
    }
