#!/usr/bin/env python3
"""
Assisted eServices weekly-claim helper (opt-in, human-driven) — Phase 3.
========================================================================

Opens a VISIBLE browser and shows your prepared answers on the page while YOU log in
(SecureAccess Washington + MFA) and fill/submit the weekly claim. This tool NEVER submits or
certifies, and never auto-answers an attestation (earnings / able & available / etc.).

USAGE
    python scripts/claim_assist.py --user demo
    python scripts/claim_assist.py --user demo --autofill      # best-effort activity-field fill
    python scripts/claim_assist.py --user demo --yes           # skip the interactive confirmation

Requires: claim_assist.enabled: true in config.yaml, Playwright installed
(`pip install playwright && playwright install chromium`), and an explicit confirmation.
Exit codes: 0 ok; 2 disabled/declined; 5 Playwright missing.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from copilot import paths, claim_assist  # noqa: E402
from copilot.config import load, ConfigError  # noqa: E402

CONFIRM_TEXT = (
    "\nThis helper opens a browser and displays your logged activities to help you fill the\n"
    "weekly claim. YOU log in, YOU enter answers, and YOU submit + certify. The weekly claim is\n"
    "a certification under penalty of perjury — this tool will not submit or certify for you,\n"
    "and will not answer any attestation (earnings, able & available, etc.).\n"
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Guided eServices weekly-claim helper.")
    ap.add_argument("--user", required=True)
    ap.add_argument("--autofill", action="store_true",
                    help="best-effort fill of activity fields (needs claim_assist.autofill_attempt)")
    ap.add_argument("--yes", action="store_true", help="skip the interactive confirmation prompt")
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args()

    try:
        paths.ensure_scaffold(args.user, args.data_root)
        cfg = load(args.user, data_root=args.data_root)
    except (paths.UnsafeDataLocation, ConfigError) as e:
        print(e, file=sys.stderr)
        return 2

    confirmed = args.yes
    ok, reason = claim_assist.preflight(cfg, confirmed=True)  # check the config flag first
    if not cfg.get("claim_assist.enabled", False):
        print(reason, file=sys.stderr)
        return 2

    if not confirmed:
        print(CONFIRM_TEXT)
        resp = input('Type "I UNDERSTAND" to continue (anything else cancels): ').strip()
        if resp != "I UNDERSTAND":
            print("Cancelled.")
            return 2

    return claim_assist.assist(cfg, autofill=args.autofill, data_root=args.data_root)


if __name__ == "__main__":
    sys.exit(main())
