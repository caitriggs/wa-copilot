"""
Per-user paths — all data lives under the user's local Desktop, never in the repo or a cloud drive.
====================================================================================================

WHY: The tool is multi-user. Each person gets an isolated data folder under THEIR OWN Desktop:

    <Desktop>/wa-unemployment-copilot/<user>/

On Windows the Desktop may be the classic `%USERPROFILE%\\Desktop` or a OneDrive-redirected
`%USERPROFILE%\\OneDrive\\Desktop`; both are handled. Data must never be written to a cloud-synced
drive (G:/H:/Google Drive/etc.), so `validate_local()` refuses those paths.

TESTING / OVERRIDE: set WA_UI_DATA_ROOT to use an explicit base dir (skips Desktop resolution);
set WA_UI_DESKTOP to override just the Desktop location. Tests use WA_UI_DATA_ROOT -> a temp dir.
"""

from __future__ import annotations

import os
import re
from datetime import date, timedelta
from pathlib import Path

APP_DIRNAME = "wa-unemployment-copilot"
SUBDIRS = ("profile", "postings_cache", "drafts", "log", "packets", "applications")

# Drive letters / path markers we refuse to store data on (cloud-synced or explicitly excluded).
_FORBIDDEN_DRIVES = {"G:", "H:"}
_FORBIDDEN_MARKERS = ("google drive", "my drive", "\\gdrive", "dropbox", "/gdrive")

_SAFE_USER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


class UnsafeDataLocation(ValueError):
    """Raised when a resolved data path would land on an excluded/cloud drive."""


def safe_user(user: str) -> str:
    """Validate and normalize a user id used as a folder name."""
    u = (user or "").strip().lower()
    if not _SAFE_USER_RE.match(u):
        raise ValueError(
            f"Invalid user id {user!r}. Use lowercase letters, digits, '-' or '_' (max 32)."
        )
    return u


def validate_local(path: Path) -> Path:
    """Raise UnsafeDataLocation if `path` is on an excluded/cloud-synced drive.

    Works cross-platform: on POSIX `Path("G:/x").drive` is "", so we also detect a leading
    `<letter>:` from the raw string. That keeps the guard correct on Windows and testable on Linux.
    """
    p = Path(path)
    raw = str(path)
    m = re.match(r"^([A-Za-z]):", raw)
    drive = (m.group(1).upper() + ":") if m else (p.drive or "").upper()
    if drive in _FORBIDDEN_DRIVES:
        raise UnsafeDataLocation(
            f"Refusing to use {p}: data must stay on the local C: drive, not {drive} "
            f"(cloud/shared drives are excluded)."
        )
    low = str(p).replace("/", "\\").lower()
    if any(m in low for m in _FORBIDDEN_MARKERS):
        raise UnsafeDataLocation(
            f"Refusing to use {p}: it looks like a cloud-synced folder. Data must stay local."
        )
    return p


def desktop_dir() -> Path:
    """Resolve the current user's Desktop (Windows classic or OneDrive-redirected)."""
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
    # Nothing exists yet (fresh machine / non-Windows dev): default to ~/Desktop and let
    # ensure_scaffold create it.
    return home / "Desktop"


def app_root(data_root: str | os.PathLike | None = None) -> Path:
    """Base folder that holds every user's data: <data_root or Desktop>/wa-unemployment-copilot."""
    env_root = os.environ.get("WA_UI_DATA_ROOT")
    if data_root is not None:
        base = Path(data_root)
    elif env_root:
        base = Path(env_root)
    else:
        base = desktop_dir() / APP_DIRNAME
    return validate_local(base)


def user_root(user: str, data_root: str | os.PathLike | None = None) -> Path:
    """This user's isolated data folder."""
    return app_root(data_root) / safe_user(user)


def ensure_scaffold(user: str, data_root: str | os.PathLike | None = None) -> Path:
    """Create the user's data folder and standard subdirectories if missing. Returns the root."""
    root = user_root(user, data_root)
    root.mkdir(parents=True, exist_ok=True)
    for sub in SUBDIRS:
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


# --- ESD claim-week helpers (weeks run Sunday..Saturday; the week is named by its Saturday) ---

def week_ending(d: date | None = None) -> date:
    """The Saturday that ends the Sun..Sat claim week containing `d` (default: today)."""
    d = d or date.today()
    wd = d.weekday()                 # Mon=0 .. Sun=6
    if wd == 5:                      # Saturday
        delta = 0
    elif wd == 6:                    # Sunday -> next Saturday
        delta = 6
    else:                            # Mon..Fri
        delta = 5 - wd
    return d + timedelta(days=delta)


def week_slug(week_end: date | None = None) -> str:
    """Folder-safe label for a claim week, e.g. '2026-07-25'."""
    we = week_end if isinstance(week_end, date) else week_ending(week_end)
    return we.isoformat()


# Convenience accessors for the standard files/folders.
def config_path(user: str, data_root=None) -> Path:
    return user_root(user, data_root) / "config.yaml"


def secrets_env_path(user: str, data_root=None) -> Path:
    return user_root(user, data_root) / "secrets.env"


def weekly_notes_path(user: str, data_root=None) -> Path:
    """Steering file for the week's focus, written by the review-dashboard round-trip and read by
    Config.weekly_notes. Lets the user update `weekly_notes` from the dashboard without editing
    config.yaml."""
    return user_root(user, data_root) / "weekly_notes.txt"


def log_csv_path(user: str, data_root=None) -> Path:
    return user_root(user, data_root) / "log" / "job_search_log.csv"


def drafts_dir(user: str, week_end: date | None = None, data_root=None) -> Path:
    return user_root(user, data_root) / "drafts" / week_slug(week_end)


def packet_dir(user: str, week_end: date | None = None, data_root=None) -> Path:
    return user_root(user, data_root) / "packets" / week_slug(week_end)


def applications_dir(user: str, week_end: date | None = None, data_root=None) -> Path:
    return user_root(user, data_root) / "applications" / week_slug(week_end)


def postings_cache_path(user: str, week_end: date | None = None, data_root=None) -> Path:
    return user_root(user, data_root) / "postings_cache" / f"{week_slug(week_end)}.json"
