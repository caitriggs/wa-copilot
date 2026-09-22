// wa-copilot-dashboard Worker — serves the private review dashboard and acts as its backend.
//
// Cloudflare Access sits in front of this whole route (configured on the zone/app, not here),
// so every request that reaches this Worker from a browser is already Jordan's Google identity.
// The two RADMACHINE-facing endpoints (GET /api/approvals, PUT /api/week) are additionally
// gated by a bearer token (Worker secret PUBLISH_TOKEN) since RADMACHINE calls them directly,
// outside the browser/Access session.
//
// There is NO auto-fill / auto-submit step anywhere: approving a job here only records the pick.
// The user then self-serves two artifacts straight from the dashboard — the submissions CSV and a
// Claude CoWork apply prompt — both built ON DEMAND from KV by GET /api/approvals/csv and
// GET /api/approvals/cowork-prompt (see worker/src/artifacts.js). RADMACHINE still keeps its own
// local copy of the spreadsheet and sends a backup "ready to apply" email that links to the CSV
// route (scripts/fetch_approvals.py). Nothing in this repo fills out or submits an application.
//
// KV layout (binding: WA_COPILOT_KV):
//   week:<week>        -> { week, jobs: [...], metrics: {...}, applicant: {...},
//                            resumes: {label: filename}, user }   (the staged payload; each job may
//                          carry a drafted "cover_letter" string. applicant + resumes feed the
//                          on-demand CSV + CoWork prompt; absent for weeks published before they
//                          were added — the artifacts degrade to a blank résumé column / a prompt
//                          with whatever applicant fields exist. `user` is stored for provenance)
//   current_week       -> "<week>"                                (pointer to the latest published week)
//   approvals:<week>   -> { week, approved: [jobId, ...], cover_letters: {jobId: text, ...},
//                            submitted_at }   (cover_letters holds the dashboard-edited text, so
//                            it may differ from the originally staged draft)
//   weekly_notes       -> { notes, updated_at, week }   (the "steer next week's search" note the
//                          user saves on the dashboard; RADMACHINE pulls it via GET /api/notes and
//                          writes it to weekly_notes.txt before the next discover run)
//   weekly_notes_doc   -> { filename, content_type, data_b64, uploaded_at, week }   (a document the
//                          user uploads on the dashboard to steer the week; the Worker only stores
//                          it — the weekly run's fetch_notes.py extracts its text. Newest of the
//                          typed note vs. this upload wins.)

import dashboardHtml from "../../web/dashboard.html";
import { approvedJobDetails, buildSubmissionsCsv, buildCoworkPrompt } from "./artifacts.js";

