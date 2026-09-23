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
    # Same precedence as copilot/paths.py: explicit --data-root, else WA_UI_DATA_ROOT (CI/instance
    # checkout), else <Desktop>/wa-unemployment-copilot.
    root = data_root or os.environ.get("WA_UI_DATA_ROOT")
    base = Path(root) if root else desktop_dir() / APP_DIRNAME
    return base / user


def _clean_cf_value(v):
    """Tolerate a value pasted WITH its header-name prefix, e.g.
    'CF-Access-Client-Id: <id>' or 'CF-Access-Client-Secret:  <secret>' — strip the label +
    surrounding whitespace so the raw token is used."""
    v = (v or "").strip()
    low = v.lower()
    for pref in ("cf-access-client-id:", "cf-access-client-secret:"):
        if low.startswith(pref):
            return v[len(pref):].strip()
    return v


def cf_access_headers() -> dict[str, str]:
    client_id = _clean_cf_value(os.environ.get("WA_COPILOT_CF_ACCESS_CLIENT_ID"))
    client_secret = _clean_cf_value(os.environ.get("WA_COPILOT_CF_ACCESS_CLIENT_SECRET"))
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


def _extract_doc_text(filename: str, data_b64: str, max_chars: int = 6000) -> str:
    """Decode an uploaded steering doc and pull its text. Prefers this repo's résumé parser
    (handles PDF via pdfplumber + DOCX + text); falls back to stdlib DOCX/text if the copilot
    package isn't importable (PDF then needs the package). Returns "" on failure. Capped so a big
    document stays a reasonable steering signal."""
    import base64
    import tempfile
    raw = base64.b64decode(data_b64)
    suffix = (Path(filename).suffix or ".txt").lower()
    tmp = Path(tempfile.mkstemp(suffix=suffix)[1])
    tmp.write_bytes(raw)
    try:
        try:
            repo_src = Path(__file__).resolve().parent.parent / "src"
            if str(repo_src) not in sys.path:
                sys.path.insert(0, str(repo_src))
            from copilot.profile_import import _resume_text
            text = _resume_text(tmp)
        except Exception:  # noqa: BLE001 — copilot not importable; stdlib fallback
            if suffix == ".docx":
                import zipfile
                import re
                import html as _html
                with zipfile.ZipFile(tmp) as z:
                    xml = z.read("word/document.xml").decode("utf-8", "ignore")
                xml = re.sub(r"</w:p>", "\n", xml)
                xml = re.sub(r"<[^>]+>", "", xml)
                text = _html.unescape(xml)
            else:
                text = raw.decode("utf-8", "replace")
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    return (text or "").strip()[:max_chars]


def _normalize_worker_url(url):
    """Accept a Worker URL saved without a scheme (e.g. 'x.workers.dev') by assuming https://."""
    url = (url or "").strip()
    if url and not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    return url


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user", required=True, help="copilot user id (matches <Desktop>/wa-unemployment-copilot/<user>)")
    ap.add_argument("--worker-url", default=os.environ.get("WA_COPILOT_WORKER_URL"),
                    help="dashboard Worker base URL (default: env WA_COPILOT_WORKER_URL)")
    ap.add_argument("--data-root", default=None, help="override data root (default: <Desktop>/wa-unemployment-copilot)")
    args = ap.parse_args()
    args.worker_url = _normalize_worker_url(args.worker_url)

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

    # A steering DOC uploaded more recently than the typed note (or when no note was ever typed)
    # wins — "latest source steers the week." Extract its text into weekly_notes.txt.
    doc = data.get("doc")
    note_ts = data.get("updated_at")
    doc_ts = (doc or {}).get("uploaded_at")
    if doc and doc_ts and (not note_ts or doc_ts > note_ts):
        try:
            text = _extract_doc_text(doc.get("filename", "upload"), doc.get("data_b64", ""))
        except Exception as e:  # noqa: BLE001
            print(f"[fetch_notes] could not read uploaded steer-doc ({type(e).__name__}: {e}); "
                  "falling back to the typed note.", file=sys.stderr)
            text = ""
        if text:
            name = doc.get("filename", "upload")
            framed = (f"Steer this week's search toward roles that fit this uploaded document "
                      f"({name}):\n\n{text}")
            path = write_note(args.user, framed, args.data_root)
            print(f"[fetch_notes] steering from uploaded file {name!r} -> {path} ({len(text)} chars)")
            return 0
        # extraction empty/failed -> fall through to the typed-note behavior below.

    # Never set on the dashboard -> don't clobber a config.yaml note with an empty override.
    if not note_ts:
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
