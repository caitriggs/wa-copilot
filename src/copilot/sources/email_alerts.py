"""
Job-alert email ingest (covers Indeed + LinkedIn + WorkSourceWA, compliantly).
==============================================================================

Indeed and LinkedIn no longer offer a usable job-search API or RSS to individuals, so the
reliable, ToS-respectful way to cover them is their own **job-alert emails**: you create a saved
search on each site, they email you matching postings, and this source reads those alert emails
from your Gmail over IMAP and turns them into JobPosting objects.

SETUP (per user)
    1. Create saved-search job alerts on Indeed / LinkedIn / WorkSourceWA (email alerts on).
    2. Store an app password for IMAP (Gmail: same app password you use for SMTP works):
         python scripts/setup_user.py --user <you> --set-secret smtp_user   # your gmail address
         python scripts/setup_user.py --user <you> --set-secret smtp_pass   # app password
       (or set imap_user / imap_pass explicitly if different).
    3. Enable in config:  sources.email_alerts.enabled: true

Reads recent messages from known alert senders, extracts the job links + titles (and best-effort
employer/location), and returns them. Credentials are never logged. If creds are missing or IMAP
is unreachable, it returns [] with a note rather than aborting the weekly run.
"""

from __future__ import annotations

import email
import imaplib
import re
from datetime import date, timedelta
from email.header import decode_header
from html.parser import HTMLParser

from ..models import JobPosting

# Which "From" addresses/domains each provider sends job alerts from.
PROVIDER_SENDERS = {
    "linkedin": ["jobalerts-noreply@linkedin.com", "jobs-noreply@linkedin.com",
                 "jobs-listings@linkedin.com"],
    "indeed": ["alert@indeed.com", "invite@indeed.com", "noreply@indeed.com",
               "donotreply@indeed.com"],
    "worksourcewa": ["noreply@worksourcewa.com", "no-reply@worksourcewa.com"],
}

# The job-posting URL shape for each provider (used to pick real job links out of an email).
PROVIDER_URL_RE = {
    "linkedin": re.compile(r"linkedin\.com/(?:comm/)?jobs/view/", re.I),
    "indeed": re.compile(r"indeed\.com/(?:rc/clk|viewjob|pagead/clk|job/|jobs\?)", re.I),
    "worksourcewa": re.compile(r"worksourcewa\.com/.*(?:job|listing|jobs)", re.I),
}


