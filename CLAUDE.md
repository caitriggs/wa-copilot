# wa-unemployment-copilot

Modular, multi-user tooling that prepares each week's Washington State unemployment
job-search requirements. It finds real jobs, drafts applications for review, logs the
activities the user actually completed in ESD format, and assembles a claim-ready packet.

## Guardrails (these override any convenience)

- **The weekly claim is the hard line.** The tool NEVER submits or certifies the WA weekly
  claim. That claim is a certification under penalty of perjury (earnings, able & available,
  genuine work search) — a machine must not sign it. `claim_assist` only *assists*; the user
  certifies.
- **No auto-fill, no auto-submit.** The Chrome-autofill / Cowork-executor apply automation
  (`docs/cowork-apply-task.md`, `apply.mode: live`) is deprecated and dropped as the primary
  flow — too failure-prone and desktop-bound. The tool ranks top matches, the user approves them
  on the review dashboard, and it emails her the title + application link for each so she applies
  herself. `src/copilot/apply.py`'s `stage` mode may still be used to write a local
  `queue.md` brief, but nothing auto-fills or auto-submits a form.
- **The log is truthful.** Only activities the user genuinely completed are logged, and only via
  her own confirmation (`log_activity.py`). Never log an application that wasn't really
  submitted.
- **Data lives locally, never in the repo.** All per-user data lives under that user's
  **Desktop** (`<Desktop>/wa-unemployment-copilot/<user>/`). Never write data to the repo, and
  never to G:/H:/any cloud-synced drive (`paths.py` enforces this).
- **Never commit credentials.** Site logins and SMTP passwords live in the OS keyring
  (preferred) or a gitignored local `secrets.env`. The config file holds only a `secret_ref`.
- **Respect source terms.** Prefer official APIs (USAJOBS) and the user's own saved-search
  email/RSS alerts. Login-gated fetch is limited to the authenticated user's OWN account.

## Layout

- `src/copilot/` — the package (paths, config, discover, draft, logbook, packet, notify, mailer).
- `src/copilot/sources/` — one module per job source; each exposes `fetch(cfg) -> list[JobPosting]`.
- `scripts/` — `setup_user.py` (onboard a user), `run_weekly.py` (the weekly pipeline),
  `log_activity.py` (interactive confirm-then-log), `doctor.py` (setup preflight),
  `claim_assist.py` (opt-in guided eServices helper — never submits/certifies),
  `publish_week.py` / `fetch_approvals.py` / `fetch_notes.py` (RADMACHINE <-> review dashboard
  bridge, see below; `fetch_notes.py` pulls the dashboard's "steer next week" note to
  `weekly_notes.txt` before discover).
- `config/user.example.yaml` — copy per user; the only tracked config. Comp filtering is a single
  floor, `search.comp_min` (no ceiling) — see `discover.py:_comp_fit()`.
- `tests/` — pytest; no network, no real Desktop writes (uses `WA_UI_DATA_ROOT` override).
- `web/dashboard.html` + `worker/` — the review dashboard: a self-contained HTML page served by a
  Cloudflare Worker (KV-backed) so Jordan can approve staged picks from a phone. See repo README
  "Review dashboard" section and `worker/README.md` for routes/secrets. Approval only records the
  picks — it never applies or submits anything. The dashboard is the source of truth for the
  "get my stuff" artifacts: after Submit, the user self-serves the week's submissions CSV
  (`GET /api/approvals/csv`) and a copyable **opt-in** Claude CoWork apply prompt
  (`GET /api/approvals/cowork-prompt`), both built live from KV and both Access-gated. The CoWork
  prompt is optional and always pauses for human review before submitting — it never auto-submits.
  RADMACHINE (`fetch_approvals.py`) still logs the picks to a local spreadsheet and sends a backup
  email that links to the same CSV route (its own Gmail SMTP; the Worker has no email transport).

Convention: single-purpose modules, heavy top docstrings (SETUP/USAGE/exit codes), stdlib-first
with optional deps behind try/except, meaningful exit codes, and `--dry-run` on side-effecting
entrypoints.
