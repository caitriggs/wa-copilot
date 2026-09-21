#!/bin/bash
#
# SessionStart hook — provision a Claude Code on the web session AFTER the clone.
# =============================================================================
# WHY THIS EXISTS (and why the environment "setup script" field can't do it):
#   The environment setup script runs to provision the VM *before* the repo is
#   reliably in place, so pointing it at a repo-relative path like
#   `bash scripts/cloud_setup.sh` fails with exit 127 / "No such file or
#   directory". SessionStart hooks run AFTER Claude Code launches — i.e. after
#   the repo is cloned — and expose $CLAUDE_PROJECT_DIR, so a repo-committed
#   script resolves regardless of the session's working directory.
#
# WHAT IT DOES: delegates to scripts/cloud_setup.sh (single source of truth —
#   installs requirements.txt and repairs the cffi native backend), run from the
#   repo root. Cloud-only: it exits immediately in local sessions so your laptop
#   is untouched.
# =============================================================================
set -uo pipefail

# Cloud sessions only. $CLAUDE_CODE_REMOTE is "true" in Claude Code on the web,
# never set locally, so a local session start does nothing here.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# $CLAUDE_PROJECT_DIR is the repository root; fall back to the hook's own
# location so the script still resolves if the var is ever unset.
ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
cd "$ROOT" || { echo "!! could not cd to project root ($ROOT)" >&2; exit 1; }

if [ -f scripts/cloud_setup.sh ]; then
  bash scripts/cloud_setup.sh
else
  echo "!! scripts/cloud_setup.sh not found under $ROOT — skipping provisioning" >&2
  exit 1
fi
