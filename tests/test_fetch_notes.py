"""Tests for scripts/fetch_notes.py — the dashboard 'steer next week' note -> weekly_notes.txt.

The Worker GET is always stubbed (no network); only the local write/skip behavior is exercised.
"""

import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "fetch_notes.py"


def _load():
    spec = importlib.util.spec_from_file_location("fetch_notes", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fetch_notes = _load()


def test_write_note_creates_file(tmp_path):
    p = fetch_notes.write_note("max", "lean marketing analytics", data_root=str(tmp_path))
    assert p == tmp_path / "max" / "weekly_notes.txt"
    assert p.read_text(encoding="utf-8") == "lean marketing analytics"


def test_main_writes_note_when_set(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WA_COPILOT_PUBLISH_TOKEN", "tok")
    monkeypatch.setattr(sys, "argv", [
        "fetch_notes.py", "--user", "max", "--worker-url", "https://w", "--data-root", str(tmp_path),
    ])
    monkeypatch.setattr(fetch_notes, "fetch_notes",
                        lambda url, token: {"notes": "explore WA State jobs", "updated_at": "2026-08-01T00:00:00Z"})
    assert fetch_notes.main() == 0
    assert (tmp_path / "max" / "weekly_notes.txt").read_text(encoding="utf-8") == "explore WA State jobs"


def test_main_skips_when_never_set(tmp_path, monkeypatch):
    monkeypatch.setenv("WA_COPILOT_PUBLISH_TOKEN", "tok")
    monkeypatch.setattr(sys, "argv", [
        "fetch_notes.py", "--user", "max", "--worker-url", "https://w", "--data-root", str(tmp_path),
    ])
    monkeypatch.setattr(fetch_notes, "fetch_notes",
                        lambda url, token: {"notes": "", "updated_at": None})
    assert fetch_notes.main() == 0
    assert not (tmp_path / "max" / "weekly_notes.txt").exists()   # config note left untouched


def test_main_writes_empty_when_cleared(tmp_path, monkeypatch):
    monkeypatch.setenv("WA_COPILOT_PUBLISH_TOKEN", "tok")
    monkeypatch.setattr(sys, "argv", [
        "fetch_notes.py", "--user", "max", "--worker-url", "https://w", "--data-root", str(tmp_path),
    ])
    # Note was set then cleared on the dashboard (updated_at present, notes empty) -> clear locally.
    monkeypatch.setattr(fetch_notes, "fetch_notes",
                        lambda url, token: {"notes": "", "updated_at": "2026-08-02T00:00:00Z"})
    assert fetch_notes.main() == 0
    assert (tmp_path / "max" / "weekly_notes.txt").read_text(encoding="utf-8") == ""


def test_main_clears_stale_note_when_reset_to_blank(tmp_path, monkeypatch):
    """Regression for the blank-note-doesn't-reset bug: a PREVIOUSLY-saved note must actually be
    overwritten to empty on the next fetch, not left stale — otherwise "leave blank to rank on
    your résumé & usual titles" silently keeps steering on the old note forever."""
    monkeypatch.setenv("WA_COPILOT_PUBLISH_TOKEN", "tok")
    monkeypatch.setattr(sys, "argv", [
        "fetch_notes.py", "--user", "max", "--worker-url", "https://w", "--data-root", str(tmp_path),
    ])
    note_path = tmp_path / "max" / "weekly_notes.txt"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text("explore WA State jobs", encoding="utf-8")   # stale note from a prior week

    monkeypatch.setattr(fetch_notes, "fetch_notes",
                        lambda url, token: {"notes": "", "updated_at": "2026-08-09T00:00:00Z"})
    assert fetch_notes.main() == 0
    assert note_path.read_text(encoding="utf-8") == ""   # overwritten, not left stale


def test_main_writes_empty_when_note_is_whitespace_only(tmp_path, monkeypatch):
    """Even if the Worker ever returned unTrimmed whitespace for a "cleared" box (e.g. the user
    left stray spaces/newlines after deleting their text), fetch_notes.py must still resolve it
    to a truly empty weekly_notes.txt — the pipeline's `Config.weekly_notes` also strips, but this
    guarantees the clearing behavior doesn't depend on that second strip alone."""
    monkeypatch.setenv("WA_COPILOT_PUBLISH_TOKEN", "tok")
    monkeypatch.setattr(sys, "argv", [
        "fetch_notes.py", "--user", "max", "--worker-url", "https://w", "--data-root", str(tmp_path),
    ])
    note_path = tmp_path / "max" / "weekly_notes.txt"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text("explore WA State jobs", encoding="utf-8")

    monkeypatch.setattr(fetch_notes, "fetch_notes",
                        lambda url, token: {"notes": "   \n\t  ", "updated_at": "2026-08-09T00:00:00Z"})
    assert fetch_notes.main() == 0
    assert note_path.read_text(encoding="utf-8") == ""


def test_main_requires_worker_url(tmp_path, monkeypatch):
    monkeypatch.setenv("WA_COPILOT_PUBLISH_TOKEN", "tok")
    monkeypatch.delenv("WA_COPILOT_WORKER_URL", raising=False)
    monkeypatch.setattr(sys, "argv", ["fetch_notes.py", "--user", "max", "--data-root", str(tmp_path)])
    assert fetch_notes.main() == 3
