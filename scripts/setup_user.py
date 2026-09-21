#!/usr/bin/env python3
"""
Onboard a user — scaffold their Desktop data folder, copy the config template, store secrets.
=============================================================================================

Creates <Desktop>/wa-unemployment-copilot/<user>/ with the standard subfolders, drops a copy of
config/user.example.yaml there as config.yaml (only if one doesn't already exist), and can store
secrets in the OS keyring (Windows Credential Manager) or a gitignored secrets.env fallback.

USAGE
-----
    python scripts/setup_user.py --user demo
    python scripts/setup_user.py --user demo --set-secret usajobs      # prompts (hidden input)
    python scripts/setup_user.py --user demo --set-secret smtp_pass
    python scripts/setup_user.py --user demo --print-schedule          # print schtasks commands

Nothing sensitive is written to config.yaml; secrets go to the keyring/secrets.env only.
Exit code 0 on success, non-zero on error.
"""

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from copilot import paths, config  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = REPO_ROOT / "config" / "user.example.yaml"

# Well-known secret refs and how they're described at the prompt.
KNOWN_SECRETS = {
    "usajobs": "USAJOBS API authorization key (from developer.usajobs.gov/apirequest)",
    "usajobs_email": "Email you registered with USAJOBS (sent as the User-Agent header)",
    "adzuna_id": "Adzuna app id (from developer.adzuna.com)",
    "adzuna_key": "Adzuna app key (from developer.adzuna.com)",
    "jooble": "Jooble API key (from jooble.org/api/about)",
    "careerjet_affid": "Careerjet affiliate id (from careerjet.com/partners)",
    "anthropic": "Anthropic API key (for LLM-written cover letters; console.anthropic.com)",
    "smtp_user": "SMTP username, e.g. your Gmail address",
    "smtp_pass": "SMTP app password (Gmail: needs 2-Step Verification)",
    "linkedin": "LinkedIn login (stored as 'email|password') — only if using authed source",
}


def scaffold(user: str) -> Path:
    root = paths.ensure_scaffold(user)
    dest = root / "config.yaml"
    if dest.exists():
        print(f"  config.yaml already exists: {dest}")
    elif TEMPLATE.exists():
        text = TEMPLATE.read_text(encoding="utf-8").replace("user: demo", f"user: {user}", 1)
        dest.write_text(text, encoding="utf-8")
        print(f"  wrote config template -> {dest}")
    else:
        print(f"  WARNING: template not found at {TEMPLATE}; create config.yaml manually.")

    # Copy the editable cover-letter prompt so this user can tune the letter's voice.
    prompt_src = REPO_ROOT / "config" / "cover_letter_prompt.md"
    prompt_dest = root / "cover_letter_prompt.md"
    if prompt_src.exists() and not prompt_dest.exists():
        prompt_dest.write_text(prompt_src.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"  wrote cover-letter prompt -> {prompt_dest} (edit to tune the letter's voice)")

    print(f"  data folder ready: {root}")
    return root


def store_secret(user: str, ref: str) -> int:
    desc = KNOWN_SECRETS.get(ref, f"secret '{ref}'")
    print(f"Enter {desc}.")
    value = getpass.getpass(f"  {ref} (input hidden): ").strip()
    if not value:
        print("  no value entered; nothing stored.", file=sys.stderr)
        return 1
    backend = config.set_secret(user, ref, value)
    print(f"  stored '{ref}' via {backend}.")
    return 0


def print_schedule(user: str) -> None:
    early = REPO_ROOT / "scripts" / "run_weekly.py"
    publish = REPO_ROOT / "scripts" / "publish_week.py"
    print("\nWindows Task Scheduler — run these in an elevated prompt (adjust the path):\n")
    print(
        f'schtasks /Create /TN "UnemploymentCopilot_{user}_Early" ^\n'
        f'  /TR "python {early} --user {user} --steps discover,draft,packet" ^\n'
        f'  /SC WEEKLY /D MON /ST 07:30 /RU {user} /RP * /F\n'
    )
    print(
        f'schtasks /Create /TN "UnemploymentCopilot_{user}_Publish" ^\n'
        f'  /TR "python {publish} --user {user}" ^\n'
        f'  /SC WEEKLY /D THU /ST 18:00 /RU {user} /RP * /F\n'
    )
    print(
        "  ^-- Thursday evening: publishes this week's picks to the review dashboard Worker and\n"
        "      (if email.enabled in config.yaml) sends the \"ready to review\" email with the\n"
        "      dashboard link. Requires WA_COPILOT_WORKER_URL and WA_COPILOT_PUBLISH_TOKEN set as\n"
        "      persistent environment variables for this user first, e.g.:\n"
        "        setx WA_COPILOT_WORKER_URL \"https://wa-copilot-dashboard.<subdomain>.workers.dev\"\n"
        "        setx WA_COPILOT_PUBLISH_TOKEN \"<same value as the Worker's PUBLISH_TOKEN secret>\"\n"
        "      (open a new shell after setx for the task to see them.)\n"
    )
    print(
        f'schtasks /Create /TN "UnemploymentCopilot_{user}_Nudge" ^\n'
        f'  /TR "python {early} --user {user} --steps nudge" ^\n'
        f'  /SC WEEKLY /D THU /ST 16:00 /RU {user} /RP * /F\n'
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Onboard a wa-unemployment-copilot user.")
    ap.add_argument("--user", required=True, help="short user id (a-z0-9-_)")
    ap.add_argument("--set-secret", metavar="REF",
                    help=f"store a secret by ref ({', '.join(KNOWN_SECRETS)}, or any name)")
    ap.add_argument("--print-schedule", action="store_true",
                    help="print the Windows Task Scheduler commands and exit")
    args = ap.parse_args()

    try:
        user = paths.safe_user(args.user)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2

    if args.print_schedule:
        print_schedule(user)
        return 0

    print(f"Onboarding user: {user}")
    try:
        scaffold(user)
    except paths.UnsafeDataLocation as e:
        print(e, file=sys.stderr)
        return 3

    if args.set_secret:
        rc = store_secret(user, args.set_secret.strip())
        if rc:
            return rc

    print("\nNext steps:")
    print(f"  1. Edit config: {paths.config_path(user)}")
    print("  2. Add your resume at profile/resume.pdf (or a LinkedIn export zip).")
    print(f"  3. Store secrets, e.g.: python scripts/setup_user.py --user {user} --set-secret usajobs")
    print(f"  4. Preview a run:  python scripts/run_weekly.py --user {user} --dry-run")
    print(f"  5. Schedule it:    python scripts/setup_user.py --user {user} --print-schedule")
    print("\nReminder: this tool prepares your week. YOU submit applications and certify the")
    print("weekly claim in eServices. It never submits or certifies on your behalf.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
