"""
Weekly-review HTML email template.
==================================

Renders the "your ranked jobs are ready to review" email as a self-contained, email-client-safe
HTML document — the periwinkle drafting-surface design (graph-paper bands, monospace headings, a
surfaced-jobs stat, match cards, a deep-indigo REVIEW & APPLY button).

Design constraints for real email clients (Gmail web/iOS/Android especially):
  * Table-based layout, everything styled INLINE — no <style> blocks, no external CSS/fonts.
  * Solid `bgcolor` fills everywhere; the graph-paper texture is a background-image (SVG data URI)
    layered OVER a solid periwinkle fallback, so it simply degrades to flat periwinkle where
    background images are stripped.
  * Monospace/sans use font *stacks* (no web fonts) — the drafting look survives on the mono
    fallback, and body copy stays legible on the sans fallback.

This module is PURE and deterministic: callers pass already-computed values (the "posted N days
ago" label, the match figure, etc.), so nothing here depends on the wall clock. `mailer.send`
takes the returned HTML as the message's HTML alternative; `weekly_review_plaintext` builds the
matching text/plain part.
"""

from __future__ import annotations

import html
from urllib.parse import quote

# --- palette (the drafting surface) ----------------------------------------------------------
PAGE_BG = "#E7E9F8"      # light periwinkle page
HERO_BG = "#DADDF4"      # slightly deeper periwinkle hero/stat panels
CARD_BG = "#F4F5FD"      # near-white lavender cards
CARD_BORDER = "#C9CEEE"  # thin periwinkle card rule
GRID_LINE = "#CDD1EF"    # graph-paper lines
INK = "#23264D"          # deep indigo ink (headings/body)
MUTED = "#6E739B"        # periwinkle-gray secondary text
LABEL = "#5B60A6"        # letter-spaced mono labels
ACCENT = "#2E2FA6"       # deep indigo (big number + button)
ON_ACCENT = "#FFFFFF"

MONO = "'SFMono-Regular','DejaVu Sans Mono','Menlo','Consolas','Courier New',monospace"
SANS = "-apple-system,'Segoe UI',Helvetica,Arial,sans-serif"

_DEFAULT_FOOTER = "Job Search Copilot"


def _graph_paper_uri() -> str:
    """A 24px graph-paper tile as an SVG data URI (periwinkle ground + one grid corner)."""
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' width='24' height='24'>"
        f"<rect width='24' height='24' fill='{PAGE_BG}'/>"
        f"<path d='M24 0H0V24' fill='none' stroke='{GRID_LINE}' stroke-width='1'/>"
        "</svg>"
    )
    return "data:image/svg+xml;utf8," + quote(svg)


def _esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def _band() -> str:
    uri = _graph_paper_uri()
    return (
        f'<tr><td height="56" bgcolor="{PAGE_BG}" '
        f'style="background-image:url(\'{uri}\');background-repeat:repeat;height:56px;'
        f'line-height:56px;font-size:0;">&nbsp;</td></tr>'
    )


def _label(text: str, color: str = LABEL, size: int = 11) -> str:
    return (
        f'<span style="font-family:{MONO};font-size:{size}px;letter-spacing:2px;'
        f'text-transform:uppercase;color:{color};">{_esc(text)}</span>'
    )


def _resume_pill(label: str) -> str:
    """Small badge naming the résumé a posting best matched (multi-resume), beside the match."""
    return (
        f'<span style="display:inline-block;font-family:{MONO};font-size:10px;letter-spacing:1px;'
        f'text-transform:uppercase;color:{ACCENT};background:{HERO_BG};border-radius:5px;'
        f'padding:2px 8px;vertical-align:middle;">{_esc(label)}</span>'
    )


