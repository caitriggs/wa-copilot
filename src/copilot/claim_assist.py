"""
Assisted eServices weekly-claim helper (Phase 3 — opt-in, human-driven).
========================================================================

This is the most sensitive piece, so it is deliberately conservative:

- **You** log in (SecureAccess Washington + MFA) and do all navigation in a VISIBLE browser.
  The tool never handles or stores your SAW credentials.
- The tool shows your prepared answers in an on-page **reference panel** (activities to enter +
  the attestation questions) so you can fill the form quickly and accurately.
- It **never clicks submit or certify**, and it **never auto-answers an attestation**
  (earnings / able & available / refused work / other payments). Those are certified under
  penalty of perjury — only you answer them.
- Optional best-effort auto-fill is limited to *activity facts you already logged* (employer,
  title, date, contact) into clearly-labeled text fields, and it pauses for your review. It is
  off unless `claim_assist.autofill_attempt: true`.

Gated behind `claim_assist.enabled: true` in config AND an explicit confirmation at the CLI.
Requires Playwright (`pip install playwright && playwright install chromium`).
"""

from __future__ import annotations

import html as _html
import json
from datetime import date

from . import paths, logbook
from .config import Config
from .packet import CHECKLIST, DISCLAIMER

DEFAULT_URL = "https://secure.esd.wa.gov/"

# Attestation intent we must NEVER auto-answer (perjury-sensitive). Used to keep autofill away.
ATTESTATION_MARKERS = (
    "able", "available", "earn", "wage", "hours worked", "did you work", "refuse",
    "pension", "severance", "vacation", "workers comp", "self-employ", "certify", "penalty",
)

# Activity-fact fields we may best-effort fill (never attestations).
ACTIVITY_FIELD_HINTS = {
    "employer_or_org": ("employer", "company", "organization", "business name"),
    "position": ("position", "job title", "occupation", "title of job"),
    "contact_name_or_url": ("website", "url", "web address", "contact", "how did you"),
    "date": ("date of", "date you", "activity date"),
}


def preflight(cfg: Config, confirmed: bool) -> tuple[bool, str]:
    """Return (ok, reason). Both the config flag and an explicit confirmation are required."""
    if not cfg.get("claim_assist.enabled", False):
        return False, ("claim_assist is disabled. Set `claim_assist.enabled: true` in your "
                       "config.yaml only if you want this opt-in helper.")
    if not confirmed:
        return False, "not confirmed by the user"
    return True, ""


def build_reference(cfg: Config, week_end: date | None = None, data_root=None) -> dict:
    """The prepared answers to display: logged activities + the attestation checklist."""
    we = week_end or paths.week_ending()
    data_root = data_root if data_root is not None else cfg.data_root
    rows = logbook.week_activities(cfg.user, we, data_root)
    have = logbook.count_valid(cfg.user, we, data_root)
    activities = [{
        "date": r.get("date", ""),
        "activity": logbook.ACTIVITY_TYPES.get(r.get("activity_type", ""), r.get("activity_type", "")),
        "employer_or_org": r.get("employer_or_org", ""),
        "position": r.get("position", ""),
        "contact_method": r.get("contact_method", ""),
        "contact_name_or_url": r.get("contact_name_or_url", ""),
        "result_status": r.get("result_status", ""),
    } for r in rows]
    checklist = [{"question": q, "kind": kind} for q, kind in CHECKLIST]
    return {"week": we.isoformat(), "have": have, "need": cfg.targets_per_week,
            "activities": activities, "checklist": checklist}


_PANEL_CSS = ("position:fixed;top:12px;right:12px;width:420px;max-height:88vh;overflow:auto;"
              "z-index:2147483647;background:#fff;border:2px solid #2F6F4E;border-radius:10px;"
              "box-shadow:0 6px 24px rgba(0,0,0,.25);font:13px/1.45 -apple-system,Segoe UI,Arial,"
              "sans-serif;color:#1c1c1c;padding:14px;")