class _AnchorContext(HTMLParser):
    """Collect (href, anchor_text, following_text) tuples from an HTML email."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.anchors: list[list] = []          # [href, text, follow]
        self._in_a = False
        self._href = ""
        self._buf = []
        self._awaiting_follow = False

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._in_a = True
            self._href = dict(attrs).get("href", "") or ""
            self._buf = []
            self._awaiting_follow = False

    def handle_endtag(self, tag):
        if tag == "a" and self._in_a:
            text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            self.anchors.append([self._href, text, ""])
            self._in_a = False
            self._awaiting_follow = True

    def handle_data(self, data):
        if self._in_a:
            self._buf.append(data)
        elif self._awaiting_follow:
            chunk = re.sub(r"\s+", " ", data).strip()
            if chunk:
                self.anchors[-1][2] = chunk
                self._awaiting_follow = False


def _split_company_location(follow: str) -> tuple[str, str]:
    """Alert emails often render 'Company · Location' after the title link."""
    if not follow:
        return "", ""
    for sep in ("·", "•", " - ", " — ", "|"):
        if sep in follow:
            a, b = follow.split(sep, 1)
            return a.strip(), b.strip()
    return follow.strip(), ""


def parse_alert_email(provider: str, html: str, keywords=None) -> list[JobPosting]:
    """Extract postings from one alert email's HTML. Pure function — unit-tested with fixtures."""
    url_re = PROVIDER_URL_RE.get(provider)
    if not url_re or not html:
        return []
    p = _AnchorContext()
    try:
        p.feed(html)
    except Exception:
        return []

    out: list[JobPosting] = []
    seen = set()
    for href, text, follow in p.anchors:
        if not href or not url_re.search(href):
            continue
        title = text.strip()
        if len(title) < 2 or title.lower() in ("view job", "apply", "see job", "view", "apply now"):
            continue
        company, location = _split_company_location(follow)
        # Dedup within the email by the URL path (drops tracking query strings).
        job_id = re.split(r"[?#]", href, 1)[0].rstrip("/").rsplit("/", 1)[-1]
        key = (title.lower(), job_id.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(JobPosting(
            source=f"email:{provider}",
            title=title,
            employer=company,
            location=location,
            url=href,
            keywords=list(keywords or []),
        ))
    return out


def _decoded_subject(msg) -> str:
    parts = decode_header(msg.get("Subject", "") or "")
    return "".join(
        (b.decode(enc or "utf-8", "replace") if isinstance(b, bytes) else b)
        for b, enc in parts
    )


def _html_body(msg) -> str:
    """Best HTML body from a possibly-multipart message (falls back to text)."""
    html, text = "", ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if part.get("Content-Disposition", "").startswith("attachment"):
                continue
            try:
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                body = payload.decode(part.get_content_charset() or "utf-8", "replace")
            except Exception:
                continue
            if ctype == "text/html" and not html:
                html = body
            elif ctype == "text/plain" and not text:
                text = body
    else:
        try:
            html = (msg.get_payload(decode=True) or b"").decode(
                msg.get_content_charset() or "utf-8", "replace")
        except Exception:
            html = ""
    return html or text


def fetch(cfg) -> list[JobPosting]:
    conf = cfg.get("sources.email_alerts", {}) or {}
    if not conf.get("enabled", False):
        return []

    user = cfg.get_secret("imap_user") or cfg.get_secret("smtp_user")
    pw = cfg.get_secret("imap_pass") or cfg.get_secret("smtp_pass")
    if not user or not pw:
        print("  [email_alerts] no IMAP credentials; skipping. Store smtp_user/smtp_pass "
              f"(or imap_user/imap_pass) via setup_user.py --user {cfg.user} --set-secret smtp_pass")
        return []

    host = conf.get("imap_host", "imap.gmail.com")
    mailbox = conf.get("mailbox", "INBOX")
    since_days = int(conf.get("since_days", 7))
    providers = conf.get("providers", ["indeed", "linkedin", "worksourcewa"])
    keywords = cfg.get("search.keywords_include", []) or []
    since = (date.today() - timedelta(days=since_days)).strftime("%d-%b-%Y")

    out: list[JobPosting] = []
    try:
        M = imaplib.IMAP4_SSL(host)
        M.login(user, pw)
        M.select(mailbox, readonly=True)
    except Exception as e:  # noqa: BLE001
        print(f"  [email_alerts] IMAP connect/login failed: {type(e).__name__}: {e}")
        return []

    try:
        for provider in providers:
            for sender in PROVIDER_SENDERS.get(provider, []):
                try:
                    typ, data = M.search(None, "SINCE", since, "FROM", sender)
                    if typ != "OK":
                        continue
                    ids = data[0].split()
                except Exception as e:  # noqa: BLE001
                    print(f"  [email_alerts] search {sender} failed: {type(e).__name__}")
                    continue
                for num in ids[-25:]:  # cap per sender
                    try:
                        typ, msg_data = M.fetch(num, "(RFC822)")
                        if typ != "OK" or not msg_data or not msg_data[0]:
                            continue
                        msg = email.message_from_bytes(msg_data[0][1])
                        out.extend(parse_alert_email(provider, _html_body(msg), keywords))
                    except Exception as e:  # noqa: BLE001
                        print(f"  [email_alerts] fetch/parse failed: {type(e).__name__}")
    finally:
        try:
            M.logout()
        except Exception:
            pass

    # de-dup across emails by (title, employer)
    uniq = {}
    for jp in out:
        uniq.setdefault(jp.dedup_key, jp)
    result = list(uniq.values())
    print(f"  [email_alerts] {len(result)} postings from alert emails "
          f"({', '.join(providers)})")
    return result
