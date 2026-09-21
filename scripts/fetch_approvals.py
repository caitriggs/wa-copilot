"""
Log Jordan's approved-job list for a week to a local spreadsheet on RADMACHINE.
============================================================================

SETUP: run from wherever RADMACHINE drives the weekly pipeline (after `publish_week.py` and
after Jordan has submitted her picks on the dashboard). Stdlib-only (urllib + csv).

WHAT THIS DOES (and does NOT do): it GETs `/api/approvals?week=...` from the Worker to find out
which job ids Jordan checked "approve" for in the dashboard, cross-references those ids against
that user's local `queue.json` (same file `publish_week.py` read from) to attach the full job
details, and appends a row per approved job to a local CSV spreadsheet —
`<user>/log/submitted_jobs.csv` — for her own weekly WA unemployment job-search submission
record. Appends are de-duplicated by (week, job id), so re-running for the same week is safe.
Each row also carries a `resume_file` column (the exact résumé PDF filename that best matched
that posting, from that résumé's `profile.resumes` label) — that's what lets the emailed CoWork
prompt tell the job seeker's OWN desktop CoWork exactly which résumé to attach per job, without
it having to guess. If an older CSV predates this column, it's migrated in place (header rewritten,
existing rows backfilled with an empty resume_file) rather than left with a ragged column count.

    This script does NOT fill out, submit, or apply to anything, and it never touches the ESD
    log (`log/job_search_log.csv`) — only genuinely-completed activities the user confirms herself
    via `scripts/log_activity.py` belong there. This local spreadsheet is the RADMACHINE-side
    record of what was queued to apply to and its status, editable by hand as she actually applies.

    After appending, this script also sends a best-effort BACKUP "ready to apply" email (unless
    --no-email): the approved jobs, a link to the live submissions CSV on the dashboard Worker
    (`GET /api/approvals/csv` — the same artifact the dashboard's Download button serves, not a
    separate attached copy), and a copyable Claude CoWork prompt. The dashboard itself is the
    primary way to grab the CSV + prompt; this email is the "in case she doesn't check it" fallback.

    IMPORTANT: this runs on RADMACHINE (Jordan's side), but the "ready to apply" email + CoWork
    prompt it sends goes to the JOB SEEKER (e.g. Alex), who does NOT have RADMACHINE access and
    would run his own separate Claude CoWork session on his own desktop. The prompt is written
    for THAT machine: it references the CSV as downloaded from the email (~/Downloads/) and a
    résumé folder the job seeker creates on their own Desktop (~/Desktop/wa-unemployment-copilot/)
    — never a RADMACHINE-only absolute path.

USAGE:
    python fetch_approvals.py --user demo --week 2026-08-01 --worker-url https://wa-copilot-dashboard.<subdomain>.workers.dev
    # appends new rows to <user>/log/submitted_jobs.csv and prints a summary

Reads the bearer token from env WA_COPILOT_PUBLISH_TOKEN (never pass it on the command line).
If the Worker route is also fronted by Cloudflare Access, set WA_COPILOT_CF_ACCESS_CLIENT_ID
and WA_COPILOT_CF_ACCESS_CLIENT_SECRET (a Cloudflare Access service token) so requests carry
the CF-Access-Client-Id/CF-Access-Client-Secret headers alongside the bearer token; both are
optional and, if unset, the script behaves exactly as before (bearer token only).

EXIT CODES: 0 ok, 1 missing/unreadable local queue.json, 2 network/HTTP error, 3 bad arguments.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

APP_DIRNAME = "wa-unemployment-copilot"
CSV_FIELDS = ["date", "job_title", "company", "application_link", "status", "week", "job_id",
             "resume_file"]
DEFAULT_STATUS = "to apply"


def desktop_dir() -> Path:
    """Kept in sync with wa-unemployment-copilot/src/copilot/paths.py:desktop_dir()."""
    override = os.environ.get("WA_UI_DESKTOP")
    if override:
        return Path(override)
    home = Path(os.path.expanduser("~"))
    candidates = []
    onedrive = os.environ.get("OneDrive") or os.environ.get("OneDriveConsumer")
    if onedrive:
        candidates.append(Path(onedrive) / "Desktop")
    candidates.append(home / "OneDrive" / "Desktop")
    candidates.append(home / "Desktop")
    for c in candidates:
        if c.exists():
            return c
    return home / "Desktop"


def user_root(user: str, data_root: str | None = None) -> Path:
    base = Path(data_root) if data_root else desktop_dir() / APP_DIRNAME
    return base / user


def _clean_cf_value(v):
    """Tolerate a value pasted WITH its header-name prefix, e.g.
    'CF-Access-Client-Id: <id>' or 'CF-Access-Client-Secret:  <secret>' — strip the label +
    surrounding whitespace so the raw token is used."""
    v = (v or "").strip()
    low = v.lower()
    for pref in ("cf-access-client-id:", "cf-access-client-secret:"):
        if low.startswith(pref):
            return v[len(pref):].strip()
    return v


def cf_access_headers() -> dict[str, str]:
    """CF-Access-Client-Id/Secret headers for a Cloudflare Access service token, if configured.

    Optional and additive to the PUBLISH_TOKEN bearer auth — empty dict if the env vars aren't
    set, so requests behave exactly as before Access was added in front of the Worker route.
    """
    client_id = _clean_cf_value(os.environ.get("WA_COPILOT_CF_ACCESS_CLIENT_ID"))
    client_secret = _clean_cf_value(os.environ.get("WA_COPILOT_CF_ACCESS_CLIENT_SECRET"))
    if client_id and client_secret:
        return {"CF-Access-Client-Id": client_id, "CF-Access-Client-Secret": client_secret}
    return {}


def fetch_approvals(worker_url: str, week: str, token: str) -> dict:
    url = f"{worker_url.rstrip('/')}/api/approvals?week={urllib.parse.quote(week)}"
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "wa-copilot-publisher/1.0",
    }
    headers.update(cf_access_headers())
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_queue_items(user: str, week: str, data_root: str | None = None) -> dict:
    """Map dedup_key -> full queue item (job details) for this week."""
    queue_path = user_root(user, data_root) / "applications" / week / "queue.json"
    if not queue_path.exists():
        raise FileNotFoundError(f"queue.json not found: {queue_path}")
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    return {item.get("dedup_key", ""): item for item in queue.get("items", [])}


def _load_resume_filenames(user: str, data_root: str | None = None) -> dict[str, str]:
    """Best-effort résumé-label -> filename map (from config `profile.resumes`), for the
    resume_file CSV column + CoWork prompt. Returns {} on any problem (missing copilot install,
    bad config, etc.) rather than blocking the otherwise-stdlib-only approval-logging path —
    resume_file just comes back blank for every row in that case."""
    try:
        repo_src = Path(__file__).resolve().parent.parent / "src"
        if str(repo_src) not in sys.path:
            sys.path.insert(0, str(repo_src))
        from copilot.config import load as load_config
        cfg = load_config(user, data_root=data_root)
        return {r["label"]: Path(r["path"]).name for r in cfg.resumes()}
    except Exception as e:  # noqa: BLE001
        print(f"[fetch_approvals] résumé filename lookup unavailable ({type(e).__name__}: {e}); "
              "resume_file column will be blank.", file=sys.stderr)
        return {}


def build_rows(approved_ids: list[str], items_by_id: dict, week: str,
               resume_by_label: dict[str, str] | None = None) -> list[dict]:
    """One spreadsheet row per approved job that has local details in queue.json."""
    resume_by_label = resume_by_label or {}
    today = date.today().isoformat()
    rows = []
    for job_id in approved_ids:
        item = items_by_id.get(job_id)
        if not item:
            print(f"[fetch_approvals] warning: approved id {job_id!r} not found in local queue.json — skipping",
                  file=sys.stderr)
            continue
        rows.append({
            "date": today,
            "job_title": item.get("title", ""),
            "company": item.get("employer", ""),
            "application_link": item.get("url", ""),
            "status": DEFAULT_STATUS,
            "week": week,
            "job_id": job_id,
            "resume_file": resume_by_label.get(item.get("best_resume", ""), ""),
        })
    return rows


def existing_keys(csv_path: Path) -> set[tuple[str, str]]:
    """(week, job_id) pairs already logged, so re-running a week is idempotent."""
    if not csv_path.exists():
        return set()
    with csv_path.open(newline="", encoding="utf-8") as f:
        return {(row.get("week", ""), row.get("job_id", "")) for row in csv.DictReader(f)}


def _migrate_csv_header(csv_path: Path) -> None:
    """If an existing submitted_jobs.csv predates a column now in CSV_FIELDS (e.g. resume_file),
    rewrite it with the current header, backfilling the new column(s) with "" for old rows — keeps
    the file one consistent table (for a spreadsheet or CoWork to parse) instead of a header/row
    column-count mismatch as soon as new rows get appended with more fields than old ones had."""
    if not csv_path.exists() or csv_path.stat().st_size == 0:
        return
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames == CSV_FIELDS:
            return
        rows = list(reader)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in CSV_FIELDS})
    print(f"[fetch_approvals] migrated {csv_path.name} to the current column set.")


def append_rows(user: str, rows: list[dict], data_root: str | None = None) -> int:
    """Append new rows to <user>/log/submitted_jobs.csv, skipping ones already logged. Returns count appended."""
    csv_path = user_root(user, data_root) / "log" / "submitted_jobs.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    _migrate_csv_header(csv_path)
    seen = existing_keys(csv_path)
    new_rows = [r for r in rows if (r["week"], r["job_id"]) not in seen]
    if not new_rows:
        return 0
    write_header = not csv_path.exists() or csv_path.stat().st_size == 0
    with csv_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerows(new_rows)
    return len(new_rows)


def _import_email_deps():
    """Lazy import of this repo's copilot package, for the apply email only (RADMACHINE has SMTP).

    Same pattern publish_week.py uses — a missing/broken copilot install just skips the email
    instead of blocking the approval-logging path (which is stdlib-only).
    """
    repo_src = Path(__file__).resolve().parent.parent / "src"
    if str(repo_src) not in sys.path:
        sys.path.insert(0, str(repo_src))
    from copilot.config import load as load_config
    from copilot.mailer import send, MailError
    from copilot.email_render import render_apply_email, apply_email_plaintext, build_cowork_prompt
    return load_config, send, MailError, render_apply_email, apply_email_plaintext, build_cowork_prompt


def approvals_csv_url(worker_url: str, week: str) -> str:
    """The dashboard Worker's live submissions-CSV endpoint for this week — the single source of
    truth the dashboard's Download button and this backup email both point at (no attached copy)."""
    return f"{worker_url.rstrip('/')}/api/approvals/csv?week={urllib.parse.quote(week)}"


