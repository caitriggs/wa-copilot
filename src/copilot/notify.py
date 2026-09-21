"""
Notify — always write a local summary; optionally email the packet to the user.
================================================================================

The tool runs on the user's own machine, so (unlike the radrigs-ops outbox bus) it can send
email directly. Email is optional per user (config `email.enabled`); the local summary is always
written so a scheduled run leaves a durable trace even when email is off or fails.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from . import paths
from .config import Config


def _summary_path(user: str, week_end: date, data_root=None) -> Path:
    return paths.packet_dir(user, week_end, data_root) / "notify_summary.txt"


def notify(cfg: Config, packet_path: Path | None, week_end: date | None = None,
           data_root=None, subject: str | None = None) -> Path:
    """Write a local summary and, if email is enabled, send the packet. Returns summary path."""
    we = week_end or paths.week_ending()
    data_root = data_root if data_root is not None else cfg.data_root

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"wa-unemployment-copilot notify — {stamp}",
             f"user: {cfg.user}   week ending: {we.isoformat()}"]
    if packet_path and Path(packet_path).exists():
        lines.append(f"packet: {packet_path}")
    else:
        lines.append("packet: (none — not enough activities logged yet)")

    emailed = False
    if cfg.email_enabled and packet_path and Path(packet_path).exists():
        from .mailer import send, MailError  # local import so mailer stays optional
        subj = subject or f"WA weekly claim packet — week ending {we.isoformat()}"
        try:
            send(cfg, subj, Path(packet_path).read_text(encoding="utf-8"))
            emailed = True
            lines.append(f"email: sent to {cfg.email_to}")
        except MailError as e:
            lines.append(f"email: FAILED ({e})")
    elif cfg.email_enabled:
        lines.append("email: skipped (no packet to send)")
    else:
        lines.append("email: disabled in config")

    out = _summary_path(cfg.user, we, data_root)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n".join(lines))
    if cfg.email_enabled and not emailed and packet_path and Path(packet_path).exists():
        print("  (email did not send — the packet is still available locally above)")
    return out
