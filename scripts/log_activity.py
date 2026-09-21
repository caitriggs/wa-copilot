#!/usr/bin/env python3
"""
Log a job-search activity you ACTUALLY completed (the human-in-the-loop step).
=============================================================================

This is how activities enter your ESD log. It only records what YOU confirm — nothing is
auto-logged. Run it after you genuinely apply to a job or complete another approved activity.

USAGE
-----
    python scripts/log_activity.py --user demo                 # interactive prompt
    python scripts/log_activity.py --user demo --list          # show this week's logged activities
    # Non-interactive (for power users / scripting a single confirmed entry):
    python scripts/log_activity.py --user demo \
        --type applied_to_job --employer "Acme" --position "Ops Manager" \
        --method online --contact "https://acme.com/jobs/123" --result applied --notes "referral"

Valid activity types are printed if you omit --type in non-interactive mode.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from copilot import paths, logbook  # noqa: E402


def _print_types():
    print("Approved activity types:")
    for k, label in logbook.ACTIVITY_TYPES.items():
        print(f"  {k:<32} {label}")


def interactive(user: str, data_root=None) -> int:
    print("Log an activity you actually completed (Ctrl-C to cancel).\n")
    _print_types()
    at = input("\nactivity_type: ").strip()
    if at not in logbook.ACTIVITY_TYPES:
        print(f"Unknown type {at!r}.", file=sys.stderr)
        return 2
    entry = {
        "activity_type": at,
        "date": input("date [YYYY-MM-DD, blank=today]: ").strip(),
        "employer_or_org": input("employer/org: ").strip(),
        "position": input("position: ").strip(),
        "contact_method": input("contact method [online/email/phone/in-person]: ").strip(),
        "contact_name_or_url": input("contact name or URL: ").strip(),
        "result_status": input("result [applied/interview/no-response/...]: ").strip(),
        "notes": input("notes: ").strip(),
    }
    if not entry["date"]:
        entry.pop("date")
    added = logbook.append_confirmed(user, [entry], data_root)
    print(f"\n{'Logged' if added else 'Already logged (duplicate)'}.")
    _show_week(user, data_root)
    return 0


def _show_week(user: str, data_root=None):
    we = paths.week_ending()
    rows = logbook.week_activities(user, we, data_root)
    have = logbook.count_valid(user, we, data_root)
    print(f"\nWeek ending {we.isoformat()}: {have} activities logged.")
    for r in rows:
        print(f"  - {r['date']}  {r['activity_type']:<24} {r['employer_or_org']}  ({r['result_status']})")


def main() -> int:
    ap = argparse.ArgumentParser(description="Log a confirmed job-search activity.")
    ap.add_argument("--user", required=True)
    ap.add_argument("--list", action="store_true", help="show this week's activities and exit")
    ap.add_argument("--data-root", default=None)
    ap.add_argument("--type", help="activity_type (see --list-types)")
    ap.add_argument("--list-types", action="store_true")
    for f in ("employer", "position", "method", "contact", "result", "notes", "date"):
        ap.add_argument(f"--{f}", default="")
    args = ap.parse_args()

    try:
        user = paths.safe_user(args.user)
        paths.ensure_scaffold(user, args.data_root)
    except (ValueError, paths.UnsafeDataLocation) as e:
        print(e, file=sys.stderr)
        return 2

    if args.list_types:
        _print_types()
        return 0
    if args.list:
        _show_week(user, args.data_root)
        return 0

    if args.type:
        entry = {
            "activity_type": args.type, "employer_or_org": args.employer,
            "position": args.position, "contact_method": args.method,
            "contact_name_or_url": args.contact, "result_status": args.result,
            "notes": args.notes,
        }
        if args.date:
            entry["date"] = args.date
        try:
            added = logbook.append_confirmed(user, [entry], args.data_root)
        except logbook.InvalidActivity as e:
            print(e, file=sys.stderr)
            return 2
        print("Logged." if added else "Already logged (duplicate).")
        _show_week(user, args.data_root)
        return 0

    try:
        return interactive(user, args.data_root)
    except KeyboardInterrupt:
        print("\ncancelled.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
