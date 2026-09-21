"""
Pull the dashboard's "steer next week" note to a local weekly_notes.txt on RADMACHINE.
=====================================================================================

SETUP: run at the START of the weekly task on RADMACHINE, BEFORE `run_weekly.py --steps discover`,
so a note the user saved on the review dashboard steers this run's search + ranking. Stdlib-only
(urllib).

WHAT THIS DOES: GETs `/api/notes` from the review-dashboard Worker (the note the user saved via
the dashboard's "Steer next week's search" box) and writes it to that user's local
`<user>/weekly_notes.txt`. `copilot.config.Config.weekly_notes` reads that file and it OVERRIDES
`search.weekly_notes` in config.yaml, so the note takes effect on the next discover run without
anyone editing the config.

  - If the dashboard note has never been set (the Worker returns updated_at: null), this leaves
    any existing weekly_notes.txt / config value untouched — it won't clobber a config-set note
    with an empty override.
  - If the note has been set (even cleared to empty on purpose), the local file is rewritten to
    match, so clearing it on the dashboard clears the week's focus.

USAGE:
    python fetch_notes.py --user demo --worker-url https://wa-copilot-dashboard.<subdomain>.workers.dev
    # writes <user>/weekly_notes.txt and prints a summary

Reads the bearer token from env WA_COPILOT_PUBLISH_TOKEN (never pass it on the command line).
If the Worker route is also fronted by Cloudflare Access, set WA_COPILOT_CF_ACCESS_CLIENT_ID and
WA_COPILOT_CF_ACCESS_CLIENT_SECRET (a Cloudflare Access service token) so requests carry the
CF-Access-Client-Id/CF-Access-Client-Secret headers alongside the bearer token; both optional.

EXIT CODES: 0 ok, 2 network/HTTP error, 3 bad arguments.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

APP_DIRNAME = "wa-unemployment-copilot"


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


def cf_access_headers() -> dict[str, str]:
    client_id = os.environ.get("WA_COPILOT_CF_ACCESS_CLIENT_ID")
    client_secret = os.environ.get("WA_COPILOT_CF_ACCESS_CLIENT_SECRET")
    if client_id and client_secret:
        return {"CF-Access-Client-Id": client_id, "CF-Access-Client-Secret": client_secret}
    return {}


def fetch_notes(worker_url: str, token: str) -> dict:
    url = f"{worker_url.rstrip('/')}/api/notes"
    headers = {"Authorization": f"Bearer {token}", "User-Agent": "wa-copilot-publisher/1.0"}
    headers.update(cf_access_headers())
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def write_note(user: str, note: str, data_root: str | None = None) -> Path:
    path = user_root(user, data_root) / "weekly_notes.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(note, encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user", required=True, help="copilot user id (matches <Desktop>/wa-unemployment-copilot/<user>)")
    ap.add_argument("--worker-url", default=os.environ.get("WA_COPILOT_WORKER_URL"),
                    help="dashboard Worker base URL (default: env WA_COPILOT_WORKER_URL)")
    ap.add_argument("--data-root", default=None, help="override data root (default: <Desktop>/wa-unemployment-copilot)")
    args = ap.parse_args()

    if not args.worker_url:
        print("[fetch_notes] --worker-url or env WA_COPILOT_WORKER_URL is required", file=sys.stderr)
        return 3
    token = os.environ.get("WA_COPILOT_PUBLISH_TOKEN")
    if not token:
        print("[fetch_notes] env WA_COPILOT_PUBLISH_TOKEN is required", file=sys.stderr)
        return 3

    try:
        data = fetch_notes(args.worker_url, token)
    except urllib.error.URLError as e:
        print(f"[fetch_notes] failed to reach Worker: {e}", file=sys.stderr)
        return 2

    # Never set on the dashboard -> don't clobber a config.yaml note with an empty override.
    if not data.get("updated_at"):
        print("[fetch_notes] no dashboard note set yet — leaving weekly_notes.txt unchanged.")
        return 0

    note = (data.get("notes") or "").strip()
    path = write_note(args.user, note, args.data_root)
    if note:
        preview = note if len(note) <= 80 else note[:79] + "…"
        print(f"[fetch_notes] wrote steering note -> {path}\n              {preview!r}")
    else:
        print(f"[fetch_notes] dashboard note is empty (focus cleared) -> wrote empty {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
