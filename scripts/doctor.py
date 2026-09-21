#!/usr/bin/env python3
"""
Doctor — verify a user's setup before a real end-to-end run.
============================================================

Read-only. Checks Python version, dependencies, the data folder, config validity, which secrets
are present (never their values), and (optionally) does a live USAJOBS ping and an IMAP login
test. Prints a ✓/✗ summary with the next action for anything that isn't ready.

USAGE
    python scripts/doctor.py --user demo
    python scripts/doctor.py --user demo --live     # also ping USAJOBS + test IMAP login

Exit code 0 if nothing is broken (warnings allowed), 1 if a hard problem would block a run.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

OK, WARN, BAD = "  ✓", "  ! ", "  ✗"


def main() -> int:
    ap = argparse.ArgumentParser(description="Check a wa-unemployment-copilot setup.")
    ap.add_argument("--user", required=True)
    ap.add_argument("--live", action="store_true", help="also make live source calls")
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args()

    problems = 0
    warns = 0
    print(f"wa-unemployment-copilot doctor — user {args.user}\n")

    # 1) Python
    v = sys.version_info
    if v >= (3, 10):
        print(f"{OK} Python {v.major}.{v.minor}.{v.micro}")
    else:
        print(f"{BAD} Python {v.major}.{v.minor} — need 3.10+")
        problems += 1

    # 2) Dependencies
    from importlib import import_module
    required = {"yaml": "PyYAML", "requests": "requests"}
    optional = {"keyring": "keyring (OS secret store)", "feedparser": "feedparser (RSS)",
                "pdfplumber": "pdfplumber (PDF résumé)"}
    for mod, label in required.items():
        try:
            import_module(mod)
            print(f"{OK} {label} installed")
        except ImportError:
            print(f"{BAD} {label} missing — run: pip install -r requirements.txt")
            problems += 1
    for mod, label in optional.items():
        try:
            import_module(mod)
            print(f"{OK} {label} installed")
        except ImportError:
            print(f"{WARN}{label} not installed (optional; some features reduced)")
            warns += 1

    # 3) Paths + config
    from copilot import paths, config
    try:
        root = paths.user_root(args.user, args.data_root)
        print(f"{OK} data folder resolves: {root}")
    except paths.UnsafeDataLocation as e:
        print(f"{BAD} {e}")
        return 1

    try:
        cfg = config.load(args.user, data_root=args.data_root)
        print(f"{OK} config valid ({paths.config_path(args.user, args.data_root)})")
    except config.ConfigError as e:
        print(f"{BAD} config problem:\n{e}")
        print(f"     fix: edit {paths.config_path(args.user, args.data_root)} "
              f"(or run setup_user.py --user {args.user})")
        return 1

    print(f"{OK} enabled sources: {', '.join(cfg.enabled_sources()) or '(none!)'}")
    if not cfg.enabled_sources():
        print(f"{WARN}no sources enabled — you'll get no postings.")
        warns += 1

    # 4) Profile data
    prof = root / "profile"
    export = root / cfg.get("profile.data_export_path", "profile/linkedin-export.zip")
    if cfg.get("profile.linkedin_import") == "export":
        if export.exists():
            print(f"{OK} profile source present (export)")
        else:
            print(f"{WARN}no LinkedIn export in {prof} — drafts/ranking will be generic.")
            warns += 1
    else:
        resumes = cfg.resumes()
        missing = [r["label"] for r in resumes if not (root / r["path"]).exists()]
        if not missing:
            names = ", ".join(r["label"] for r in resumes)
            print(f"{OK} résumé(s) present: {names}")
        elif len(missing) == len(resumes):
            print(f"{WARN}no résumé found in {prof} — drafts/ranking will be generic.")
            warns += 1
        else:
            print(f"{WARN}résumé missing for: {', '.join(missing)} — that résumé won't be ranked.")
            warns += 1

    # 5) Secrets (presence only)
    def has(ref):
        return bool(cfg.get_secret(ref))
    checks = []
    if cfg.get("sources.usajobs.enabled"):
        checks += [("usajobs", "USAJOBS API key"), ("usajobs_email", "USAJOBS email")]
    if cfg.get("sources.adzuna.enabled"):
        checks += [("adzuna_id", "Adzuna app id"), ("adzuna_key", "Adzuna app key")]
    if cfg.get("sources.email_alerts.enabled"):
        checks += [("smtp_user|imap_user", "IMAP username"), ("smtp_pass|imap_pass", "IMAP password")]
    if cfg.email_enabled:
        checks += [("smtp_user", "SMTP username"), ("smtp_pass", "SMTP password")]
    if cfg.get("draft.llm.enabled"):
        checks += [("anthropic", "Anthropic API key (LLM cover letters)")]
    for ref, label in checks:
        present = any(has(r) for r in ref.split("|"))
        if present:
            print(f"{OK} secret present: {label}")
        else:
            print(f"{WARN}secret missing: {label} — setup_user.py --user {args.user} --set-secret {ref.split('|')[0]}")
            warns += 1

    # 5b) LLM cover letters readiness (always shown, so it's obvious why drafts are templated)
    from copilot import llm
    lok, lwhy = llm.status(cfg)
    if lok:
        print(f"{OK} LLM cover letters: ready ({cfg.get('draft.llm.model', 'claude-haiku-4-5')})")
    else:
        print(f"{WARN}LLM cover letters: OFF — {lwhy}")
        warns += 1

    # 6) Live checks
    if args.live:
        print("\n  live checks:")
        if cfg.get("sources.usajobs.enabled") and has("usajobs") and has("usajobs_email"):
            try:
                from copilot.sources import usajobs
                jobs = usajobs.fetch(cfg)
                print(f"{OK} USAJOBS returned {len(jobs)} postings")
            except Exception as e:  # noqa: BLE001
                print(f"{BAD} USAJOBS call failed: {type(e).__name__}: {e}")
                problems += 1
        if cfg.get("sources.adzuna.enabled") and has("adzuna_id") and has("adzuna_key"):
            try:
                from copilot.sources import adzuna
                jobs = adzuna.fetch(cfg)
                print(f"{OK} Adzuna returned {len(jobs)} postings")
            except Exception as e:  # noqa: BLE001
                print(f"{BAD} Adzuna call failed: {type(e).__name__}: {e}")
                problems += 1
        if cfg.get("sources.email_alerts.enabled"):
            user = cfg.get_secret("imap_user") or cfg.get_secret("smtp_user")
            pw = cfg.get_secret("imap_pass") or cfg.get_secret("smtp_pass")
            if user and pw:
                import imaplib
                host = cfg.get("sources.email_alerts.imap_host", "imap.gmail.com")
                try:
                    M = imaplib.IMAP4_SSL(host)
                    M.login(user, pw)
                    M.logout()
                    print(f"{OK} IMAP login OK ({host})")
                except Exception as e:  # noqa: BLE001
                    print(f"{BAD} IMAP login failed: {type(e).__name__} — check app password/IMAP enabled")
                    problems += 1

    print(f"\nSummary: {problems} problem(s), {warns} warning(s).")
    if problems == 0:
        print("Ready. Try:  python scripts/run_weekly.py --user %s --dry-run" % args.user)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
