# wa-copilot-dashboard Worker

Cloudflare Worker that serves the private review dashboard (`web/dashboard.html`) and acts as
its backend. See the repo-root `README.md` for the full architecture and data flow; this file
covers just the Worker's routes and secrets.

## Routes

| Method | Path              | Auth                          | Purpose                                             |
|--------|-------------------|--------------------------------|------------------------------------------------------|
| GET    | `/`                | Cloudflare Access (edge)      | Serves the dashboard HTML.                            |
| GET    | `/api/week`        | Cloudflare Access (edge)      | Returns `{week, jobs[], metrics}` for the current week; each job may include a drafted `cover_letter`. |
| POST   | `/api/approve`     | Cloudflare Access (edge)      | Body `{week, approved: [jobId, ...], cover_letters: {jobId: text, ...}, dev?}`. Stores the approval set plus any dashboard-edited cover letters. No email is sent from here — the user self-serves the CSV + CoWork prompt from the dashboard (below). If `dev: true` (a dashboard opened from a `?dev=1` dev-test review email), the submit is acknowledged but **recorded nowhere in KV**, so a test run can't pollute the prod approvals a real Submit relies on. |
| GET    | `/api/approvals/current` | Cloudflare Access (edge) | No body/params. Returns `{week, jobs: [{id,title,org,url,resume}], submitted_at}` for the current week's approvals, so the dashboard can render "Ready to apply" on page load. |
| GET    | `/api/approvals/csv` | Cloudflare Access (edge)    | Optional `?week=YYYY-MM-DD` (defaults to the current week). Regenerates the submissions CSV **live** from KV (`approvals:<week>` joined against `week:<week>` jobs + `resumes` map) and returns it as a `text/csv` download. Same columns `scripts/fetch_approvals.py` writes locally, plus a `resume_file` column. The dashboard's "Download CSV" button and the RADMACHINE backup email both point here — one artifact, one source of truth. |
| GET    | `/api/approvals/cowork-prompt` | Cloudflare Access (edge) | Optional `?week=YYYY-MM-DD`. Returns `{week, prompt}` — the copyable Claude CoWork apply prompt, built from the week's stored `applicant`/`user`. Powers the dashboard's "Copy CoWork prompt" button. (JS port of `src/copilot/email_render.py:build_cowork_prompt`, kept in sync by matching tests on both sides.) |
| POST   | `/api/notes`       | Cloudflare Access (edge)      | Body `{notes, week?}`. Saves the "Steer next week's search" note the user typed on the dashboard (latest wins). `GET /api/week` returns it as `notes` so the textarea prefills. |
| PUT    | `/api/week`        | Bearer token (`PUBLISH_TOKEN`) | Body `{week, jobs[], metrics, user?, applicant?, resumes?}`. Called by `scripts/publish_week.py` from RADMACHINE. `user`/`applicant`/`resumes` feed the on-demand CSV + CoWork prompt routes; a payload without them (older publisher) just yields a blank `resume_file` column and a leaner prompt. |
| GET    | `/api/approvals`   | Bearer token (`PUBLISH_TOKEN`) | `?week=YYYY-MM-DD`. Called by `scripts/fetch_approvals.py` from RADMACHINE (now used to log approved picks to a local spreadsheet, not to feed an auto-fill step — see repo-root README). |
| GET    | `/api/notes`       | Bearer token (`PUBLISH_TOKEN`) | No params. Returns `{notes, updated_at, week}`. Called by `scripts/fetch_notes.py` from RADMACHINE, which writes it to the user's local `weekly_notes.txt` (overrides `search.weekly_notes`) before the next discover run. |

Every browser-facing route (`GET /`, `GET /api/week`, `POST /api/approve`, `GET
/api/approvals/current`, `GET /api/approvals/csv`, `GET /api/approvals/cowork-prompt`, `POST
/api/notes`) is reachable only because Cloudflare Access is configured on the route in front of
the Worker (see repo-root README for the policy description) — the Worker itself does not re-check
the Access identity. The CSV + CoWork-prompt routes are intentionally Access-only (not
bearer-gated): it's the user downloading her own artifacts in her own authenticated browser
session, the same posture as the rest of the dashboard, and it keeps the bearer token out of any
client-side link. The two RADMACHINE-facing endpoints (`PUT /api/week`, `GET /api/approvals`, and
the bearer-gated `GET /api/notes`) additionally require the bearer token since RADMACHINE talks to
the Worker directly, outside any Access browser session.

There is no auto-fill / auto-submit step anywhere in this Worker — `POST /api/approve` only
records the pick. The user then downloads the submissions CSV and copies the CoWork apply prompt
from the dashboard (both built live from KV), and applies to each job herself.

## Bindings

- **KV namespace** `WA_COPILOT_KV` — holds staged weeks (`week:<week>`), the pointer to the
  latest published week (`current_week`), stored approvals (`approvals:<week>`), and the latest
  "steer next week" note (`weekly_notes`). The `id` in `wrangler.toml` is a placeholder; create
  the real namespace at deploy time (see repo-root README) and paste its id in.

## Secrets (names only — set via `wrangler secret put`, never committed)

- `PUBLISH_TOKEN` — shared bearer token that gates `PUT /api/week` and `GET /api/approvals`.
  RADMACHINE reads the same value from its own environment (`WA_COPILOT_PUBLISH_TOKEN`) when
  calling `scripts/publish_week.py` / `scripts/fetch_approvals.py`.
- If the two RADMACHINE-facing routes are also put behind Cloudflare Access with a service
  token, RADMACHINE's environment additionally needs `WA_COPILOT_CF_ACCESS_CLIENT_ID` and
  `WA_COPILOT_CF_ACCESS_CLIENT_SECRET` — both scripts send them as `CF-Access-Client-Id` /
  `CF-Access-Client-Secret` headers alongside the bearer token when present, and skip them
  (no error) when absent.
That is the only secret this Worker needs. There is **no** email transport in the Worker anymore:
the previous Resend integration (`RESEND_API_KEY` / `NOTIFY_EMAIL_TO` / `NOTIFY_EMAIL_FROM`, and
the `TEST_MODE` / `TEST_NOTIFY_EMAIL_TO` test-routing that was briefly layered on it) has been
removed. The user gets her artifacts from the dashboard directly (Access-gated CSV + CoWork
prompt), and RADMACHINE sends the only email — a backup, over its own Gmail SMTP — that links
back to `GET /api/approvals/csv`. If you had set those Resend secrets or the `TEST_MODE` var on a
prior deploy, delete them (`wrangler secret delete RESEND_API_KEY`, etc., and remove the
`[vars] TEST_MODE` line from `wrangler.toml`); they are no longer read. Dev/prod send testing now
happens on the RADMACHINE side via `publish_week.py --dev-test` (see the repo-root README).

## Tests

`worker/` has no build step to test, but the pure artifact builders (`src/artifacts.js` — the CSV
+ CoWork prompt) have unit tests: `cd worker && node --test` (Node 18+, no dependencies). They
pin the CSV column shape (which must stay in step with `scripts/fetch_approvals.py`) and the
CoWork prompt's wording/guardrails.

## Local dev

`wrangler dev` picks up secrets from a gitignored `.dev.vars` file in this directory
(`PUBLISH_TOKEN=...`) — never commit that file.
