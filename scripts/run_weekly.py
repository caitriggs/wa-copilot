#!/usr/bin/env python3
"""
Weekly pipeline entrypoint.
===========================

Runs the tedious parts of the week for a user: discover jobs, draft applications, assemble the
claim packet, and notify. It never submits applications and never certifies the weekly claim —
you do those, and you log what you actually did with scripts/log_activity.py.

USAGE
-----
    python scripts/run_weekly.py --user demo                     # discover,draft,packet,notify
    python scripts/run_weekly.py --user demo --dry-run           # show what would happen; no writes to log/email
    python scripts/run_weekly.py --user demo --steps discover,draft
    python scripts/run_weekly.py --user demo --steps packet,notify
    python scripts/run_weekly.py --user demo --steps nudge       # remind if under the weekly minimum

Steps: discover, draft, packet, notify, nudge. Default: discover,draft,packet,notify.
Exit codes: 0 ok; 2 bad args/config; 3 unsafe data location. A week short of the ESD minimum is a
warning (packet is skipped, a nudge is printed), not a hard error.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from copilot import paths, discover as discover_mod, draft as draft_mod, packet as packet_mod  # noqa: E402
from copilot import notify as notify_mod, logbook, profile_import as profile_mod, apply as apply_mod  # noqa: E402
from copilot.config import load, ConfigError  # noqa: E402

DEFAULT_STEPS = ["discover", "draft", "apply", "packet", "notify"]
ALL_STEPS = ["import"] + DEFAULT_STEPS + ["record", "nudge"]

DISCLAIMER = ("Reminder: the weekly CLAIM is always yours to submit + certify — this tool never "
              "does that. Auto-apply follows apply.mode in your config (off/stage/live).")


def main() -> int:
    ap = argparse.ArgumentParser(description="wa-unemployment-copilot weekly pipeline.")
    ap.add_argument("--user", required=True)
    ap.add_argument("--steps", default=",".join(DEFAULT_STEPS),
                    help=f"comma list of: {', '.join(ALL_STEPS)}")
    ap.add_argument("--dry-run", action="store_true",
                    help="run read-only steps; skip writing to the log/sending email")
    ap.add_argument("--data-root", default=None, help="override data base dir (testing)")
    args = ap.parse_args()

    steps = [s.strip() for s in args.steps.split(",") if s.strip()]
    bad = [s for s in steps if s not in ALL_STEPS]
    if bad:
        print(f"Unknown step(s): {', '.join(bad)}. Valid: {', '.join(ALL_STEPS)}", file=sys.stderr)
        return 2

    try:
        paths.ensure_scaffold(args.user, args.data_root)
        cfg = load(args.user, data_root=args.data_root)
    except paths.UnsafeDataLocation as e:
        print(e, file=sys.stderr)
        return 3
    except ConfigError as e:
        print(e, file=sys.stderr)
        return 2

    we = paths.week_ending()
    print(f"== wa-unemployment-copilot :: user={cfg.user} :: week ending {we.isoformat()} ==")
    print(DISCLAIMER + "\n")

    postings = []
    packet_path = None

    if "import" in steps:
        print("[import]")
        if args.dry_run:
            print(f"  [dry-run] would (re)build profile/history.json "
                  f"(mode: {cfg.get('profile.linkedin_import', 'resume')})")
        else:
            p = profile_mod.import_history(cfg, args.data_root, force=True)
            print(f"  history: {p}")

    if "discover" in steps:
        print("[discover]")
        postings = discover_mod.discover(cfg, we, args.data_root, write_cache=not args.dry_run)

    if "draft" in steps:
        print("[draft]")
        if not postings and not args.dry_run:
            # allow draft to run off a fresh discover if it wasn't in the step list
            postings = discover_mod.discover(cfg, we, args.data_root, write_cache=True)
        if args.dry_run:
            print(f"  [dry-run] would draft top {cfg.get('ranking.top_n_drafts', 5)} of "
                  f"{len(postings)} postings.")
        else:
            draft_mod.draft(cfg, postings, we, args.data_root)

    if "apply" in steps:
        print("[apply]")
        if not postings and not args.dry_run:
            postings = discover_mod.discover(cfg, we, args.data_root, write_cache=True)
        if args.dry_run:
            print(f"  [dry-run] would queue top {cfg.get('apply.top_n', 3)} "
                  f"(mode: {cfg.get('apply.mode', 'stage')}); live mode would log executor results.")
        else:
            apply_mod.run_apply(cfg, postings, we, args.data_root)

    if "record" in steps:
        print("[record]")
        if args.dry_run:
            print("  [dry-run] would read applications/<week>/results.json and log applied ones.")
        else:
            summary = apply_mod.record_results(cfg, we, args.data_root)
            if not summary.get("found"):
                print("  no results.json yet — run the Cowork apply task first, then re-run record.")

    if "packet" in steps:
        print("[packet]")
        have = logbook.count_valid(cfg.user, we, args.data_root)
        need = cfg.targets_per_week
        if have < need:
            print(f"  Only {have}/{need} activities logged this week — packet skipped. "
                  f"Log more: python scripts/log_activity.py --user {cfg.user}")
        else:
            try:
                packet_path = packet_mod.build(cfg, we, args.data_root)
                print(f"  packet: {packet_path}")
            except packet_mod.NotEnoughActivities as e:
                print(f"  {e}")

    if "notify" in steps:
        print("[notify]")
        if args.dry_run:
            print("  [dry-run] would write local summary" +
                  (" and email" if cfg.email_enabled else "") + ".")
        else:
            if packet_path is None:
                # try an existing packet for this week
                candidate = paths.packet_dir(cfg.user, we, args.data_root) / "claim_packet.md"
                packet_path = candidate if candidate.exists() else None
            notify_mod.notify(cfg, packet_path, we, args.data_root)

    if "nudge" in steps:
        print("[nudge]")
        have = logbook.count_valid(cfg.user, we, args.data_root)
        need = cfg.targets_per_week
        if have >= need:
            print(f"  On track: {have}/{need} activities logged. File your weekly claim when open.")
        else:
            msg = (f"You have {have}/{need} job-search activities logged for the week ending "
                   f"{we.isoformat()}. Log {need - have} more before you file. "
                   f"Run: python scripts/log_activity.py --user {cfg.user}")
            print("  " + msg)
            if cfg.email_enabled and not args.dry_run:
                from copilot.mailer import send, MailError
                try:
                    send(cfg, f"Job-search reminder — {have}/{need} logged", msg)
                    print(f"  emailed reminder to {cfg.email_to}")
                except MailError as e:
                    print(f"  (email reminder failed: {e})")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