def _panel_html(ref: dict) -> str:
    """A self-contained floating reference panel injected into the eServices page.

    The HTML is built in Python then JSON-encoded for safe embedding as a JS string literal; the
    close handler is attached via an event listener (no fragile inline-onclick quoting).
    """
    def esc(s):
        return _html.escape(str(s))

    rows = ""
    for a in ref["activities"]:
        rows += (
            "<tr>"
            f"<td>{esc(a['date'])}</td><td>{esc(a['activity'])}</td>"
            f"<td>{esc(a['employer_or_org'])}</td><td>{esc(a['position'])}</td>"
            f"<td>{esc(a['contact_method'])}</td><td>{esc(a['contact_name_or_url'])}</td>"
            "</tr>"
        )
    checks = ""
    for c in ref["checklist"]:
        tag = "PRE-FILLED" if c["kind"] == "prefilled" else "YOU CONFIRM"
        color = "#2F6F4E" if c["kind"] == "prefilled" else "#9a3b1b"
        checks += (f'<li><span style="color:{color};font-weight:700">[{tag}]</span> '
                   f'{esc(c["question"])}</li>')

    inner = (
        '<div style="display:flex;justify-content:space-between;align-items:center">'
        f'<strong style="font-size:15px">Weekly claim helper — week {esc(ref["week"])}</strong>'
        '<button id="wauc-close" style="border:0;background:#eee;border-radius:6px;padding:2px 8px;'
        'cursor:pointer">&times;</button></div>'
        '<p style="margin:6px 0;color:#9a3b1b;font-weight:700">This helper never submits or '
        'certifies. You enter the answers and certify yourself.</p>'
        f'<p style="margin:6px 0">Activities this week: <strong>{ref["have"]}</strong> '
        f'(minimum {ref["need"]}).</p>'
        '<table style="border-collapse:collapse;width:100%;font-size:12px" border="1" cellpadding="4">'
        '<tr style="background:#f0efe9"><th>Date</th><th>Activity</th><th>Employer</th>'
        f'<th>Position</th><th>Method</th><th>Contact/URL</th></tr>{rows}</table>'
        '<p style="margin:10px 0 4px;font-weight:700">Weekly-claim questions</p>'
        f'<ul style="margin:0;padding-left:18px">{checks}</ul>'
    )

    return (
        "(function(){"
        "var old=document.getElementById('wauc-panel'); if(old) old.remove();"
        "var d=document.createElement('div'); d.id='wauc-panel';"
        f"d.style.cssText={json.dumps(_PANEL_CSS)};"
        f"d.innerHTML={json.dumps(inner)};"
        "document.body.appendChild(d);"
        "var c=document.getElementById('wauc-close'); if(c){c.onclick=function(){d.remove();};}"
        "})();"
    )


def _looks_like_attestation(label: str) -> bool:
    low = (label or "").lower()
    return any(m in low for m in ATTESTATION_MARKERS)


def assist(cfg: Config, week_end: date | None = None, data_root=None,
           autofill: bool = False, url: str | None = None) -> int:
    """Run the guided browser assist. Returns an exit code. Requires Playwright."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright is not installed. Run:\n"
              "  python -m pip install playwright\n  playwright install chromium")
        return 5

    ref = build_reference(cfg, week_end, data_root)
    target = url or cfg.get("claim_assist.url", DEFAULT_URL)

    print("\n" + "=" * 72)
    print("ASSISTED WEEKLY-CLAIM HELPER")
    print(DISCLAIMER)
    print("=" * 72)
    print(f"Week ending {ref['week']}: {ref['have']} activities logged (min {ref['need']}).")
    if ref["have"] < ref["need"]:
        print(f"  WARNING: below the ESD minimum — log more before you file "
              f"(python scripts/log_activity.py --user {cfg.user}).")
    print("\nYour logged activities (also shown in the on-page panel):")
    for a in ref["activities"]:
        print(f"  - {a['date']}  {a['activity']}  {a['employer_or_org']}  {a['contact_name_or_url']}")
    print("\nA visible browser will open. YOU log in (SAW + MFA) and open your weekly claim.")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=cfg.get("claim_assist.headless", False))
        context = browser.new_context()          # ephemeral: no stored credentials/cookies
        page = context.new_page()
        try:
            page.goto(target)
        except Exception as e:  # noqa: BLE001
            print(f"Could not open {target}: {type(e).__name__}: {e}")

        input("\nLog in and open your weekly claim, then press Enter to load the helper panel... ")
        try:
            page.evaluate(_panel_html(ref))
            print("Reference panel loaded in the browser (top-right).")
        except Exception as e:  # noqa: BLE001
            print(f"Could not inject the panel ({type(e).__name__}); use the terminal list above.")

        if autofill and cfg.get("claim_assist.autofill_attempt", False):
            _autofill_activity_fields(page, ref)
        elif autofill:
            print("autofill requested but claim_assist.autofill_attempt is false — skipping "
                  "(panel-only). Enable it in config to try best-effort fills.")

        input("\nEnter your answers, review everything, and certify yourself in the browser.\n"
              "This tool will NOT submit or certify. Press Enter here to close the browser... ")
        context.close()
        browser.close()
    print("Closed. Nothing was submitted or certified by this tool.")
    return 0


def _autofill_activity_fields(page, ref: dict) -> None:
    """Best-effort: fill clearly-labeled ACTIVITY text fields only; never attestations/buttons."""
    if not ref["activities"]:
        print("  [autofill] no logged activities to fill.")
        return
    print("  [autofill] best-effort — filling only clearly-labeled activity fields, one confirm each.")
    try:
        inputs = page.query_selector_all("input[type=text], textarea")
    except Exception as e:  # noqa: BLE001
        print(f"  [autofill] could not read fields: {type(e).__name__}")
        return

    a = ref["activities"][0]  # fill from the first activity as an example; user extends the rest
    for el in inputs:
        try:
            label = (el.get_attribute("aria-label") or el.get_attribute("name")
                     or el.get_attribute("placeholder") or "")
            if not label or _looks_like_attestation(label):
                continue
            low = label.lower()
            value = None
            for field, hints in ACTIVITY_FIELD_HINTS.items():
                if any(h in low for h in hints):
                    value = a.get(field, "")
                    break
            if not value:
                continue
            resp = input(f"  fill field '{label[:40]}' with '{value}'? [y/N] ").strip().lower()
            if resp == "y":
                el.fill(value)
        except Exception:
            continue
    print("  [autofill] done. Review every field; the tool touched no attestations or buttons.")