def _card(job: dict) -> str:
    """One match card. job keys: title, org, location, mode, match_pct, match_reason, resume,
    posted_label, blurb, url."""
    top_bits = []
    if job.get("match_pct") is not None:
        top_bits.append(f"{int(job['match_pct'])}% match")
    if job.get("match_reason"):
        top_bits.append(str(job["match_reason"]))   # WHY it scored this way, beside the score
    top = " · ".join(top_bits)

    sub_bits = [b for b in (job.get("org"), job.get("location"), job.get("mode"),
                            job.get("posted_label")) if b]
    sub = " · ".join(sub_bits)

    title = _esc(job.get("title", "Untitled role"))
    url = job.get("url", "")
    title_html = (
        f'<a href="{_esc(url)}" style="color:{INK};text-decoration:none;">{title}</a>'
        if url else title
    )

    rows = []
    if top or job.get("resume"):
        chip = _label(top, MUTED) if top else ""
        pill = _resume_pill(job["resume"]) if job.get("resume") else ""
        sep = "&nbsp;&nbsp;" if chip and pill else ""
        rows.append(f'<tr><td style="padding:0 0 7px 0;">{chip}{sep}{pill}</td></tr>')
    rows.append(
        f'<tr><td style="padding:0 0 6px 0;font-family:{MONO};font-size:19px;'
        f'font-weight:700;color:{INK};line-height:1.25;">{title_html}</td></tr>'
    )
    if sub:
        rows.append(
            f'<tr><td style="padding:0 0 10px 0;font-family:{MONO};font-size:12px;'
            f'color:{MUTED};letter-spacing:0.5px;">{_esc(sub)}</td></tr>'
        )
    if job.get("blurb"):
        rows.append(
            f'<tr><td style="font-family:{SANS};font-size:14px;color:{INK};'
            f'line-height:1.55;">{_esc(job["blurb"])}</td></tr>'
        )

    return (
        f'<tr><td style="padding:0 0 14px 0;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'bgcolor="{CARD_BG}" style="background:{CARD_BG};border:1px solid {CARD_BORDER};">'
        f'<tr><td style="padding:20px 22px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        f'{"".join(rows)}</table>'
        f'</td></tr></table>'
        f'</td></tr>'
    )


def _bug_mailto(reply_to: str, week: str) -> str:
    """A `mailto:` that opens a reply to this email's sender, prefilled for a bug report — the
    "Report a bug" button. Reaches the operator's inbox the same way a plain Reply would."""
    subject = f"Bug report — Job Search Copilot (week ending {week})"
    body = "What went wrong? (Attach a screenshot if you can.)\n\n"
    return f"mailto:{reply_to}?subject={quote(subject)}&body={quote(body)}"


