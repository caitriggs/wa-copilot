"""
Weekly claim-ready packet.
==========================

Assembles packets/<week>/claim_packet.md from the confirmed job-search log for a claim week:
  - your logged activities in ESD format (pre-filled), and
  - a checklist mirroring the eServices weekly-claim questions, clearly separating what is
    PRE-FILLED FROM YOUR LOG from what ONLY YOU CAN CONFIRM (earnings, able & available, etc.).

It refuses to build a packet if the week has fewer than `targets_per_week` valid activities, so
you never head to eServices short of the ESD minimum. It never marks any attestation as final.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from . import paths, logbook, metrics
from .config import Config

DISCLAIMER = (
    "This packet is preparation only. YOU submit and certify the weekly claim in eServices. "
    "The claim is a certification under penalty of perjury — answer every attestation yourself, "
    "truthfully. This tool never submits or certifies on your behalf."
)

# Weekly-claim questions and whether the tool can pre-fill them.
CHECKLIST = [
    ("Did you look for work? (list your job-search contacts)", "prefilled"),
    ("Were you able and available for work all week?", "confirm"),
    ("Did you work or earn any money this week? Gross earnings?", "confirm"),
    ("Did you refuse any work or job referral?", "confirm"),
    ("Did you receive other payments (pension, severance, PTO, workers' comp)?", "confirm"),
    ("Are you able and willing to continue claiming?", "confirm"),
]


class NotEnoughActivities(RuntimeError):
    """Raised when the week has fewer valid activities than targets_per_week."""

    def __init__(self, have: int, need: int, week: str):
        self.have, self.need, self.week = have, need, week
        super().__init__(
            f"Week {week}: only {have} of {need} required job-search activities logged. "
            f"Log more with: python scripts/log_activity.py (activities can't carry over weeks)."
        )


def _activities_table(rows: list[dict]) -> str:
    if not rows:
        return "_No activities logged._\n"
    head = "| Date | Activity | Employer/Org | Position | Method | Contact / URL | Result |\n"
    sep = "|---|---|---|---|---|---|---|\n"
    body = ""
    for r in sorted(rows, key=lambda x: str(x.get("date", ""))):
        label = logbook.ACTIVITY_TYPES.get(r.get("activity_type", ""), r.get("activity_type", ""))
        body += (
            f"| {r.get('date','')} | {label} | {r.get('employer_or_org','')} "
            f"| {r.get('position','')} | {r.get('contact_method','')} "
            f"| {r.get('contact_name_or_url','')} | {r.get('result_status','')} |\n"
        )
    return head + sep + body


def build(cfg: Config, week_end: date | None = None, data_root=None) -> Path:
    """Build the claim packet for the week; raise NotEnoughActivities if short. Returns the path."""
    we = week_end or paths.week_ending()
    week = we.isoformat()
    user = cfg.user
    data_root = data_root if data_root is not None else cfg.data_root

    rows = logbook.week_activities(user, we, data_root)
    have = logbook.count_valid(user, we, data_root)
    need = cfg.targets_per_week
    if have < need:
        raise NotEnoughActivities(have, need, week)

    out_dir = paths.packet_dir(user, we, data_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "claim_packet.md"

    lines = []
    lines.append(f"# WA Weekly Claim Packet — week ending {week}")
    lines.append(f"\n_User: {cfg.get('display_name', user)}. Prepared by wa-unemployment-copilot._\n")
    lines.append(f"> {DISCLAIMER}\n")

    lines.append("## Job-search activities this week (pre-filled from your log)\n")
    lines.append(f"You have **{have}** logged activities (ESD minimum: **{need}**).\n")
    lines.append(_activities_table(rows))

    lines.append("\n" + metrics.render_markdown(cfg, we, data_root))

    lines.append("\n## Weekly-claim answer checklist\n")
    lines.append("Answer each in eServices yourself. ✅ = the tool pre-filled this from your log; "
                 "✍️ = only you can confirm.\n")
    for question, kind in CHECKLIST:
        mark = "✅" if kind == "prefilled" else "✍️"
        if kind == "prefilled":
            lines.append(f"- {mark} **{question}** Yes — see the {have} activities above.")
        else:
            lines.append(f"- {mark} **{question}** ____ (you confirm)")
    lines.append("")

    lines.append("## Submit")
    lines.append(
        "1. Log into eServices via SecureAccess Washington: https://secure.esd.wa.gov/\n"
        "2. Open your weekly claim and answer each question above.\n"
        "3. Enter your job-search activities from the table.\n"
        "4. Review, then submit + certify. Keep this packet and your log for your records.\n"
    )

    out.write_text("\n".join(lines), encoding="utf-8")
    return out
