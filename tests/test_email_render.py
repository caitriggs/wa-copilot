"""Tests for src/copilot/email_render.py — the weekly-review HTML/text templates."""

from copilot.email_render import (render_weekly_review, weekly_review_plaintext,
                                  render_apply_email, apply_email_plaintext, build_cowork_prompt)

JOBS = [
    {"title": "Operations Program Manager", "org": "Puget Logistics", "location": "Seattle, WA",
     "mode": None, "match_pct": 99, "match_reason": "Surfaced for: title match",
     "resume": "Test Engineer", "posted_label": "posted 2d ago",
     "blurb": "Own weekly S&OP for a 3PL.", "url": "https://ex.com/a"},
    {"title": "Ops Lead <script>", "org": "Cascade & Co", "location": "Remote", "mode": None,
     "match_pct": 88, "match_reason": "Surfaced for: skills match", "posted_label": None,
     "blurb": "First ops hire.", "url": "https://ex.com/b"},
]


def test_render_contains_core_content():
    html = render_weekly_review(week="2026-08-01", surfaced=5, jobs=JOBS,
                                review_url="https://dash.example/")
    assert "Weekly Review" in html
    assert "2026-08-01" in html
    assert "Operations Program Manager" in html
    assert "Puget Logistics" in html
    assert "99% match" in html
    assert "Surfaced for: title match" in html    # the top-contributing-factor label, beside %
    assert "posted 2d ago" in html
    assert "Test Engineer" in html               # résumé badge next to the match
    assert "https://dash.example/" in html
    assert "REVIEW" in html.upper()
    assert "Takes about" not in html             # the minutes caption was removed


def test_render_bug_button_and_reply_note():
    html = render_weekly_review(week="2026-08-01", surfaced=2, jobs=JOBS,
                                review_url="https://dash.example/",
                                reply_to="demo+jobs@gmail.com")
    assert "🐛 Report a bug" in html
    # a mailto reply to the sender, prefilled with a bug-report subject
    assert "mailto:demo+jobs@gmail.com" in html
    assert "Bug%20report" in html
    # footer always invites a plain reply too
    assert "reply to this email" in html


def test_render_no_bug_button_without_reply_to():
    html = render_weekly_review(week="w", surfaced=2, jobs=JOBS, review_url="#")
    assert "🐛 Report a bug" not in html          # no button when we don't know the reply address
    assert "reply to this email" in html          # but the footer note still tells them to reply


def test_plaintext_has_reply_note():
    txt = weekly_review_plaintext(week="w", surfaced=2, jobs=JOBS, review_url="#")
    assert "reply to this email" in txt


def test_render_escapes_html():
    html = render_weekly_review(week="2026-08-01", surfaced=2, jobs=JOBS,
                                review_url="https://dash.example/")
    assert "<script>" not in html               # the job title's tag is escaped
    assert "Ops Lead &lt;script&gt;" in html
    assert "Cascade &amp; Co" in html


def test_top_label_reflects_surfaced_vs_shown():
    more = render_weekly_review(week="w", surfaced=5, jobs=JOBS, review_url="#")
    assert "Top 2 of 5".upper() in more.upper()
    # when nothing extra is queued, the label switches away from "Top N of M"
    equal = render_weekly_review(week="w", surfaced=2, jobs=JOBS, review_url="#")
    assert "Top 2 of 2".upper() not in equal.upper()


def test_intro_singular_plural_and_queue():
    many = render_weekly_review(week="w", surfaced=5, jobs=JOBS[:1], review_url="#")
    assert "5 roles cleared" in many
    assert "waiting in your queue" in many
    one = render_weekly_review(week="w", surfaced=1, jobs=JOBS[:1], review_url="#")
    assert "1 role cleared" in one


def test_render_handles_no_jobs():
    html = render_weekly_review(week="w", surfaced=0, jobs=[], review_url="#")
    assert "No roles cleared your filters" in html


APPLY_JOBS = [
    {"title": "SDET, Platform", "org": "Acme", "location": "Seattle, WA", "url": "https://x/1"},
    {"title": "QA Manager", "org": "Globex", "location": "Remote", "url": "https://x/2"},
]