def render_weekly_review(*, week: str, surfaced: int, jobs: list[dict], review_url: str,
                         shown: int | None = None, footer_address: str | None = None,
                         reply_to: str | None = None) -> str:
    """Full HTML document for the weekly-review email.

    jobs: already-ranked view models (see `_card`). `shown` defaults to len(jobs); `surfaced` is the
    total that cleared filters (may exceed shown). `review_url` is the dashboard link. `reply_to`,
    when set, is the sender address the "Report a bug" button opens a reply to (defaults to just
    telling the reader to reply to the email).
    """
    shown = len(jobs) if shown is None else shown
    footer = footer_address or _DEFAULT_FOOTER
    review_url = review_url or "#"

    # Graph-paper grid is the background behind the whole content column (so it shows around and
    # between the cards). Solid PAGE_BG stays as the `bgcolor` fallback for clients that strip
    # background images, so it degrades to flat periwinkle rather than white.
    uri = _graph_paper_uri()
    grid = f"background-color:{PAGE_BG};background-image:url('{uri}');background-repeat:repeat;"

    # "Report a bug" button under the main CTA — a mailto reply to the sender. Only rendered when we
    # know the reply address; the footer note below always tells the reader they can just reply.
    bug_button = ""
    if reply_to:
        bug_button = (
            f'<tr><td bgcolor="{PAGE_BG}" style="{grid}padding:2px 40px 6px 40px;">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
            f'<tr><td bgcolor="{CARD_BG}" align="center" style="background:{CARD_BG};border:1px solid {CARD_BORDER};">'
            f'<a href="{_esc(_bug_mailto(reply_to, week))}" style="display:block;padding:12px 24px;'
            f'font-family:{MONO};font-size:12px;font-weight:700;letter-spacing:2px;'
            f'text-transform:uppercase;color:{ACCENT};text-decoration:none;">🐛 Report a bug</a>'
            f'</td></tr></table></td></tr>'
        )

    # Intro sentence adapts to how many were surfaced vs. highlighted.
    plural = "s" if surfaced != 1 else ""
    if surfaced > shown:
        intro = (f"{surfaced} role{plural} cleared your filters this week. "
                 f"The top {shown} are highlighted below — the rest are waiting in your queue.")
    else:
        intro = f"{surfaced} role{plural} cleared your filters this week, highlighted below."

    cards = "".join(_card(j) for j in jobs) or (
        f'<tr><td style="font-family:{SANS};font-size:14px;color:{MUTED};padding:0 0 14px 0;">'
        f'No roles cleared your filters this week — open the dashboard to adjust your search.'
        f'</td></tr>'
    )

    top_label = _label(f"Top {shown} of {surfaced}") if surfaced > shown else _label("This week")

    return f"""\
<!-- weekly-review -->
<div style="margin:0;padding:0;{grid}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{PAGE_BG}" style="{grid}">
<tr><td align="center" style="padding:0;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:600px;">

{_band()}

<!-- hero -->
<tr><td bgcolor="{HERO_BG}" style="background:{HERO_BG};padding:34px 40px 30px 40px;">
  <div style="padding:0 0 10px 0;">{_label("Job Search Copilot")}</div>
  <div style="font-family:{MONO};font-size:38px;font-weight:700;color:{INK};line-height:1.1;letter-spacing:-0.5px;">Weekly Review</div>
  <div style="font-family:{MONO};font-size:13px;color:{MUTED};padding:10px 0 0 0;letter-spacing:1px;">Week ending {_esc(week)}</div>
</td></tr>

<!-- stat -->
<tr><td bgcolor="{PAGE_BG}" style="{grid}padding:26px 40px 8px 40px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
  <tr>
    <td width="40%" valign="top" style="border-right:1px solid {CARD_BORDER};padding-right:22px;">
      <div style="font-family:{MONO};font-size:56px;font-weight:700;color:{ACCENT};line-height:1;">{int(surfaced)}</div>
      <div style="padding:14px 0 0 0;">{_label("Surfaced jobs")}</div>
    </td>
    <td valign="middle" style="padding-left:24px;font-family:{SANS};font-size:15px;color:{INK};line-height:1.55;">{_esc(intro)}</td>
  </tr>
  </table>
</td></tr>

<!-- cards -->
<tr><td bgcolor="{PAGE_BG}" style="{grid}padding:22px 40px 4px 40px;">
  <div style="padding:0 0 14px 0;">{top_label}</div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{cards}</table>
</td></tr>

<!-- cta -->
<tr><td bgcolor="{PAGE_BG}" style="{grid}padding:10px 40px 6px 40px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
  <tr><td bgcolor="{ACCENT}" align="center" style="background:{ACCENT};">
    <a href="{_esc(review_url)}" style="display:block;padding:18px 24px;font-family:{MONO};font-size:15px;font-weight:700;letter-spacing:3px;text-transform:uppercase;color:{ON_ACCENT};text-decoration:none;">Review &amp; Apply</a>
  </td></tr>
  </table>
</td></tr>
{bug_button}

{_band()}

<!-- footer -->
<tr><td bgcolor="{PAGE_BG}" style="{grid}padding:22px 40px 34px 40px;text-align:center;font-family:{SANS};font-size:12px;color:{MUTED};line-height:1.7;">
  {_esc(footer)}<br>
  Something look wrong or off? Just reply to this email to report it — screenshots welcome.<br>
  This tool prepares your weekly requirements; you submit and certify the claim yourself.
</td></tr>

</table>
</td></tr>
</table>
</div>
"""


