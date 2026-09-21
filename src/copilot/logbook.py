"""
The ESD job-search log — truthful, append-only, deduped.
========================================================

One CSV per user at <Desktop>/wa-unemployment-copilot/<user>/log/job_search_log.csv, holding one
row per job-search activity in the format ESD expects. Only activities the user explicitly
confirms are written here (see scripts/log_activity.py) — nothing is auto-logged as "done".

`activity_type` is constrained to an ESD-approved set so every logged activity is valid.
Re-verify the set against ESD's current page when it changes:
https://esd.wa.gov/get-financial-help/unemployment-benefits/weekly-unemployment-claims/job-search-requirements
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Iterable

from . import paths

COLUMNS = [
    "week_ending", "date", "activity_type", "employer_or_org", "position",
    "contact_method", "contact_name_or_url", "result_status", "notes",
]

# ESD-approved activity types (labels are human-friendly; keys are what we store).
ACTIVITY_TYPES = {
    "applied_to_job": "Applied to a job",
    "contacted_employer": "Contacted an employer about work",
    "submitted_resume": "Submitted a resume/application",
    "job_interview": "Interviewed for a job",
    "registered_with_agency": "Registered with a placement/recruitment agency",
    "job_search_workshop": "Attended a job-search workshop/seminar",
    "job_fair": "Attended a job fair (incl. virtual)",
    "worksource_profile_update": "Created/updated a WorkSource profile",
    "linkedin_profile_update": "Created/updated a LinkedIn/professional profile",
    "civil_service_or_skills_test": "Took a civil-service or skills/employment test",
    "resea_reemployment_appointment": "Attended a RESEA/reemployment appointment",
}


class InvalidActivity(ValueError):
    """Raised when an activity has an unknown activity_type or missing required fields."""


def _row_key(row: dict) -> tuple:
    """Dedup identity for a logged activity within a week."""
    return (
        str(row.get("week_ending", "")).strip(),
        str(row.get("date", "")).strip(),
        str(row.get("activity_type", "")).strip().lower(),
        str(row.get("employer_or_org", "")).strip().lower(),
        str(row.get("position", "")).strip().lower(),
        str(row.get("contact_name_or_url", "")).strip().lower(),
    )


def normalize_entry(entry: dict, default_week=None) -> dict:
    """Fill defaults, validate activity_type, and coerce to the canonical column set."""
    at = str(entry.get("activity_type", "")).strip()
    if at not in ACTIVITY_TYPES:
        raise InvalidActivity(
            f"Unknown activity_type {at!r}. Valid: {', '.join(sorted(ACTIVITY_TYPES))}"
        )
    d = entry.get("date") or date.today().isoformat()
    d = d.isoformat() if isinstance(d, date) else str(d)
    we = entry.get("week_ending")
    if not we:
        we = paths.week_ending(date.fromisoformat(d)).isoformat()
    elif isinstance(we, date):
        we = we.isoformat()
    row = {c: "" for c in COLUMNS}
    row.update({
        "week_ending": str(we),
        "date": d,
        "activity_type": at,
        "employer_or_org": str(entry.get("employer_or_org", "")).strip(),
        "position": str(entry.get("position", "")).strip(),
        "contact_method": str(entry.get("contact_method", "")).strip(),
        "contact_name_or_url": str(entry.get("contact_name_or_url", "")).strip(),
        "result_status": str(entry.get("result_status", "")).strip(),
        "notes": str(entry.get("notes", "")).strip(),
    })
    return row


def read_all(user: str, data_root=None) -> list[dict]:
    path = paths.log_csv_path(user, data_root)
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def append_confirmed(user: str, entries: Iterable[dict], data_root=None) -> int:
    """Append confirmed activities, skipping exact duplicates. Returns count actually added."""
    path = paths.log_csv_path(user, data_root)
    path.parent.mkdir(parents=True, exist_ok=True)

    existing = read_all(user, data_root)
    seen = {_row_key(r) for r in existing}

    to_write = []
    for e in entries:
        row = normalize_entry(e)
        k = _row_key(row)
        if k in seen:
            continue
        seen.add(k)
        to_write.append(row)

    write_header = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        if write_header:
            w.writeheader()
        for row in to_write:
            w.writerow(row)
    return len(to_write)


def week_activities(user: str, week_end, data_root=None) -> list[dict]:
    """All logged rows for the given claim week (week_end may be a date or 'YYYY-MM-DD')."""
    we = week_end.isoformat() if isinstance(week_end, date) else str(week_end)
    return [r for r in read_all(user, data_root) if str(r.get("week_ending", "")) == we]


def count_valid(user: str, week_end, data_root=None) -> int:
    """Number of valid (known activity_type) activities logged for the week."""
    return sum(
        1 for r in week_activities(user, week_end, data_root)
        if str(r.get("activity_type", "")).strip() in ACTIVITY_TYPES
    )
