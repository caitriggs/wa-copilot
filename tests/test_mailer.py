"""Tests for src/copilot/mailer.py — Gmail SMTP send + env-var credential resolution.

No real network and no real Desktop: SMTP is stubbed at smtplib.SMTP, and credentials/addresses
come from monkeypatched env vars.
"""

import os

import pytest

from copilot import config as config_mod
from copilot.config import Config
from copilot.mailer import MailError, send, _gmail_login_user


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Clear any ambient WA_COPILOT_* vars so tests are hermetic even when a real cloud session
    (which sets these) is running the suite."""
    for k in list(os.environ):
        if k.startswith("WA_COPILOT_"):
            monkeypatch.delenv(k, raising=False)


def _cfg(data=None):
    # Minimal in-memory Config (no config.yaml on disk) — mirrors a cloud/CI session.
    return Config("testuser", data or {}, data_root=None)


# --------------------------------------------------------------------------- env-var resolution


def test_get_secret_reads_gmail_app_password_env(monkeypatch, tmp_path):
    monkeypatch.setattr(config_mod, "keyring", None)  # no keyring backend
    monkeypatch.setenv("WA_COPILOT_GMAIL_APP_PASSWORD", "app-pw-123")
    assert config_mod.get_secret("testuser", "smtp_pass", data_root=tmp_path) == "app-pw-123"


def test_get_secret_generic_env_alias(monkeypatch, tmp_path):
    # Any ref resolves from the generic WA_COPILOT_<REF> name.
    monkeypatch.setattr(config_mod, "keyring", None)
    monkeypatch.setenv("WA_COPILOT_USAJOBS", "key-xyz")
    assert config_mod.get_secret("testuser", "usajobs", data_root=tmp_path) == "key-xyz"


def test_email_fields_fall_back_to_env(monkeypatch):
    monkeypatch.setenv("WA_COPILOT_EMAIL_TO", "to@example.com")
    monkeypatch.setenv("WA_COPILOT_EMAIL_FROM", "from+tag@gmail.com")
    cfg = _cfg()
    assert cfg.email_to == "to@example.com"
    assert cfg.email_from == "from+tag@gmail.com"


def test_config_value_beats_env(monkeypatch):
    monkeypatch.setenv("WA_COPILOT_EMAIL_TO", "env@example.com")
    cfg = _cfg({"email": {"to": "config@example.com"}})
    assert cfg.email_to == "config@example.com"


# --------------------------------------------------------------------------- Gmail login derivation


def test_gmail_login_strips_plus_alias():
    assert _gmail_login_user("demo+jobs@gmail.com", "smtp.gmail.com") == "demo@gmail.com"


def test_gmail_login_plain_address_unchanged():
    assert _gmail_login_user("demo@gmail.com", "smtp.gmail.com") == "demo@gmail.com"


def test_non_gmail_host_keeps_plus():
    assert _gmail_login_user("a+b@corp.com", "smtp.corp.com") == "a+b@corp.com"


# --------------------------------------------------------------------------- SMTP transport


class _FakeSMTP:
    last = None

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.logged_in = None
        self.sent = None
        _FakeSMTP.last = self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        self.tls = True

    def login(self, user, pw):
        self.logged_in = (user, pw)

    def send_message(self, msg):
        self.sent = msg


def test_smtp_send_uses_env_creds_and_derives_login(monkeypatch):
    monkeypatch.setattr(config_mod, "keyring", None)
    monkeypatch.setenv("WA_COPILOT_EMAIL_TO", "demo@gmail.com")
    monkeypatch.setenv("WA_COPILOT_EMAIL_FROM", "demo+jobs@gmail.com")
    monkeypatch.setenv("WA_COPILOT_GMAIL_APP_PASSWORD", "app-pw")
    import copilot.mailer as mailer
    monkeypatch.setattr(mailer.smtplib, "SMTP", _FakeSMTP)

    send(_cfg(), "Subject line", "**Hi** there")

    s = _FakeSMTP.last
    assert s.host == "smtp.gmail.com" and s.port == 587
    assert s.logged_in == ("demo@gmail.com", "app-pw")  # +alias stripped for login
    assert s.sent["From"] == "demo+jobs@gmail.com"
    assert s.sent["To"] == "demo@gmail.com"
    assert s.sent["Subject"] == "Subject line"
    # both plain + html alternatives present
    types = {p.get_content_type() for p in s.sent.iter_parts()}
    assert {"text/plain", "text/html"} <= types


def test_html_body_used_verbatim(monkeypatch):
    monkeypatch.setattr(config_mod, "keyring", None)
    monkeypatch.setenv("WA_COPILOT_EMAIL_TO", "demo@gmail.com")
    monkeypatch.setenv("WA_COPILOT_EMAIL_FROM", "demo@gmail.com")
    monkeypatch.setenv("WA_COPILOT_GMAIL_APP_PASSWORD", "app-pw")
    import copilot.mailer as mailer
    monkeypatch.setattr(mailer.smtplib, "SMTP", _FakeSMTP)

    send(_cfg(), "Subj", "plain text fallback", html_body="<div>RENDERED &amp; ready</div>")

    sent = _FakeSMTP.last.sent
    html = next(p for p in sent.iter_parts() if p.get_content_type() == "text/html")
    plain = next(p for p in sent.iter_parts() if p.get_content_type() == "text/plain")
    assert "<div>RENDERED" in html.get_content()      # used verbatim, not markdown-converted
    assert "plain text fallback" in plain.get_content()


def test_attachments_added(monkeypatch):
    monkeypatch.setattr(config_mod, "keyring", None)
    monkeypatch.setenv("WA_COPILOT_EMAIL_TO", "demo@gmail.com")
    monkeypatch.setenv("WA_COPILOT_EMAIL_FROM", "demo@gmail.com")
    monkeypatch.setenv("WA_COPILOT_GMAIL_APP_PASSWORD", "app-pw")
    import copilot.mailer as mailer
    monkeypatch.setattr(mailer.smtplib, "SMTP", _FakeSMTP)

    send(_cfg(), "Subj", "text", html_body="<div>x</div>",
         attachments=[{"filename": "submitted_jobs.csv", "content": b"date,job_title\n2026,SDET\n",
                       "mimetype": "text/csv"}])

    atts = list(_FakeSMTP.last.sent.iter_attachments())
    assert len(atts) == 1
    assert atts[0].get_filename() == "submitted_jobs.csv"
    assert atts[0].get_content_type() == "text/csv"


def test_to_override_wins_over_config(monkeypatch):
    # The dev-test review send passes to= to redirect a single message to the operator without
    # touching config; From/login are unchanged.
    monkeypatch.setattr(config_mod, "keyring", None)
    monkeypatch.setenv("WA_COPILOT_GMAIL_APP_PASSWORD", "app-pw")
    import copilot.mailer as mailer
    monkeypatch.setattr(mailer.smtplib, "SMTP", _FakeSMTP)

    send(_cfg({"email": {"to": "prod@example.com", "from": "sender@gmail.com"}}), "Subj", "body",
         to="dev@example.com")

    sent = _FakeSMTP.last.sent
    assert sent["To"] == "dev@example.com"        # override recipient
    assert sent["From"] == "sender@gmail.com"     # From unchanged


def test_smtp_missing_password_raises(monkeypatch):
    monkeypatch.setattr(config_mod, "keyring", None)
    monkeypatch.setenv("WA_COPILOT_EMAIL_TO", "demo@gmail.com")
    monkeypatch.setenv("WA_COPILOT_EMAIL_FROM", "demo@gmail.com")
    # no WA_COPILOT_GMAIL_APP_PASSWORD
    with pytest.raises(MailError, match="SMTP credentials missing"):
        send(_cfg(), "s", "b")


def test_no_recipient_raises():
    with pytest.raises(MailError, match="email.to is not set"):
        send(_cfg(), "s", "b")