def weekly_review_plaintext(*, week: str, surfaced: int, jobs: list[dict], review_url: str) -> str:
    """text/plain alternative — the same information without markup."""
    lines = [f"Job Search Copilot — Weekly Review (week ending {week})", ""]
    plural = "s" if surfaced != 1 else ""
    lines.append(f"{surfaced} role{plural} cleared your filters this week.")
    lines.append("")
    for i, j in enumerate(jobs, 1):
        head = j.get("title", "Untitled role")
        if j.get("match_pct") is not None:
            head = f"{int(j['match_pct'])}% match — {head}"
        if j.get("resume"):
            head = f"{head}  [{j['resume']}]"
        lines.append(f"{i}. {head}")
        sub = " · ".join(b for b in (j.get("org"), j.get("location"), j.get("mode"),
                                     j.get("posted_label")) if b)
        if sub:
            lines.append(f"   {sub}")
        if j.get("match_reason"):
            lines.append(f"   why: {j['match_reason']}")
        if j.get("blurb"):
            lines.append(f"   {j['blurb']}")
        if j.get("url"):
            lines.append(f"   {j['url']}")
        lines.append("")
    lines.append(f"Review & approve: {review_url}")
    lines.append("")
    lines.append("Something look wrong or off? Just reply to this email to report it — screenshots welcome.")
    lines.append("This tool prepares your weekly requirements; you submit and certify the claim yourself.")
    return "\n".join(lines)


# ============================================================================ apply email
# The BACKUP "ready to apply" email, sent RADMACHINE-side after you approve picks on the dashboard
# (see scripts/fetch_approvals.py) in case you don't check the dashboard. It lists the selected
# jobs, links to the submissions CSV on the dashboard Worker (GET /api/approvals/csv — the single
# source of truth, not a separate attached copy), and offers two ways to apply — by hand, or with a
# copyable Claude CoWork prompt. That prompt runs on the RECIPIENT's own desktop/CoWork login (e.g.
# Alex — who has no RADMACHINE access), so it only ever references things they can set up themselves:
# the CSV as their browser downloads it (~/Downloads/) and a résumé folder on their own Desktop.


def build_cowork_prompt(*, week: str, applicant: dict,
                        csv_filename: str = "submitted_jobs.csv",
                        resume_filenames: list[str] | None = None) -> str:
    """A copyable prompt for Claude CoWork running on the JOB SEEKER'S OWN desktop (their own
    CoWork login — not RADMACHINE, which they have no access to) to fill each application for
    review. Fills from the applicant's own details/résumé and STOPS before submitting — the human
    reviews and submits. Pure/deterministic (no wall clock).

    Because this runs on the seeker's own machine, every path referenced must be something THEY
    can act on directly: the CSV in their own Downloads folder and a résumé folder on their own
    Desktop — never a RADMACHINE absolute path (`<Desktop>/wa-unemployment-copilot/<user>/...`),
    which doesn't exist for them, and never "the attached file" / "this email", which CoWork
    running standalone has no notion of. Résumé filenames aren't required to match exactly —
    whatever the seeker already has saved there is fine; CoWork is expected to pick the résumé
    whose title/content best fits each job (the CSV's resume_file column is offered only as a
    hint, not a strict requirement).
    """
    a = applicant or {}
    links = a.get("links", {}) or {}
    line = lambda label, val: f"     - {label}: {val}" if val else ""
    details = "\n".join(x for x in [
        line("Name", a.get("full_name")),
        line("Email", a.get("email")),
        line("Phone", a.get("phone")),
        line("Location", a.get("location")),
        line("Work authorization", a.get("work_authorization")),
        line("Willing to relocate", a.get("willing_to_relocate")),
        line("Notice period", a.get("notice_period")),
        line("LinkedIn", links.get("linkedin")),
        line("GitHub", links.get("github")),
        line("Portfolio", links.get("portfolio")),
    ] if x)

    resumes = list(dict.fromkeys(resume_filenames or []))   # dedupe, keep order
    resume_dir = "~/Desktop/wa-unemployment-copilot/"
    resume_hint = (
        f" (the CSV's resume_file column names which résumé we matched to that job — e.g. "
        f"{', '.join(resumes)} — use it as a hint if it helps, but match by title/content; the "
        f"filenames in that folder don't need to match exactly)"
        if resumes else ""
    )

    return (
        f"You are running in Claude CoWork on MY OWN desktop with browser access (my own CoWork "
        f"login, not a shared machine). Help me apply to the jobs I selected for the week ending "
        f"{week}.\n\n"
        f"The job list is at ~/Downloads/{csv_filename} on this computer.\n\n"
        f"My résumé(s) are saved in {resume_dir} — there may be more than one, each geared toward "
        f"a different type of role.\n\n"
        f"Open {csv_filename} and, for EACH row whose status is \"to apply\":\n"
        f"  1. Open its application_link in the browser.\n"
        f"  2. Fill the application form using my details:\n{details}\n"
        f"     - Résumé: from {resume_dir}, pick whichever résumé best fits this job's title and "
        f"content{resume_hint}.\n"
        f"     Tailor answers to the role using the posting's own text for screening questions.\n"
        f"  3. STOP before submitting and let me review the filled form — I click submit myself.\n"
        f"  4. After I confirm I submitted, set that row's status to \"applied\" (with today's date) "
        f"in the CSV.\n\n"
        f"Only use facts from my résumé — never invent experience, and never submit without my ok."
    )