def test_cowork_prompt_includes_applicant_and_csv():
    p = build_cowork_prompt(week="2026-08-22", csv_filename="submitted_jobs.csv",
                            applicant={"full_name": "Alex Rivera", "email": "alex@x.com",
                                       "links": {"linkedin": "li/alex"}})
    assert "Alex Rivera" in p and "alex@x.com" in p and "li/alex" in p
    assert "submitted_jobs.csv" in p
    assert "STOP before submitting" in p          # human-in-the-loop safety
    assert "never submit without my ok" in p.lower()


def test_cowork_prompt_uses_seekers_own_desktop_not_radmachine():
    """The prompt runs on the job seeker's OWN CoWork/desktop (e.g. Alex), not RADMACHINE — it must
    never reference a RADMACHINE-only absolute path, only things the seeker can set up themselves
    from the email: the CSV as their browser downloads it, and a Desktop folder they create."""
    p = build_cowork_prompt(week="2026-08-22", csv_filename="submitted_jobs.csv",
                            applicant={"full_name": "Alex Rivera"})
    assert "wa-unemployment-copilot/max/" not in p    # no RADMACHINE per-user absolute path
    assert "~/Downloads/submitted_jobs.csv" in p
    assert "~/Desktop/wa-unemployment-copilot/" in p


def test_cowork_prompt_offers_resume_filenames_as_a_hint_not_a_requirement():
    """Résumé filenames are a soft hint (the seeker may not have renamed their files to match) —
    CoWork is told to match by job title/content, never to require an exact filename or refuse to
    proceed if one is "missing"."""
    p = build_cowork_prompt(week="2026-08-22", csv_filename="submitted_jobs.csv",
                            applicant={"full_name": "Alex Rivera"},
                            resume_filenames=["project-manager.pdf", "qa-lead.pdf",
                                              "project-manager.pdf"])   # dupe, should collapse
    assert p.count("project-manager.pdf") == 1    # listed once, deduped
    assert "qa-lead.pdf" in p
    assert "resume_file column" in p
    assert "as a hint" in p.lower()
    assert "don't need to match exactly" in p.lower()
    assert "ask me first" not in p.lower()         # no hard-stop on a missing exact filename


def test_cowork_prompt_falls_back_when_no_resume_filenames_given():
    p = build_cowork_prompt(week="2026-08-22", csv_filename="submitted_jobs.csv",
                            applicant={"full_name": "Alex Rivera"})
    assert "~/Desktop/wa-unemployment-copilot/" in p
    assert "resume_file column" not in p
    assert "best fits this job" in p.lower()       # still tells it to match by content


def test_render_apply_email_lists_jobs_and_embeds_prompt():
    html = render_apply_email(week="2026-08-22", jobs=APPLY_JOBS, review_url="https://dash/",
                              cowork_prompt="COWORK-PROMPT-TOKEN",
                              csv_url="https://dash/api/approvals/csv?week=2026-08-22",
                              csv_filename="submitted_jobs.csv")
    assert "SDET, Platform" in html and "QA Manager" in html
    assert "COWORK-PROMPT-TOKEN" in html          # prompt embedded for copy
    assert "submitted_jobs.csv" in html and "CoWork" in html
    assert "Ready to apply" in html
    # CSV is linked (single source of truth on the Worker), not attached.
    assert "https://dash/api/approvals/csv?week=2026-08-22" in html
    assert "Download submissions CSV" in html


def test_apply_email_plaintext():
    txt = apply_email_plaintext(week="w", jobs=APPLY_JOBS, review_url="https://dash/",
                                cowork_prompt="PROMPT",
                                csv_url="https://dash/api/approvals/csv?week=w")
    assert "SDET, Platform" in txt and "https://x/1" in txt and "PROMPT" in txt
    assert "https://dash/api/approvals/csv?week=w" in txt   # CSV linked, not attached


def test_plaintext_lists_jobs_and_link():
    txt = weekly_review_plaintext(week="2026-08-01", surfaced=5, jobs=JOBS,
                                  review_url="https://dash.example/")
    assert "Weekly Review" in txt
    assert "99% match — Operations Program Manager" in txt
    assert "https://ex.com/a" in txt
    assert "Review & approve: https://dash.example/" in txt
    assert "<td" not in txt and "<div" not in txt  # no HTML markup in the text part
