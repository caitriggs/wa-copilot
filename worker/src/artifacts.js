// Pure builders for the two self-serve "get my stuff" artifacts the dashboard offers after a
// Submit: the weekly submissions CSV and the Claude CoWork apply prompt. Kept in their own
// module (no dependency on the dashboard-HTML import in index.js) so they're unit-testable with
// `node --test`, and so the CSV/prompt shape stays a single, reviewable source of truth.
//
// buildCoworkPrompt is a faithful port of src/copilot/email_render.py:build_cowork_prompt — same
// wording, same human-in-the-loop guardrails ("STOP before submitting", "never submit without my
// ok"). There is no shared runtime between the Worker (JS) and RADMACHINE (Python), so the two
// are kept in sync by hand and pinned by matching tests on both sides. buildSubmissionsCsv writes
// the same columns scripts/fetch_approvals.py appends locally, plus a resume_file column naming
// the PDF each job best matched.

// CSV field header — the local RADMACHINE spreadsheet (fetch_approvals.py CSV_FIELDS) plus the
// résumé file each job best matched. Keep the shared columns in the same order/spelling.
export const CSV_FIELDS = [
  "date", "job_title", "company", "application_link", "status", "week", "job_id", "resume_file",
];

export const DEFAULT_STATUS = "to apply";

// RFC-4180 minimal quoting, matching Python's csv.QUOTE_MINIMAL: wrap a field only when it
// contains a comma, double-quote, or newline, and double any embedded quotes.
function csvCell(value) {
  const s = value == null ? "" : String(value);
  if (/[",\r\n]/.test(s)) {
    return '"' + s.replace(/"/g, '""') + '"';
  }
  return s;
}

// Join approved job ids against that week's staged job list, in the order they were approved,
// dropping ids with no matching staged job (same join the dashboard/email use). Each detail
// carries the résumé label the posting best matched so the CSV can name its PDF file.
export function approvedJobDetails(jobs, approvedIds) {
  const byId = new Map((jobs || []).map((j) => [j.id, j]));
  return (approvedIds || [])
    .map((id) => byId.get(id))
    .filter(Boolean)
    .map((j) => ({
      id: j.id,
      title: j.title || "",
      org: j.org || "",
      url: j.url || "",
      resume: j.resume || "",
    }));
}

// The submissions CSV, regenerated live from KV. `details` is approvedJobDetails() output;
// `resumes` maps a résumé label -> its PDF filename (from the publish payload; may be absent for
// weeks published before that field existed, in which case resume_file is blank). `dateStr` is
// the log date (the week's submitted_at date, or today) — passed in so the builder stays pure.
export function buildSubmissionsCsv(week, details, resumes, dateStr, status = DEFAULT_STATUS) {
  const resumeMap = (resumes && typeof resumes === "object") ? resumes : {};
  const lines = [CSV_FIELDS.join(",")];
  for (const j of details || []) {
    const row = {
      date: dateStr || "",
      job_title: j.title || "",
      company: j.org || "",
      application_link: j.url || "",
      status,
      week,
      job_id: j.id || "",
      resume_file: (j.resume && resumeMap[j.resume]) ? resumeMap[j.resume] : "",
    };
    lines.push(CSV_FIELDS.map((f) => csvCell(row[f])).join(","));
  }
  // Trailing newline so the file ends cleanly, matching csv.writer's per-row line terminator.
  return lines.join("\n") + "\n";
}

// A copyable Claude CoWork prompt to fill each application for review. Faithful JS port of
// src/copilot/email_render.py:build_cowork_prompt — this runs on the JOB SEEKER'S OWN desktop
// (their own CoWork login, not RADMACHINE), so it references only paths they can act on: the CSV
// in their own ~/Downloads and a résumé folder on their own ~/Desktop, never a RADMACHINE path.
// Résumé filenames are a hint, not a requirement. STOPS before submitting so the human submits.
export function buildCoworkPrompt({ week, applicant, csvFilename = "submitted_jobs.csv",
                                    resumeFilenames = [] }) {
  const a = applicant || {};
  const links = (a.links && typeof a.links === "object") ? a.links : {};
  const line = (label, val) => (val ? `     - ${label}: ${val}` : "");
  const details = [
    line("Name", a.full_name),
    line("Email", a.email),
    line("Phone", a.phone),
    line("Location", a.location),
    line("Work authorization", a.work_authorization),
    line("Willing to relocate", a.willing_to_relocate),
    line("Notice period", a.notice_period),
    line("LinkedIn", links.linkedin),
    line("GitHub", links.github),
    line("Portfolio", links.portfolio),
  ].filter(Boolean).join("\n");

  const resumes = [...new Set((resumeFilenames || []).filter(Boolean))]; // dedupe, keep order
  const resumeDir = "~/Desktop/wa-unemployment-copilot/";
  const resumeHint = resumes.length
    ? ` (the CSV's resume_file column names which résumé we matched to that job — e.g. ` +
      `${resumes.join(", ")} — use it as a hint if it helps, but match by title/content; the ` +
      `filenames in that folder don't need to match exactly)`
    : "";

  return (
    `You are running in Claude CoWork on MY OWN desktop with browser access (my own CoWork ` +
    `login, not a shared machine). Help me apply to the jobs I selected for the week ending ` +
    `${week}.\n\n` +
    `The job list is at ~/Downloads/${csvFilename} on this computer.\n\n` +
    `My résumé(s) are saved in ${resumeDir} — there may be more than one, each geared toward ` +
    `a different type of role.\n\n` +
    `Open ${csvFilename} and, for EACH row whose status is "to apply":\n` +
    `  1. Open its application_link in the browser.\n` +
    `  2. Fill the application form using my details:\n${details}\n` +
    `     - Résumé: from ${resumeDir}, pick whichever résumé best fits this job's title and ` +
    `content${resumeHint}.\n` +
    `     Tailor answers to the role using the posting's own text for screening questions.\n` +
    `  3. STOP before submitting and let me review the filled form — I click submit myself.\n` +
    `  4. After I confirm I submitted, set that row's status to "applied" (with today's date) ` +
    `in the CSV.\n\n` +
    `Only use facts from my résumé — never invent experience, and never submit without my ok.`
  );
}
