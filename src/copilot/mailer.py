"""
Self-contained markdown -> HTML email sender.
=============================================

Adapted from the radrigs-ops send_report.py pattern, but standalone (no repo dependency) and
per-user: the From/To and credentials come from the user's config + secrets, not globals.

Sends over Gmail SMTP (587/STARTTLS). This needs ports 587/465 open outbound — fine on a normal
machine (e.g. RADMACHINE), but a locked-down cloud/serverless environment typically blocks them, so
test email sends there rather than from a cloud session.

CREDS/ADDRESSES resolve from the keyring/secrets.env/env (see config.get_secret) by ref:
    smtp_user  -> SMTP login username (Gmail: the full account address). If unset, it's derived from
                  the From/To address (Gmail plus-aliases like a+b@gmail.com log in as a@gmail.com).
    smtp_pass  -> SMTP app password (Gmail requires 2-Step Verification)
From/To come from config `email.from`/`email.to` or env WA_COPILOT_EMAIL_FROM/WA_COPILOT_EMAIL_TO.
Host/port default to Gmail; override via config `email.smtp_host` / `email.smtp_port`.

Falls back to a tiny built-in markdown converter if the optional `markdown` package is absent.
"""

from __future__ import annotations

import re
import smtplib
from email.message import EmailMessage

from .config import Config

DEFAULT_HOST = "smtp.gmail.com"
DEFAULT_PORT = 587


class MailError(RuntimeError):
    pass


def _inline(t: str) -> str:
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"`(.+?)`", r"<code>\1</code>", t)
    return t


def md_to_html(md: str) -> str:
    """Prefer the `markdown` lib; otherwise a minimal converter good enough for packets."""
    try:
        import markdown  # type: ignore
        return markdown.markdown(md, extensions=["tables", "fenced_code"])
    except ImportError:
        pass
    html, in_table, in_list = [], False, False
    for line in md.splitlines():
        s = line.rstrip()
        if s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            tag = "th" if not in_table else "td"
            if not in_table:
                html.append('<table border="1" cellpadding="6" style="border-collapse:collapse">')
                in_table = True
            html.append("<tr>" + "".join(f"<{tag}>{_inline(c)}</{tag}>" for c in cells) + "</tr>")
            continue
        elif in_table:
            html.append("</table>")
            in_table = False
        if re.match(r"^\s*[-*]\s+", s):
            if not in_list:
                html.append("<ul>")
                in_list = True
            item = _inline(re.sub(r"^\s*[-*]\s+", "", s))
            html.append(f"<li>{item}</li>")
            continue
        elif in_list:
            html.append("</ul>")
            in_list = False
        if s.startswith("### "):
            html.append(f"<h3>{_inline(s[4:])}</h3>")
        elif s.startswith("## "):
            html.append(f"<h2>{_inline(s[3:])}</h2>")
        elif s.startswith("# "):
            html.append(f"<h1>{_inline(s[2:])}</h1>")
        elif s.startswith("> "):
            html.append(f"<blockquote>{_inline(s[2:])}</blockquote>")
        elif not s.strip():
            html.append("")
        else:
            html.append(f"<p>{_inline(s)}</p>")
    if in_table:
        html.append("</table>")
    if in_list:
        html.append("</ul>")
    return "\n".join(html)


def _wrap(body_html: str) -> str:
    return (
        '<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;'
        'max-width:680px;margin:auto;color:#1c1c1c;line-height:1.5;">'
        + body_html +
        '<hr><p style="font-size:11px;color:#888;">Prepared by wa-unemployment-copilot. '
        "This tool prepares your weekly requirements; you submit and certify the claim yourself."
        "</p></div>"
    )


def _gmail_login_user(address: str, host: str) -> str:
    """Gmail login username for a From/To address: strip a plus-alias (a+tag@gmail.com -> a@gmail.com).

    Gmail authenticates the base account, not the plus-alias, so when no explicit smtp_user secret
    is set we derive the login from the address. Non-Gmail hosts get the address unchanged.
    """
    if not address or "@" not in address:
        return address
    local, domain = address.rsplit("@", 1)
    if host.endswith("gmail.com") and domain.lower() in ("gmail.com", "googlemail.com") and "+" in local:
        local = local.split("+", 1)[0]
    return f"{local}@{domain}"


def send(cfg: Config, subject: str, md_body: str, html_body: str | None = None,
         attachments: list[dict] | None = None, to: str | None = None) -> None:
    """Send an email to the user over Gmail SMTP. Raises MailError on misconfig/auth/send failure
    (including the blocked-port timeout you'll see if 587 isn't open outbound).

    `md_body` is always the text/plain part. If `html_body` is given (e.g. a fully-rendered
    template from `email_render`), it becomes the text/html alternative verbatim; otherwise the
    HTML is generated from `md_body` via the built-in markdown converter + wrapper.

    `attachments` is an optional list of {"filename": str, "content": bytes, "mimetype": "type/sub"}
    (e.g. the appended submitted_jobs.csv). EmailMessage restructures to multipart/mixed as needed.

    `to` overrides the recipient (config `email.to`) for this one message — used by the dev-test
    review send to redirect the email to the operator instead of the prod user, without touching
    config. The From/login/credentials are unchanged.
    """
    to = to or cfg.email_to
    if not to:
        raise MailError("email.to is not set (config email.to or env WA_COPILOT_EMAIL_TO).")

    host = cfg.get("email.smtp_host", DEFAULT_HOST)
    port = int(cfg.get("email.smtp_port", DEFAULT_PORT))
    sender = cfg.email_from or cfg.get_secret("smtp_user") or to
    user = cfg.get_secret("smtp_user") or _gmail_login_user(sender, host)
    pw = cfg.get_secret("smtp_pass")
    if not user or not pw:
        raise MailError(
            "SMTP credentials missing. Store them with: "
            f"python scripts/setup_user.py --user {cfg.user} --set-secret smtp_user "
            "(and --set-secret smtp_pass), or set WA_COPILOT_SMTP_USER / "
            "WA_COPILOT_GMAIL_APP_PASSWORD in the environment."
        )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg.set_content(md_body)
    msg.add_alternative(html_body if html_body is not None else _wrap(md_to_html(md_body)),
                        subtype="html")
    for att in (attachments or []):
        maintype, _, subtype = (att.get("mimetype") or "application/octet-stream").partition("/")
        content = att.get("content", b"")
        if isinstance(content, str):
            content = content.encode("utf-8")
        msg.add_attachment(content, maintype=maintype, subtype=subtype or "octet-stream",
                           filename=att.get("filename", "attachment"))

    try:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls()
            s.login(user, pw)
            s.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        raise MailError("SMTP auth failed — check the app password and 2-Step Verification.") from e
    except Exception as e:  # noqa: BLE001
        raise MailError(f"SMTP send failed: {type(e).__name__}: {e}") from e