def send_apply_email(user: str, week: str, email_jobs: list[dict], csv_path: Path,
                     worker_url: str, data_root: str | None = None) -> None:
    """Best-effort BACKUP email: lists the approved jobs, links to the live submissions CSV on the
    dashboard Worker (not an attached copy), and embeds a copyable Claude CoWork prompt. Never
    raises. `csv_path` is kept in the signature only to name the file for the user; it is no longer
    read or attached."""
    if not email_jobs:
        return
    try:
        (load_config, send, MailError, render_apply_email,
         apply_email_plaintext, build_cowork_prompt) = _import_email_deps()
    except Exception as e:  # noqa: BLE001
        print(f"[fetch_approvals] apply email skipped (copilot import unavailable: {type(e).__name__})")
        return
    try:
        cfg = load_config(user, data_root=data_root)
    except Exception as e:  # noqa: BLE001
        print(f"[fetch_approvals] apply email skipped (config load failed: {type(e).__name__})")
        return
    if not cfg.email_enabled:
        print("[fetch_approvals] apply email skipped (email.enabled is false in config.yaml)")
        return

    csv_name = csv_path.name
    review_url = worker_url.rstrip("/") + "/"
    csv_url = approvals_csv_url(worker_url, week)
    resume_filenames = sorted({j["resume_file"] for j in email_jobs if j.get("resume_file")})
    prompt = build_cowork_prompt(week=week, applicant=cfg.get("applicant", {}) or {},
                                 csv_filename=csv_name, resume_filenames=resume_filenames)
    html = render_apply_email(week=week, jobs=email_jobs, review_url=review_url,
                              cowork_prompt=prompt, csv_url=csv_url, csv_filename=csv_name)
    text = apply_email_plaintext(week=week, jobs=email_jobs, review_url=review_url,
                                 cowork_prompt=prompt, csv_url=csv_url, csv_filename=csv_name)
    subject = f"Ready to apply — {len(email_jobs)} job{'s' if len(email_jobs) != 1 else ''}, week ending {week}"
    try:
        send(cfg, subject, text, html_body=html)
        print(f"[fetch_approvals] apply email sent to {cfg.email_to} (CSV link: {csv_url})")
    except MailError as e:
        print(f"[fetch_approvals] apply email FAILED: {e}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user", required=True, help="copilot user id (matches <Desktop>/wa-unemployment-copilot/<user>)")
    ap.add_argument("--week", required=True, help="week-ending date YYYY-MM-DD")
    ap.add_argument("--worker-url", default=os.environ.get("WA_COPILOT_WORKER_URL"),
                    help="dashboard Worker base URL (default: env WA_COPILOT_WORKER_URL)")
    ap.add_argument("--data-root", default=None, help="override data root (default: <Desktop>/wa-unemployment-copilot)")
    ap.add_argument("--no-email", action="store_true",
                    help="only append the CSV; don't email the approved jobs + CoWork prompt")
    args = ap.parse_args()

    if not args.worker_url:
        print("[fetch_approvals] --worker-url or env WA_COPILOT_WORKER_URL is required", file=sys.stderr)
        return 3
    token = os.environ.get("WA_COPILOT_PUBLISH_TOKEN")
    if not token:
        print("[fetch_approvals] env WA_COPILOT_PUBLISH_TOKEN is required", file=sys.stderr)
        return 3

    try:
        items_by_id = load_queue_items(args.user, args.week, args.data_root)
    except FileNotFoundError as e:
        print(f"[fetch_approvals] {e}", file=sys.stderr)
        return 1

    try:
        approvals = fetch_approvals(args.worker_url, args.week, token)
    except urllib.error.URLError as e:
        print(f"[fetch_approvals] failed to reach Worker: {e}", file=sys.stderr)
        return 2

    approved_ids = approvals.get("approved", [])
    resume_by_label = _load_resume_filenames(args.user, args.data_root)
    rows = build_rows(approved_ids, items_by_id, args.week, resume_by_label)
    appended = append_rows(args.user, rows, args.data_root)
    csv_path = user_root(args.user, args.data_root) / "log" / "submitted_jobs.csv"
    print(f"[fetch_approvals] {len(approved_ids)} approved, {appended} new row(s) appended -> {csv_path}")

    if not args.no_email:
        email_jobs = [{"title": it.get("title", ""), "org": it.get("employer", ""),
                       "location": it.get("location", ""), "url": it.get("url", ""),
                       "resume_file": resume_by_label.get(it.get("best_resume", ""), "")}
                      for jid in approved_ids for it in [items_by_id.get(jid)] if it]
        send_apply_email(args.user, args.week, email_jobs, csv_path, args.worker_url, args.data_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
