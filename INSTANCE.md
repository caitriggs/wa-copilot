# Running a private instance (the `wa-copilot-<user>` model)

This repo is a **template**. It holds only shared code and never any personal data. To actually run
the copilot for one person, create a **private instance** from it and let GitHub Actions drive the
weekly pipeline — no always-on personal machine required.

## How it fits together

```
caitriggs/wa-copilot   (PUBLIC template — code only, no PII)
        │  "Use this template"
        ▼
caitriggs/wa-copilot-<user>   (PRIVATE instance)
   ├─ <user>/config.yaml          ← committed (this user's preferences)
   ├─ <user>/profile/*.pdf        ← committed (this user's résumés)
   ├─ Actions Secrets             ← credentials, NEVER committed
   └─ .github/workflows/          ← inherited from the template
        ├─ weekly.yml             → Fri 5pm PT: pull → publish → email
        └─ sync-from-template.yml → keeps this instance current with the template
        ▼
   Cloudflare Worker + KV   (review dashboard, approvals, on-demand CSV/prompt)
```

**What lives where**
- **Inputs** (config + résumés): committed in the private instance. That's the one place per-user
  data is allowed — the shared template stays clean.
- **Credentials**: only in the instance's GitHub Actions **Secrets**. Never in git.
- **Approval / submissions state**: in the Worker's KV, not the repo. The weekly run is stateless;
  the runner's working tree is discarded after each run.

## One-time instance setup

1. **Create it:** on the template, *Use this template → new private repo* `wa-copilot-<user>`.
2. **Add the user's inputs** under `<user>/`:
   - `<user>/config.yaml` — copy `config/user.example.yaml` and fill it in (`user:` must equal
     `<user>`). Holds no secrets.
   - `<user>/profile/*.pdf` — their résumé(s); wire `profile.resumes` (label → path → titles).
3. **Set the repo variable** `COPILOT_USER = <user>` (Settings → Secrets and variables → Actions →
   Variables). The weekly workflow reads it (defaults to `max` if unset).
4. **Set Actions Secrets** (Settings → Secrets and variables → Actions → Secrets):

   | Secret | What |
   |---|---|
   | `WA_COPILOT_GMAIL_APP_PASSWORD` | Gmail App Password for the sending account |
   | `WA_COPILOT_EMAIL_TO` | recipient (the job seeker) |
   | `WA_COPILOT_EMAIL_FROM` | sender (e.g. an alias of the operator's Gmail) |
   | `WA_COPILOT_EMAIL_ENABLED` | `1` |
   | `WA_COPILOT_EMAIL_DEV_TO` | optional: where `--dev-test` sends instead of the real user |
   | `WA_COPILOT_USAJOBS` / `WA_COPILOT_USAJOBS_EMAIL` | USAJOBS API key + registered email |
   | `WA_COPILOT_ADZUNA_ID` / `WA_COPILOT_ADZUNA_KEY` | Adzuna app id + key |
   | `WA_COPILOT_JOOBLE` | Jooble API key |
   | `WA_COPILOT_WORKER_URL` | the dashboard Worker base URL |
   | `WA_COPILOT_PUBLISH_TOKEN` | bearer token gating the Worker's publish route |
   | `WA_COPILOT_CF_ACCESS_CLIENT_ID` / `WA_COPILOT_CF_ACCESS_CLIENT_SECRET` | Cloudflare Access **service token** (lets the runner reach the Access-gated Worker) |
   | `WA_COPILOT_ANTHROPIC` | optional: LLM-tailored ranking/letters (fails open if absent) |

5. **Validate before going live:** run the **Weekly** workflow via *Run workflow* with
   **dev_test = true** — it exercises the whole chain but emails `WA_COPILOT_EMAIL_DEV_TO` (you)
   with a `?dev=1` link, so nothing hits the real user. Once it looks right, let the Friday cron
   run it for real.

## Staying in sync with the template

`sync-from-template.yml` runs every Thursday (and on demand). It fetches the public template and
merges it into this instance's `main` — pushing straight through on a clean merge, or opening a PR
if it hits a conflict (rare, since the template only touches shared code while the instance only
adds `<user>/` files). You get template fixes and features automatically without re-cloning.
