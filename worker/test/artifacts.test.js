// Tests for worker/src/artifacts.js — the on-demand submissions CSV + Claude CoWork prompt the
// dashboard serves from KV. Run with `node --test` (no dependencies). These pin the CSV column
// shape (which must stay in step with scripts/fetch_approvals.py) and the CoWork prompt's
// wording/guardrails (a faithful port of email_render.py:build_cowork_prompt — the Python side
// asserts the same phrases in tests/test_email_render.py).

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  CSV_FIELDS,
  approvedJobDetails,
  buildSubmissionsCsv,
  buildCoworkPrompt,
} from "../src/artifacts.js";

const JOBS = [
  { id: "a1", title: "SDET, Platform", org: "Acme", url: "https://x/1", resume: "Test Engineer" },
  { id: "b2", title: "QA Manager", org: "Globex", url: "https://x/2", resume: "QA Lead" },
  { id: "c3", title: "Games QA", org: "Initech", url: "https://x/3", resume: "" },
];
const RESUMES = { "Test Engineer": "test_engineer.pdf", "QA Lead": "qa_lead.pdf" };

test("CSV_FIELDS matches the fetch_approvals columns + resume_file", () => {
  assert.deepEqual(CSV_FIELDS, [
    "date", "job_title", "company", "application_link", "status", "week", "job_id", "resume_file",
  ]);
});

test("approvedJobDetails joins ids in order and drops unknown ids", () => {
  const details = approvedJobDetails(JOBS, ["b2", "nope", "a1"]);
  assert.deepEqual(details.map((d) => d.id), ["b2", "a1"]);
  assert.equal(details[0].title, "QA Manager");
  assert.equal(details[1].resume, "Test Engineer");
});

test("buildSubmissionsCsv writes header + a row per job with resume_file resolved", () => {
  const details = approvedJobDetails(JOBS, ["a1", "b2", "c3"]);
  const csv = buildSubmissionsCsv("2026-08-22", details, RESUMES, "2026-08-20");
  const lines = csv.trimEnd().split("\n");
  assert.equal(lines[0], CSV_FIELDS.join(","));
  assert.equal(lines.length, 4); // header + 3 jobs
  assert.equal(
    lines[1],
    "2026-08-20,SDET, Platform,Acme,https://x/1,to apply,2026-08-22,a1,test_engineer.pdf".replace(
      "SDET, Platform", '"SDET, Platform"'),
  );
  // a job whose best résumé has no filename in the map -> blank resume_file, not a crash
  assert.ok(lines[3].endsWith(",2026-08-22,c3,"));
});

test("buildSubmissionsCsv quotes fields with commas/quotes/newlines (RFC-4180 minimal)", () => {
  const details = [
    { id: "x", title: 'Eng, "Sr"', org: "A\nB", url: "u", resume: "" },
  ];
  const csv = buildSubmissionsCsv("w", details, {}, "2026-08-20");
  // The embedded newline means the record itself spans two physical lines, so assert against the
  // whole CSV rather than a single split line.
  assert.ok(csv.includes('"Eng, ""Sr"""')); // comma + doubled quotes
  assert.ok(csv.includes('"A\nB"')); // embedded newline quoted
});

test("buildSubmissionsCsv degrades when resumes map is absent", () => {
  const details = approvedJobDetails(JOBS, ["a1"]);
  const csv = buildSubmissionsCsv("2026-08-22", details, undefined, "2026-08-20");
  assert.ok(csv.trimEnd().split("\n")[1].endsWith(",a1,")); // blank resume_file, no throw
});

test("buildCoworkPrompt fills applicant details, CSV name, and Desktop path", () => {
  const p = buildCoworkPrompt({
    week: "2026-08-22",
    applicant: {
      full_name: "Alex Rivera", email: "alex@x.com", phone: "555-1212",
      location: "Seattle, WA", links: { linkedin: "li/alex", github: "gh/alex" },
    },
  });
  assert.ok(p.includes("Alex Rivera") && p.includes("alex@x.com") && p.includes("555-1212"));
  assert.ok(p.includes("li/alex") && p.includes("gh/alex"));
  // Job-seeker's own machine: the CSV in their Downloads, résumés on their own Desktop — never a
  // RADMACHINE absolute path.
  assert.ok(p.includes("~/Downloads/submitted_jobs.csv"));
  assert.ok(p.includes("~/Desktop/wa-unemployment-copilot/"));
  assert.ok(!p.includes("<Desktop>/wa-unemployment-copilot/max/"));
  assert.ok(!p.includes("Portfolio:")); // omitted fields don't render an empty line
});

test("buildCoworkPrompt lists résumé filenames as an optional hint", () => {
  const p = buildCoworkPrompt({
    week: "2026-08-22", applicant: {},
    resumeFilenames: ["alex_test_engineer.pdf", "alex_qa_lead.pdf", "alex_test_engineer.pdf"],
  });
  assert.ok(p.includes("alex_test_engineer.pdf") && p.includes("alex_qa_lead.pdf"));
  assert.ok(p.includes("use it as a hint")); // offered as a hint, not a hard requirement
  // deduped — the repeated filename appears once
  assert.equal(p.split("alex_test_engineer.pdf").length - 1, 1);
});

test("buildCoworkPrompt keeps the human-in-the-loop guardrails (parity with Python)", () => {
  const p = buildCoworkPrompt({ week: "w", applicant: {} });
  assert.ok(p.includes("STOP before submitting"));
  assert.ok(p.toLowerCase().includes("never submit without my ok"));
  assert.ok(p.includes("week ending w"));
});
