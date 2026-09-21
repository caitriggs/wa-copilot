"""
Login-gated sources — the user's OWN account only (opt-in, Phase 2/3).
======================================================================

Some boards only show postings behind a login. This module would use Playwright to sign into the
authenticated user's OWN account (credentials from the keyring, ref given by `secret_ref`) and read
their saved-search results. It is opt-in per source (config `sources.authed[].enabled`) and never
touches other members' data.

Phase 1 ships this as a guarded stub: unless enabled AND Playwright is installed, it returns [].
This keeps the weekly pipeline working without the heavier dependency.
"""

from __future__ import annotations

from ..models import JobPosting


def fetch(cfg) -> list[JobPosting]:
    entries = [e for e in (cfg.get("sources.authed", []) or []) if e.get("enabled")]
    if not entries:
        return []
    try:
        import playwright  # noqa: F401  (import check only)
    except ImportError:
        print("  [authed] enabled but Playwright not installed; skipping. "
              "Install with: python -m pip install playwright && playwright install")
        return []
    # Phase 2/3: drive a headless login per entry using cfg.get_secret(entry['secret_ref']).
    for e in entries:
        print(f"  [authed] {e.get('site')} login-gated fetch is not implemented yet (Phase 2).")
    return []