def render_apply_email(*, week: str, jobs: list[dict], review_url: str, cowork_prompt: str,
                       csv_url: str, csv_filename: str = "submitted_jobs.csv",
                       footer_address: str | None = None) -> str:
    """HTML for the 'ready to apply' backup email. jobs: [{title, org, url}].

    The submissions CSV is NOT attached — it's downloaded from `csv_url` (the dashboard Worker's
    `/api/approvals/csv`, rebuilt live from the current picks), the single source of truth the
    dashboard's Download button uses too. This email is the "in case he doesn't check the
    dashboard" backup, so it stays self-sufficient: the picks, a CSV download button, and the
    copyable CoWork prompt are all here."""
    footer = footer_address or _DEFAULT_FOOTER
    uri = _graph_paper_uri()
    grid = f"background-color:{PAGE_BG};background-image:url('{uri}');background-repeat:repeat;"
    n = len(jobs)

    rows = ""
    for j in jobs:
        title = _esc(j.get("title", "Untitled role"))
        url = j.get("url", "")
        title_html = (f'<a href="{_esc(url)}" style="color:{INK};text-decoration:none;">{title}</a>'
                      if url else title)
        sub = _esc(" · ".join(b for b in (j.get("org"), j.get("location")) if b))
        link = (f'<a href="{_esc(url)}" style="color:{ACCENT};text-decoration:none;font-family:{SANS};'
                f'font-size:13px;">Open application →</a>' if url else "")
        rows += (
            f'<tr><td style="padding:0 0 12px 0;"><table role="presentation" width="100%" '
            f'cellpadding="0" cellspacing="0" border="0" bgcolor="{CARD_BG}" '
            f'style="background:{CARD_BG};border:1px solid {CARD_BORDER};"><tr><td '
            f'style="padding:14px 18px;">'
            f'<div style="font-family:{MONO};font-size:16px;font-weight:700;color:{INK};">{title_html}</div>'
            + (f'<div style="font-family:{MONO};font-size:12px;color:{MUTED};padding:3px 0 6px 0;">{sub}</div>' if sub else "")
            + link + '</td></tr></table></td></tr>'
        )

    return f"""\
<div style="margin:0;padding:0;{grid}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{PAGE_BG}" style="{grid}">
<tr><td align="center" style="padding:0;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:600px;">

<tr><td height="40" style="{grid}line-height:40px;font-size:0;">&nbsp;</td></tr>

<tr><td bgcolor="{HERO_BG}" style="background:{HERO_BG};padding:30px 40px 26px 40px;">
  <div style="padding:0 0 10px 0;">{_label(footer)}</div>
  <div style="font-family:{MONO};font-size:32px;font-weight:700;color:{INK};line-height:1.1;">Ready to apply</div>
  <div style="font-family:{MONO};font-size:13px;color:{MUTED};padding:10px 0 0 0;letter-spacing:1px;">Week ending {_esc(week)} · {n} job{'s' if n != 1 else ''}</div>
</td></tr>

<tr><td style="{grid}padding:24px 40px 6px 40px;font-family:{SANS};font-size:15px;color:{INK};line-height:1.55;">
  These are the {n} job{'s' if n != 1 else ''} you approved. Download your running submissions log
  (<strong>{_esc(csv_filename)}</strong>) below — use it to fill in your weekly job-search activity
  for the unemployment claim. It's rebuilt live from your current picks, so it always reflects the
  latest.
</td></tr>

<tr><td style="{grid}padding:14px 40px 4px 40px;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0">
  <tr><td bgcolor="{CARD_BG}" align="center" style="background:{CARD_BG};border:1px solid {ACCENT};">
    <a href="{_esc(csv_url)}" style="display:block;padding:14px 22px;font-family:{MONO};font-size:13px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:{ACCENT};text-decoration:none;">Download submissions CSV</a>
  </td></tr>
  </table>
</td></tr>

<tr><td style="{grid}padding:16px 40px 4px 40px;">
  <div style="padding:0 0 12px 0;">{_label("Your picks")}</div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{rows}</table>
</td></tr>

<tr><td style="{grid}padding:12px 40px 4px 40px;">
  <div style="padding:0 0 10px 0;">{_label("How to apply")}</div>
  <div style="font-family:{SANS};font-size:14px;color:{INK};line-height:1.6;">
    Apply to each one yourself from the links above — <strong>or</strong> let <strong>Claude CoWork</strong>
    on your desktop fill the forms for you. Copy the prompt below into CoWork; it fills each application
    from your résumé and details and pauses for you to review before you submit.
  </div>
</td></tr>

<tr><td style="{grid}padding:12px 40px 8px 40px;">
  <div style="padding:0 0 8px 0;">{_label("Copy this into Claude CoWork")}</div>
  <div style="background:{CARD_BG};border:1px solid {CARD_BORDER};padding:16px 18px;font-family:{MONO};font-size:12px;color:{INK};line-height:1.55;white-space:pre-wrap;word-break:break-word;">{_esc(cowork_prompt)}</div>
</td></tr>

<tr><td style="{grid}padding:14px 40px 8px 40px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
  <tr><td bgcolor="{ACCENT}" align="center" style="background:{ACCENT};">
    <a href="{_esc(review_url)}" style="display:block;padding:16px 24px;font-family:{MONO};font-size:14px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:{ON_ACCENT};text-decoration:none;">Open dashboard</a>
  </td></tr>
  </table>
</td></tr>

<tr><td height="40" style="{grid}line-height:40px;font-size:0;">&nbsp;</td></tr>

<tr><td style="{grid}padding:20px 40px 32px 40px;text-align:center;font-family:{SANS};font-size:12px;color:{MUTED};line-height:1.7;">
  {_esc(footer)}<br>
  You apply and submit each yourself; this tool never submits an application or certifies your claim.
</td></tr>

</table>
</td></tr>
</table>
</div>
"""


def apply_email_plaintext(*, week: str, jobs: list[dict], review_url: str, cowork_prompt: str,
                          csv_url: str, csv_filename: str = "submitted_jobs.csv") -> str:
    lines = [f"Ready to apply — week ending {week}", "",
             f"You approved {len(jobs)} job(s). Download your submissions log ({csv_filename}) — "
             "rebuilt live from your current picks — to fill in your weekly unemployment job-search "
             f"activity:", f"   {csv_url}", ""]
    for i, j in enumerate(jobs, 1):
        lines.append(f"{i}. {j.get('title','')}" + (f" @ {j['org']}" if j.get('org') else ""))
        if j.get("url"):
            lines.append(f"   {j['url']}")
    lines += ["", "HOW TO APPLY: apply to each yourself, or paste the prompt below into Claude "
              "CoWork on your desktop to have it fill each form for your review before you submit.",
              "", "--- Claude CoWork prompt ---", cowork_prompt, "--- end prompt ---", "",
              f"Dashboard: {review_url}", "",
              "You apply and submit each yourself; this tool never submits or certifies for you."]
    return "\n".join(lines)