function jsonResponse(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

function htmlResponse(html) {
  return new Response(html, {
    status: 200,
    headers: { "content-type": "text/html; charset=utf-8" },
  });
}

function isAuthorized(request, env) {
  const auth = request.headers.get("Authorization") || "";
  const expected = `Bearer ${env.PUBLISH_TOKEN || ""}`;
  // Fixed-length-independent tokens are fine here (not a crypto secret comparison at scale);
  // a plain equality check is sufficient for a single-operator bearer token.
  return !!env.PUBLISH_TOKEN && auth === expected;
}

// SURFACED and APPROVED are recomputed here from the authoritative KV records (this week's
// stored jobs list and its approvals record) rather than trusted from whatever publish_week.py
// last wrote into metrics — that field either mirrored a stale discover-run count (could be 0
// even when jobs[] is non-empty, e.g. when jobs came from the queue.json fallback) or, for
// "approved", was always seeded to 0 at publish time since approvals happen later via
// POST /api/approve, which never wrote back into week:<week>.metrics. Computing both live means
// a Worker redeploy alone fixes the display for already-published weeks, no republish needed.
// APPLIED (LOGGED) and RESPONSES still come from publish_week.py's own metrics (they track
// events — ESD log entries / employer responses — outside anything stored in this KV record).
async function readNotes(env) {
  const rec = await env.WA_COPILOT_KV.get("weekly_notes", "json");
  return (rec && typeof rec.notes === "string") ? rec.notes : "";
}

// The uploaded steering DOC (weekly_notes_doc), returned WITHOUT its base64 payload — just enough
// for the dashboard to show "steering from <file>". null when none uploaded.
async function readSteerDocMeta(env) {
  const d = await env.WA_COPILOT_KV.get("weekly_notes_doc", "json");
  if (!d || !d.data_b64) return null;
  return { filename: d.filename || "upload", uploaded_at: d.uploaded_at || null };
}

async function handleGetWeek(env) {
  // The steering note + any uploaded steer-doc meta are returned on every shape so the dashboard
  // can prefill the textarea and show which source is currently steering.
  const notes = await readNotes(env);
  const steer_doc = await readSteerDocMeta(env);
  const currentWeek = await env.WA_COPILOT_KV.get("current_week");
  if (!currentWeek) {
    return jsonResponse({ week: null, jobs: [], metrics: {}, notes, steer_doc });
  }
  const stored = await env.WA_COPILOT_KV.get(`week:${currentWeek}`, "json");
  if (!stored) {
    return jsonResponse({ week: currentWeek, jobs: [], metrics: {}, notes, steer_doc });
  }
  const jobs = Array.isArray(stored.jobs) ? stored.jobs : [];
  const approvals = await env.WA_COPILOT_KV.get(`approvals:${currentWeek}`, "json");
  const approvedCount = (approvals && Array.isArray(approvals.approved)) ? approvals.approved.length : 0;
  const metrics = { ...(stored.metrics || {}), surfaced: jobs.length, approved: approvedCount };
  return jsonResponse({ ...stored, jobs, metrics, notes, steer_doc });
}

// Normalize a raw notes submission: trim first so a whitespace-only box (spaces/newlines left
// after deleting the text) is stored as a true empty string, not as "some non-empty text" that
// happens to render blank — the stored record must reflect "cleared" exactly, not just look like
// it. Exported (named export, alongside the default Worker export below) so it's unit-testable.
export function normalizeNote(raw) {
  return typeof raw === "string" ? raw.trim().slice(0, 2000) : "";
}

// Browser-facing (Access-gated only, like POST /api/approve): the dashboard saves the user's
// "steer next week" note here. Stored under a single stable key (latest note wins). A blank
// (or whitespace-only) submission is stored as notes: "" with a fresh updated_at — NOT deleted —
// so GET /api/notes still reports updated_at as set and fetch_notes.py knows to overwrite its
// local weekly_notes.txt with empty (see fetch_notes.py's "never set" vs "cleared" distinction).
async function handlePostNotes(request, env) {
  let body;
  try {
    body = await request.json();
  } catch {
    return jsonResponse({ ok: false, error: "invalid JSON body" }, 400);
  }
  const notes = normalizeNote(body && body.notes);
  const week = (body && typeof body.week === "string") ? body.week : null;
  const record = { notes, updated_at: new Date().toISOString(), week };
  await env.WA_COPILOT_KV.put("weekly_notes", JSON.stringify(record));
  return jsonResponse({ ok: true, notes });
}

// Browser-facing (Access-gated): the dashboard uploads a document (résumé / a doc describing a
// pivot) to steer next week. The Worker can't parse PDF/DOCX, so it just STORES the raw file
// (base64) under weekly_notes_doc; the weekly run's fetch_notes.py — which has the Python
// parsers — extracts its text into weekly_notes.txt. "Latest source wins": whichever of the typed
// note vs. this doc is more recent steers the week (compared by timestamp in fetch_notes).
const MAX_STEER_DOC_B64 = 3_000_000; // ~2.2MB decoded — plenty for a résumé/brief, safe for KV.
async function handlePostNotesUpload(request, env) {
  let body;
  try {
    body = await request.json();
  } catch {
    return jsonResponse({ ok: false, error: "invalid JSON body" }, 400);
  }
  const data_b64 = (body && typeof body.data_b64 === "string") ? body.data_b64 : "";
  if (!data_b64) {
    return jsonResponse({ ok: false, error: "data_b64 (base64 file contents) is required" }, 400);
  }
  if (data_b64.length > MAX_STEER_DOC_B64) {
    return jsonResponse({ ok: false, error: "file too large (max ~2MB)" }, 413);
  }
  const filename = (body && typeof body.filename === "string" && body.filename) ? body.filename.slice(0, 200) : "upload";
  const content_type = (body && typeof body.content_type === "string") ? body.content_type.slice(0, 120) : "";
  const week = (body && typeof body.week === "string") ? body.week : null;
  const record = { filename, content_type, data_b64, uploaded_at: new Date().toISOString(), week };
  await env.WA_COPILOT_KV.put("weekly_notes_doc", JSON.stringify(record));
  return jsonResponse({ ok: true, filename });
}

// RADMACHINE/Actions-facing (bearer-gated): fetch_notes.py pulls the latest note AND any uploaded
// steer-doc (full base64 payload) so it can extract the doc's text where the parsers live.
async function handleGetNotes(request, env) {
  if (!isAuthorized(request, env)) {
    return jsonResponse({ ok: false, error: "unauthorized" }, 401);
  }
  const rec = await env.WA_COPILOT_KV.get("weekly_notes", "json");
  const doc = await env.WA_COPILOT_KV.get("weekly_notes_doc", "json");
  return jsonResponse({ ...(rec || { notes: "", updated_at: null, week: null }), doc: doc || null });
}

async function handlePutWeek(request, env) {
  if (!isAuthorized(request, env)) {
    return jsonResponse({ ok: false, error: "unauthorized" }, 401);
  }
  let body;
  try {
    body = await request.json();
  } catch {
    return jsonResponse({ ok: false, error: "invalid JSON body" }, 400);
  }
  const { week, jobs, metrics, user, applicant, resumes } = body || {};
  if (!week || typeof week !== "string") {
    return jsonResponse({ ok: false, error: "body.week (string) is required" }, 400);
  }
  // user/applicant/resumes are optional and feed the on-demand CSV + CoWork prompt routes. Store
  // them when present (default to empty) so those artifacts are self-serve from KV; a payload
  // without them (an older publisher) just yields a blank résumé column / a leaner prompt.
  const payload = {
    week,
    jobs: Array.isArray(jobs) ? jobs : [],
    metrics: metrics || {},
    user: typeof user === "string" ? user : "",
    applicant: (applicant && typeof applicant === "object" && !Array.isArray(applicant)) ? applicant : {},
    resumes: (resumes && typeof resumes === "object" && !Array.isArray(resumes)) ? resumes : {},
  };
  await env.WA_COPILOT_KV.put(`week:${week}`, JSON.stringify(payload));
  await env.WA_COPILOT_KV.put("current_week", week);
  return jsonResponse({ ok: true });
}

async function weekRecord(env, week) {
  return (await env.WA_COPILOT_KV.get(`week:${week}`, "json")) || {};
}

async function jobsForWeek(env, week) {
  const stored = await env.WA_COPILOT_KV.get(`week:${week}`, "json");
  return (stored && Array.isArray(stored.jobs)) ? stored.jobs : [];
}

async function handlePostApprove(request, env) {
  let body;
  try {
    body = await request.json();
  } catch {
    return jsonResponse({ ok: false, error: "invalid JSON body" }, 400);
  }
  const { week, approved, cover_letters, dev } = body || {};
  if (!week || typeof week !== "string") {
    return jsonResponse({ ok: false, error: "body.week (string) is required" }, 400);
  }
  if (!Array.isArray(approved)) {
    return jsonResponse({ ok: false, error: "body.approved (array) is required" }, 400);
  }
  // Dev/test submit (dashboard opened from a ?dev=1 review email): exercise the full approve flow
  // but record NOTHING in KV, so a dev run can't pollute the prod approvals a real Submit relies
  // on. There is only one Worker/KV, so this URL-driven flag is how a run is marked non-recording.
  if (dev) {
    return jsonResponse({ ok: true, dev: true, recorded: false });
  }
  const coverLetters = (cover_letters && typeof cover_letters === "object" && !Array.isArray(cover_letters))
    ? cover_letters
    : {};
  const record = { week, approved, cover_letters: coverLetters, submitted_at: new Date().toISOString() };
  await env.WA_COPILOT_KV.put(`approvals:${week}`, JSON.stringify(record));
  // No email is sent from here anymore: the user pulls a live CSV + CoWork prompt straight from
  // the dashboard (GET /api/approvals/csv + /cowork-prompt), and RADMACHINE sends a backup email
  // that links to the same CSV route. Approval storage is the only side effect of Submit.
  return jsonResponse({ ok: true, recorded: true });
}

async function handleGetApprovals(request, env, url) {
  if (!isAuthorized(request, env)) {
    return jsonResponse({ ok: false, error: "unauthorized" }, 401);
  }
  const week = url.searchParams.get("week");
  if (!week) {
    return jsonResponse({ ok: false, error: "?week= is required" }, 400);
  }
  const stored = await env.WA_COPILOT_KV.get(`approvals:${week}`, "json");
  return jsonResponse(stored || { week, approved: [], submitted_at: null });
}

// Browser-facing (Access-gated only, like GET /api/week) so the dashboard can render the
// "Submitted / To apply" section on load, not just right after a Submit click in this session.
async function handleGetApprovalsCurrent(env) {
  const week = await env.WA_COPILOT_KV.get("current_week");
  if (!week) {
    return jsonResponse({ week: null, jobs: [], submitted_at: null });
  }
  const record = await env.WA_COPILOT_KV.get(`approvals:${week}`, "json");
  if (!record || !Array.isArray(record.approved) || record.approved.length === 0) {
    return jsonResponse({ week, jobs: [], submitted_at: null });
  }
  const jobs = await jobsForWeek(env, week);
  const details = approvedJobDetails(jobs, record.approved);
  return jsonResponse({ week, jobs: details, submitted_at: record.submitted_at || null });
}

// Resolve the week these self-serve artifacts are for: an explicit ?week= (so a bookmarked link
// keeps working) else the current published week. Returns { week, record, approvals } or null
// when nothing is published yet. record is the week:<week> payload (jobs/applicant/resumes/user).
async function resolveApprovalContext(env, url) {
  const week = url.searchParams.get("week") || (await env.WA_COPILOT_KV.get("current_week"));
  if (!week) return null;
  const record = await weekRecord(env, week);
  const approvals = await env.WA_COPILOT_KV.get(`approvals:${week}`, "json");
  return { week, record, approvals };
}

// Browser-facing (Access-gated only, like GET /api/week): the dashboard's "Download CSV" button.
// Regenerates the submissions CSV live from KV every time — approve more jobs Thursday and
// Friday's download reflects it — so it's never a stale one-shot snapshot. The log date is the
// week's submitted_at date (falling back to today) so re-downloading the same week is stable.
async function handleGetApprovalsCsv(env, url) {
  const ctx = await resolveApprovalContext(env, url);
  if (!ctx) return jsonResponse({ ok: false, error: "no week published yet" }, 404);
  const { week, record, approvals } = ctx;
  const approvedIds = (approvals && Array.isArray(approvals.approved)) ? approvals.approved : [];
  const details = approvedJobDetails(record.jobs || [], approvedIds);
  const submittedDate = (approvals && approvals.submitted_at)
    ? String(approvals.submitted_at).slice(0, 10)
    : new Date().toISOString().slice(0, 10);
  const csv = buildSubmissionsCsv(week, details, record.resumes || {}, submittedDate);
  return new Response(csv, {
    status: 200,
    headers: {
      "content-type": "text/csv; charset=utf-8",
      // Fixed name (no week suffix) so it lands as ~/Downloads/submitted_jobs.csv — exactly what
      // the CoWork prompt tells the seeker to open. The week is a column inside the file.
      "content-disposition": `attachment; filename="submitted_jobs.csv"`,
    },
  });
}

// The résumé filenames actually used by this week's approved jobs (best-fit label -> filename via
// the stored resumes map), deduped — passed to the CoWork prompt as its résumé hint.
function approvedResumeFilenames(details, resumes) {
  const map = (resumes && typeof resumes === "object") ? resumes : {};
  const names = (details || []).map((d) => (d.resume && map[d.resume]) ? map[d.resume] : "").filter(Boolean);
  return [...new Set(names)];
}

// Browser-facing (Access-gated only): the dashboard's "Copy CoWork prompt" button. Built from
// the week's stored applicant/user (same wording as the RADMACHINE email's prompt). Returned as
// JSON so the dashboard can drop it straight onto the clipboard.
async function handleGetApprovalsCoworkPrompt(env, url) {
  const ctx = await resolveApprovalContext(env, url);
  if (!ctx) return jsonResponse({ ok: false, error: "no week published yet" }, 404);
  const { week, record, approvals } = ctx;
  const approvedIds = (approvals && Array.isArray(approvals.approved)) ? approvals.approved : [];
  const details = approvedJobDetails(record.jobs || [], approvedIds);
  const prompt = buildCoworkPrompt({
    week,
    applicant: record.applicant || {},
    resumeFilenames: approvedResumeFilenames(details, record.resumes || {}),
  });
  return jsonResponse({ week, prompt });
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const { pathname } = url;
    const method = request.method;

    if (method === "GET" && pathname === "/") {
      return htmlResponse(dashboardHtml);
    }
    if (method === "GET" && pathname === "/api/week") {
      return handleGetWeek(env);
    }
    if (method === "PUT" && pathname === "/api/week") {
      return handlePutWeek(request, env);
    }
    if (method === "POST" && pathname === "/api/approve") {
      return handlePostApprove(request, env);
    }
    if (method === "POST" && pathname === "/api/notes") {
      return handlePostNotes(request, env);
    }
    if (method === "POST" && pathname === "/api/notes/upload") {
      return handlePostNotesUpload(request, env);
    }
    if (method === "GET" && pathname === "/api/notes") {
      return handleGetNotes(request, env);
    }
    if (method === "GET" && pathname === "/api/approvals") {
      return handleGetApprovals(request, env, url);
    }
    if (method === "GET" && pathname === "/api/approvals/current") {
      return handleGetApprovalsCurrent(env);
    }
    if (method === "GET" && pathname === "/api/approvals/csv") {
      return handleGetApprovalsCsv(env, url);
    }
    if (method === "GET" && pathname === "/api/approvals/cowork-prompt") {
      return handleGetApprovalsCoworkPrompt(env, url);
    }

    return jsonResponse({ ok: false, error: "not found" }, 404);
  },
};
