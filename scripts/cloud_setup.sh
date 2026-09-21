#!/usr/bin/env bash
#
# cloud_setup.sh — environment setup for Claude Code on the web
# =============================================================================
# SETUP:  Paste the contents of this file into your Claude Code environment's
#         "setup script" field, OR set the field to:  bash scripts/cloud_setup.sh
# USAGE:  Runs automatically at container start for every new session in that
#         environment. Idempotent — safe to re-run by hand.
# WHAT IT DOES:
#   1. Installs Python deps from requirements.txt (incl. dev: pytest).
#   2. Repairs the cffi native backend that keyring/cryptography import at
#      test time — without it, tests/test_publish_week.py fails with
#      "ModuleNotFoundError: No module named '_cffi_backend'".
#   3. Reports whether WA_COPILOT_EMAIL_FROM is present (informational only).
# NON-FATAL: optional/native repairs never abort the session (project value:
#            "optional deps degrade gracefully"). A broken requirements.txt
#            install DOES fail loudly, because nothing works without it.
# =============================================================================

set -uo pipefail
export PIP_ROOT_USER_ACTION=ignore   # quiet the "pip as root" warning
export PIP_DISABLE_PIP_VERSION_CHECK=1

echo "==> Python: $(python --version 2>&1)"

# 1. Core + dev dependencies -------------------------------------------------
echo "==> Installing requirements.txt"
if ! pip install -q -r requirements.txt; then
  echo "!! requirements.txt install failed — the repo cannot run without these." >&2
  exit 1
fi

# 2. Native cffi backend (keyring -> cryptography -> _cffi_backend) ----------
if python -c "import _cffi_backend" 2>/dev/null; then
  echo "==> cffi backend OK"
else
  echo "==> Repairing cffi native backend"
  pip install -q --force-reinstall cffi || echo "!! cffi repair failed (keyring tests may fail)" >&2
  python -c "import _cffi_backend" 2>/dev/null \
    && echo "==> cffi backend OK" \
    || echo "!! _cffi_backend still missing — test_publish_week.py will fail" >&2
fi

# 3. Informational: email readiness ------------------------------------------
# These are read by copilot.config / copilot.mailer (env fallback for a machine with no keyring or
# secrets.env). NOTE: email sends over Gmail SMTP (587), which cloud environments typically block —
# so email is tested on a machine with those ports open (e.g. RADMACHINE), not from a cloud session.
echo "==> Email env readiness (for reference — SMTP send won't work from a cloud session):"
[ -n "${WA_COPILOT_EMAIL_FROM:-}" ]         && echo "    from: ${WA_COPILOT_EMAIL_FROM}"     || echo "    from: (WA_COPILOT_EMAIL_FROM unset)"
[ -n "${WA_COPILOT_EMAIL_TO:-}" ]           && echo "    to:   ${WA_COPILOT_EMAIL_TO}"       || echo "    to:   (WA_COPILOT_EMAIL_TO unset)"
[ -n "${WA_COPILOT_GMAIL_APP_PASSWORD:-}" ] && echo "    smtp pass: set"                     || echo "    smtp pass: (WA_COPILOT_GMAIL_APP_PASSWORD unset)"

echo "==> Setup complete. Verify with:  python -m pytest -q"
