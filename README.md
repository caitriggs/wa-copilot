# wa-unemployment-copilot

Modular, multi-user tooling that prepares your **weekly Washington State unemployment
job-search requirements** — finding real jobs, drafting applications for you to review,
logging the activities you actually completed in ESD format, and assembling a claim-ready
packet with a pre-filled answer checklist.

It ranks the week's postings and puts the top matches in front of you on a **review dashboard**
(phone-friendly). You approve the ones you want, and it emails you each one's title + application
link so you apply yourself — nothing here auto-fills or auto-submits an application. The one hard
line it **never** crosses is the weekly claim itself: it never submits or certifies that, because
it's an attestation under penalty of perjury.

> Compliance: WA ESD requires **3 genuine job-search activities** per claim week (no
> carryover) and a detailed log kept ~30+ days past your benefit year; it can audit the log
> and cross-checks earnings against employer wage reports. This tool keeps your log truthful
> (only activities you confirm are written) and leaves every attestation to you.
> Refs: [ESD job-search requirements](https://esd.wa.gov/get-financial-help/unemployment-benefits/weekly-unemployment-claims/job-search-requirements),
> [weekly claims](https://esd.wa.gov/get-financial-help/unemployment-benefits/weekly-unemployment-claims).

## Quickstart: test it end-to-end with your own data

Fastest path to real results (USAJOBS needs only a free key — no waiting on alert emails):

```
# 0. Prereqs: Python 3.10+ and git on your machine.
git clone https://github.com/YOUR-ORG/wa-unemployment-copilot.git
cd wa-unemployment-copilot
python -m pip install -r requirements.txt

# 1. Onboard yourself (creates your Desktop data folder + config.yaml)
python scripts\setup_user.py --user demo

# 2. Add your experience + preferences
#    - drop your resume at:  <Desktop>\wa-unemployment-copilot\demo\profile\resume.pdf
#    - edit config.yaml: search.titles, locations, comp_min, weekly_notes
#      (open it at the path setup_user.py printed)

# 3a. Turn on USAJOBS (federal jobs; free key at https://developer.usajobs.gov/apirequest)
python scripts\setup_user.py --user demo --set-secret usajobs        # paste the key
python scripts\setup_user.py --user demo --set-secret usajobs_email  # the email you registered

# 3b. RECOMMENDED for private-sector jobs: turn on Adzuna (free key at https://developer.adzuna.com/)
python scripts\setup_user.py --user demo --set-secret adzuna_id      # your Adzuna app id
python scripts\setup_user.py --user demo --set-secret adzuna_key     # your Adzuna app key
#     then set sources.adzuna.enabled: true in config.yaml

# 4. Check your setup (read-only; --live pings the sources)
python scripts\doctor.py --user demo --live

# 5. Preview, then run for real
python scripts\run_weekly.py --user demo --dry-run
python scripts\run_weekly.py --user demo --steps import,discover,draft
#   -> review drafts in: <Desktop>\wa-unemployment-copilot\demo\drafts\<week>\

# 6. Apply to what you like, then log what you ACTUALLY did
python scripts\log_activity.py --user demo          # repeat until you have 3+

# 7. Build your claim packet + (optional) email it to yourself
python scripts\run_weekly.py --user demo --steps packet,notify
#   -> <Desktop>\wa-unemployment-copilot\demo\packets\<week>\claim_packet.md

# 8. Log into eServices yourself and file the weekly claim using that packet.
```

Add **Indeed + LinkedIn** whenever you want by following "Set up the email job feed" below; then
re-run `doctor --live` to confirm IMAP works, and `run_weekly` will include them.

`doctor` and `--dry-run` never write to your log or send email — safe to run any time.

## Job sources (what's covered)

| Source | Status | How |
|---|---|---|
| **USAJOBS** | ✅ Live API | Official public API (key + registered email). Federal jobs; free key. |
| **Adzuna** | ✅ Live API | Licensed aggregator — broad **private-sector** coverage (incl. Indeed/LinkedIn-sourced roles). Free app id + key, no email-alert wait. |
| **Indeed** | ✅ via email alerts | No usable public API/RSS anymore — the tool ingests your Indeed **job-alert emails** from Gmail (IMAP). |
| **LinkedIn** | ✅ via email alerts | Same — no public jobs API; the tool reads your LinkedIn **job-alert emails**. |
| **WorkSourceWA** | ✅ via email alerts / RSS | Email alerts (or a saved-search RSS URL if you have one). Activity there also counts toward ESD. |
| **Any RSS** | ✅ Live | Paste any saved-search RSS/Atom URL into `sources.feeds.rss_urls`. |
| Own-account fetch | ⏳ Opt-in (Phase 3) | Authenticated Playwright fetch of your *own* saved searches. ToS-gray; off by default. |

To cover Indeed + LinkedIn directly, follow **"Set up the email job feed"** below. For broad
private-sector coverage right away (no email-alert wait), enable **Adzuna**: get a free app id +
key at <https://developer.adzuna.com/>, store them (`--set-secret adzuna_id` / `adzuna_key`), and
set `sources.adzuna.enabled: true`.

## Set up the email job feed (Indeed + LinkedIn + WorkSourceWA)

This is how the tool covers Indeed and LinkedIn (neither has a usable public API). You create
job alerts on each site; the tool reads those alert emails from your Gmail over IMAP. One-time
setup, ~10 minutes.

### Step 1 — Turn on email job alerts on each site

- **Indeed:** sign in → run the job search you want → on the results page click **"Get new jobs
  for this search"** (create a job alert), set frequency to **Daily**. Alerts arrive from
  `alert@indeed.com` / `invite@indeed.com`.
- **LinkedIn:** go to **Jobs** → run your search → toggle **"Set alert"** on (or **Jobs → Job
  alerts** to manage), and make sure the alert's delivery includes **Email**. Alerts arrive from
  `jobalerts-noreply@linkedin.com`.
- **WorkSourceWA:** sign in at worksourcewa.com → save your search → enable **email
  notifications**. Alerts arrive from a `@worksourcewa.com` address.

The tool matches these known sender addresses (see `PROVIDER_SENDERS` in
`src/copilot/sources/email_alerts.py`). If your alerts come from a different address, add it there.

### Step 2 — Turn on IMAP + make a Gmail App Password

1. Gmail → **Settings (gear) → See all settings → Forwarding and POP/IMAP → Enable IMAP → Save**.
2. Google Account → **Security → 2-Step Verification** (must be ON) → **App passwords** →
   generate one for "Mail". Copy the 16-character password (no spaces).

### Step 3 — Store your Gmail login for the tool

The IMAP login reuses the same secrets as email sending:

```
python scripts/setup_user.py --user demo --set-secret smtp_user   # your full gmail address
python scripts/setup_user.py --user demo --set-secret smtp_pass   # the 16-char App Password
```

(If your IMAP account differs from your sending account, set `imap_user` / `imap_pass` instead —
those take precedence.) Secrets go to the OS keyring (or a gitignored `secrets.env`), never the config.

### Step 4 — Enable and tune the feed in your config

In `<Desktop>/wa-unemployment-copilot/demo/config.yaml`:

```yaml
sources:
  email_alerts:
    enabled: true                       # <- turn it on
    imap_host: "imap.gmail.com"
    mailbox: "INBOX"                    # or a Gmail label if you route alerts there (see note)
    since_days: 7                       # how far back to read alert emails
    providers: ["indeed", "linkedin", "worksourcewa"]
```

Optional — keep your inbox tidy: make a Gmail filter that labels job alerts (e.g. `JobAlerts`)
and, if you also **archive** them (skip the inbox), set `mailbox: "JobAlerts"` so the tool looks
in that label instead of `INBOX`. (Gmail labels are IMAP folders.)

### Step 5 — Test it

```
python scripts/run_weekly.py --user demo --steps discover --dry-run
```

Look for a line like `[email_alerts] 12 postings from alert emails (indeed, linkedin, worksourcewa)`.

**Troubleshooting**

| You see | Fix |
|---|---|
| `no IMAP credentials; skipping` | Run Step 3 (`--set-secret smtp_pass`). |
| `IMAP connect/login failed` | App Password wrong, IMAP not enabled (Step 2), or 2-Step Verification off. |
| `0 postings from alert emails` | Alerts haven't arrived yet (wait for the next daily send), they're older than `since_days`, or they're under a label — set `mailbox` to that label. |

## How it works

```
 discover -> rank -> draft -> publish (Thu evening) -> review + approve (dashboard) -> you apply
   |          |        |            |                        |                            |
 real       by prefs  cover      review-ready            approve = email + local          real
 postings   + résumé  letters    email w/ link           spreadsheet log of picks          apps
 (USAJOBS,  fit                                                                             |
  Adzuna,                                                                                    v
  alerts)                                                             log (you confirm) -> packet -> notify
```

You apply yourself using the emailed links, log what you actually did with
`scripts/log_activity.py`, and do one thing entirely by hand: **file + certify the weekly claim**
in eServices, using the packet. Everything runs **on your own machine**; all data stays under
**your Desktop**.

## Steer the week with `weekly_notes` (an override, not a nudge)

`search.weekly_notes` is the one field you edit each week, and it **overrides** the résumé/title
ranking rather than just tweaking it. Leave it blank and the tool ranks purely on your
`search.titles` + résumé fit + comp band. Fill it in and that note becomes the week's search:

- It **replaces** the titles the sources search for, folds its terms into your keywords, and —
  when the focus implies you'd flex on pay — **drops the salary floor** so plausible-stretch roles
  aren't filtered out.
- Ranking stops being title-driven: a heavily-weighted `focus_match` leads, title similarity is
  relaxed (a role you've never held by title isn't punished), and comp goes neutral. Enough résumé
  overlap to *plausibly* apply still helps — it just no longer decides.
- The **cover letter does the bridging**: for a pivot/stretch it names your transferable
  experience and frames the focus as genuine motivation (never claiming experience you don't have).

Examples:

```yaml
search:
  weekly_notes: "explore WA State jobs with outdoor or remote flexibility"
# or
  weekly_notes: "pivot toward marketing analytics at robotics companies like iRobot"
```

If an Anthropic key is configured, Claude expands the note into a concrete plan (queries, keywords,
sectors, target employers); otherwise a keyword heuristic is used. Turn the behavior off with
`search.focus.enabled: false`, and tune its influence with `ranking.focus_match`.

## Apply to the top-ranked jobs (you apply — nothing auto-fills or auto-submits)

There is no automated apply/Chrome-autofill step. The way you apply is: review + approve on the
**dashboard** (see below), then apply yourself using the emailed links. The packet still gets
**funnel metrics** (screened → ranked → applied, the top picks' ranking reasons, and which
companies/job types you've applied to so far) once you log what you actually did with
`scripts/log_activity.py`.

`src/copilot/apply.py` (`apply.mode` in config, default `stage`) can still stage a
`applications/<week>/queue.md` brief of the top `apply.top_n` postings for your own reading —
useful if you want a plain-text list outside the dashboard — but its `live` executor-apply mode
(a Cowork task auto-filling and submitting forms) is deprecated in favor of the dashboard flow
below and is no longer the recommended path.

Everything runs **on your own machine**; all data stays under **your Desktop**.

## Review dashboard (web + Cloudflare Worker) — how you actually apply

A small private dashboard lets you review each week's staged picks and approve which ones you
want to apply to, from your phone or any browser. Approving doesn't apply or submit anything —
it emails you the title + application link for each pick so you apply yourself, and logs the
same picks to a local spreadsheet on RADMACHINE for your own weekly-submission tracking.

- **`web/dashboard.html`** — self-contained, no build step, no external deps. Renders a metrics
  strip (surfaced/approved/applied-logged/responses), a **"Submitted / To apply"** section listing
  each approved job's title + application link, and job cards (title, org, location, comp, match
  score, why-matched, a link to the posting, and an approve checkbox), with a sticky "Submit
  approved" bar.
- **`worker/`** — a Cloudflare Worker (see `worker/README.md`) that serves the dashboard and backs
  it with KV storage:
  - `GET /api/week` — the current week's `{week, jobs[], metrics}` (browser call; Cloudflare
    Access guards the route).
  - `POST /api/approve` — stores `{week, approved: [ids]}` for that week, then (best-effort)
    emails you the approved jobs' titles + application links.
  - `GET /api/approvals/current` — the current week's approved jobs (title + link), for the
    dashboard's "Submitted / To apply" section on page load.
  - `PUT /api/week` (bearer-token secret) — RADMACHINE publishes the week's payload here via
    `scripts/publish_week.py`, which also sends a **"your jobs are ready to review"** email
    (Thursday evening — see "Schedule it" below) linking back to this dashboard.
  - `GET /api/approvals` (bearer-token secret) — RADMACHINE reads back approvals here via
    `scripts/fetch_approvals.py`, which logs each approved job (date, title, company, application
    link, status) to `<user>/log/submitted_jobs.csv` for your own records.
- **`scripts/publish_week.py`** — reads a user's staged `queue.json` + postings cache + log,
  builds the week payload, `PUT`s it to the Worker, and emails the review-ready link.
  `--dry-run` to preview without a network call or email.
- **`scripts/fetch_approvals.py`** — fetches the approved job ids for a week, joins them back
  against the local `queue.json` for full job details, and appends new rows to
  `<user>/log/submitted_jobs.csv`. Nothing in this repo fills out or submits an application —
  you apply using the links from the email or the dashboard, then log what you actually did with
  `scripts/log_activity.py` (only that human-confirmed record ever reaches your ESD log).

Deploying the Worker is a one-time, per-operator setup (Cloudflare account required):

```
cd worker
wrangler kv namespace create WA_COPILOT_KV     # paste the returned id into wrangler.toml
wrangler secret put PUBLISH_TOKEN              # same value goes in RADMACHINE's env as
                                                # WA_COPILOT_PUBLISH_TOKEN
wrangler deploy
```

Then put Cloudflare Access in front of the route (Google-identity login for the browser-facing
endpoints) — see `worker/README.md` for the route/auth breakdown. `PUBLISH_TOKEN` is the Worker's
only secret; there is no Resend/email transport in the Worker. After a week is approved, you grab
your submissions CSV and a copyable Claude CoWork apply prompt straight from the dashboard (both
Access-gated, rebuilt live from KV), and RADMACHINE sends a backup email — over its own Gmail SMTP
— that links to the same CSV.

**Dev vs prod sends.** The weekly review email normally goes to the config'd user (`email.to`).
To preview the whole experience without touching the real user, run `publish_week.py --dev-test`
(recipient from `--dev-to`, config `email.dev_to`, or env `WA_COPILOT_EMAIL_DEV_TO`): it publishes
the week as usual but the review email goes to the dev recipient and its dashboard link carries
`?dev=1`. There's only one Worker/KV, so that flag is how a run is marked non-recording — the
dashboard shows a red **DEV** banner and a Submit from it is acknowledged but written nowhere in
KV, leaving prod approvals untouched. The CSV/CoWork-prompt buttons still appear and work in dev
mode, so the whole self-serve flow is testable end to end — since nothing was recorded, they're
built entirely in the browser from the picks you just selected (see `web/dashboard.html`'s
`buildSubmissionsCsvClient`/`buildCoworkPromptClient`) rather than from the Worker's normal
KV-backed `GET /api/approvals/csv` / `GET /api/approvals/cowork-prompt` routes.

## Install

```
git clone https://github.com/YOUR-ORG/wa-unemployment-copilot.git
cd wa-unemployment-copilot
python -m pip install -r requirements.txt
```

### Windows notes (Git Bash / conda)

You do **not** need conda — any **Python 3.10+** works, including miniconda's own Python.

- **In Git Bash, use forward slashes in paths:** `python scripts/setup_user.py --user demo`.
  Git Bash treats `\` as an escape char, so `scripts\setup_user.py` becomes `scriptssetup_user.py`
  ("No such file or directory"). The `scripts\...` form works in Anaconda Prompt / PowerShell / cmd.
- If `python` opens the Microsoft Store or says *"Python was not found"*, Windows' **App
  Execution Alias** is intercepting it. Turn it off (**Settings → Apps → Advanced app settings →
  App execution aliases →** disable `python.exe` / `python3.exe`), or call Python by full path.
- **conda not found in Git Bash?** It isn't on PATH until initialized. Quickest — call it by full
  path (use your username):
  ```bash
  /c/Users/Jordan/miniconda3/python.exe -m pip install -r requirements.txt
  /c/Users/Jordan/miniconda3/python.exe scripts/setup_user.py --user demo
  ```
  To make `conda`/`python` work normally, run this once, then **reopen** Git Bash:
  ```bash
  /c/Users/Jordan/miniconda3/Scripts/conda.exe init bash
  ```
  Or just use **"Anaconda Prompt (miniconda3)"** from the Start menu, where conda + `python`
  already work.

## Onboard a user (you, then anyone else — e.g. Alex)

```
python scripts/setup_user.py --user demo
```

This creates `<Desktop>/wa-unemployment-copilot/demo/`, copies the config template there as
`config.yaml`, and prompts you (via the OS keyring) for any secrets you want to store now
(USAJOBS key, SMTP app-password, site logins). **No secret is ever typed into a file.**

Then:

1. **Edit your config** — open `<Desktop>/wa-unemployment-copilot/demo/config.yaml` and set
   your titles, locations, comp minimum, `weekly_notes`, LinkedIn URL, and which sources are on.
2. **Add your experience** — drop a résumé at `.../demo/profile/resume.pdf` (default), or a
   LinkedIn data export zip and set `profile.linkedin_import: export`.
   (LinkedIn: *Settings → Data privacy → Get a copy of your data*.)
3. **USAJOBS key** (if enabled) — register at <https://developer.usajobs.gov/apirequest>, then
   store it: `python scripts/setup_user.py --user demo --set-secret usajobs`. The API needs
   your **registered email** too (stored as `usajobs_email`).
4. **Job alerts (covers Indeed + LinkedIn)** — follow the full **"Set up the email job feed"**
   section above. In short: create email job alerts on each site, store your Gmail App Password,
   and set `sources.email_alerts.enabled: true`.
5. **Email reminders** (optional) — a Gmail **app password** (needs 2-Step Verification) stored
   as `smtp_user` / `smtp_pass`; set `email.to` in config.

## Run it

```
# Full weekly pipeline (safe to preview first with --dry-run):
python scripts/run_weekly.py --user demo --dry-run
python scripts/run_weekly.py --user demo

# Just some steps:
python scripts/run_weekly.py --user demo --steps discover,draft
python scripts/run_weekly.py --user demo --steps packet,notify

# Log a real activity you completed (interactive; this is the human-in-the-loop step):
python scripts/log_activity.py --user demo
```

`run_weekly.py` steps: `import`, `discover`, `draft`, `packet`, `notify`, `nudge` (default:
`discover,draft,packet,notify`). `import` (re)builds `profile/history.json` from your résumé or
LinkedIn export; discovery and drafts auto-import it on first use, so your real experience drives
both ranking and the tailored cover-letter drafts. Exit code is non-zero on misconfiguration so a
scheduled task can detect problems.

## Schedule it (Windows Task Scheduler)

`setup_user.py` prints the exact commands. They look like:

```
schtasks /Create /TN "UnemploymentCopilot_cait_Early" ^
  /TR "python C:\path\to\wa-unemployment-copilot\scripts\run_weekly.py --user demo --steps discover,draft,packet" ^
  /SC WEEKLY /D MON /ST 07:30 /RU demo /RP * /F

schtasks /Create /TN "UnemploymentCopilot_cait_Nudge" ^
  /TR "python C:\path\to\wa-unemployment-copilot\scripts\run_weekly.py --user demo --steps nudge" ^
  /SC WEEKLY /D THU /ST 16:00 /RU demo /RP * /F
```

## Your data folder

```
<Desktop>/wa-unemployment-copilot/<user>/
  config.yaml              # your preferences (no passwords)
  secrets.env              # optional gitignored fallback secret store (keyring preferred)
  profile/                 # resume.pdf / linkedin-export.zip + parsed history.json
  postings_cache/          # discovered postings per week (dedup + audit trail)
  drafts/<week>/           # drafted applications awaiting your review — nothing is submitted
  log/job_search_log.csv   # your ESD job-search log (keep indefinitely)
  packets/<week>/          # claim_packet.md — activities + pre-filled answer checklist
```

## Automate the full week (hands-off discovery + review, hands-on apply)

Three scheduled tasks (all via Windows Task Scheduler — `setup_user.py --print-schedule` prints
the exact `schtasks` commands) cover the week without any browser automation:

1. **Monday morning** — `run_weekly.py --steps discover,draft,packet` finds jobs and drafts
   cover letters.
2. **Thursday evening** — `publish_week.py` stages the week's top matches to the dashboard Worker
   and emails you a **"your jobs are ready to review"** link.
3. **Thursday afternoon** — a **nudge** email if you're short of the ESD minimum for the week.

Then, from the dashboard (any browser, e.g. your phone): review the top matches, check the ones
you want, and hit **Submit approved**. That emails you each pick's title + application link and
logs them to a local spreadsheet on RADMACHINE (`scripts/fetch_approvals.py`) — apply to each one
yourself. Log what you actually did with `scripts/log_activity.py`; `packet`/`notify` roll
genuinely-completed activities into the funnel metrics and the claim packet email. You still do
one thing entirely by hand each week: **file + certify the claim** in eServices using the packet.

## Better cover letters (LLM-written, optional)

By default drafts use a lightweight template. For genuinely tailored letters, turn on LLM
drafting: the tool sends your résumé/history + the specific posting to a small Claude model and
gets back a concise letter arguing why you fit *and* why you want the role. It's guardrailed to
use **only facts from your résumé** (no invented employers, titles, or metrics), and it falls
back to the template if the key or package is missing.

```
python -m pip install anthropic
python scripts/setup_user.py --user demo --set-secret anthropic   # your Anthropic API key
# in config.yaml: set draft.llm.enabled: true
```

- Model defaults to **Sonnet 5** (`claude-sonnet-5`) for the best writing; set
  `draft.llm.model: "claude-haiku-4-5"` for a cheaper/faster option.
- **The writing instructions are an editable template** — `cover_letter_prompt.md` in your data
  folder (created by `setup_user.py`; falls back to `config/cover_letter_prompt.md` in the repo).
  Tune the voice, the hook, the do's/don'ts there without touching code. It's filled with the
  posting, your résumé, and your `weekly_notes` before each call, so letters show the synergy
  between your background and what you're focused on now.
- Runs wherever the pipeline runs (host-native/Task Scheduler), using your stored key — so
  scheduled runs write letters unattended.
- Always review before sending: the draft is headed *"AI-drafted — verify every claim."*

## Optional: assisted claim helper (Phase 3, opt-in)

A guided helper for filling the eServices weekly claim. It is deliberately conservative and
**off by default**:

- **You** log in (SecureAccess Washington + MFA) and do all navigation in a **visible** browser.
  The tool never handles or stores your SAW credentials.
- It shows your logged activities and the weekly-claim questions in an **on-page reference panel**
  so you can fill the form quickly, clearly marking what's pre-filled vs. what only you confirm.
- It **never clicks submit or certify**, and **never auto-answers an attestation** (earnings,
  able & available, refused work, other payments). Those are certified under penalty of perjury —
  only you answer them.
- Optional best-effort auto-fill (`--autofill`) touches only *activity facts you already logged*
  (employer/title/date/contact) in clearly-labeled fields, pausing for your confirmation on each.

Enable and use it:

```
python -m pip install playwright
playwright install chromium
# in config.yaml: set claim_assist.enabled: true
python scripts\claim_assist.py --user demo
```

You'll be asked to type `I UNDERSTAND`, then a browser opens; log in, open your weekly claim,
press Enter to load the panel, fill it in, and **you** submit + certify.

## The weekly claim itself

The tool prepares a **packet** (`packets/<week>/claim_packet.md`) that lists your logged
activities in ESD format and a checklist mirroring the eServices weekly-claim questions. It
clearly separates what it **pre-filled from your log** from what **only you can confirm**
(earnings, able & available, etc.). You log into eServices via SecureAccess Washington and
submit + certify yourself. The tool never touches your SAW login.

## Secrets

- **Preferred: OS keyring** — `setup_user.py` stores secrets in Windows Credential Manager
  under service `wa-unemployment-copilot:<user>`. Nothing sensitive on disk.
- **Fallback: `secrets.env`** in your Desktop data folder — gitignored, `KEY=value` lines
  (e.g. `USAJOBS=...`, `SMTP_PASS=...`). The config references these by `secret_ref` only.

## Tests

```
python -m pytest -q
```

No network, no real Desktop writes (tests set `WA_UI_DATA_ROOT` to a temp dir).
