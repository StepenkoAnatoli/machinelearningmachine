/*
 * Tests for scripts/audit_vendor_deps.mjs - the JavaScript half of the
 * "dependency audit" CI job:
 *
 *     npm install && node --test tests/js/*.test.mjs
 *
 * `npm audit` exits 1 both when it finds a real high+ CVE and when it simply
 * cannot reach the advisory endpoint. Those need opposite responses (bump the
 * pin vs. retry), so the wrapper classifies them separately. These tests pin
 * that classification down using report shapes captured from real npm output,
 * because the failure mode they guard against is a CI job that goes red for a
 * network blip - or worse, goes green having audited nothing.
 *
 * No network, no child processes: only the pure decision functions.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  SEVERITY_ORDER,
  blockingSeverities,
  classify,
  formatFindings,
  isRetryable,
  readLockedVersions,
  summarize,
} from "../../scripts/audit_vendor_deps.mjs";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");

/** Shape npm actually emits for a clean audit (captured, not invented). */
const CLEAN_REPORT = JSON.parse(`{
  "auditReportVersion": 2,
  "vulnerabilities": {},
  "metadata": {
    "vulnerabilities": {"info":0,"low":0,"moderate":0,"high":0,"critical":0,"total":0},
    "dependencies": {"prod":1,"dev":140,"optional":2,"peer":0,"peerOptional":0,"total":140}
  }
}`);

/** Shape npm emits when the advisory endpoint answers with an error. */
const ENDPOINT_ERROR = JSON.parse(`{
  "message": "request to https://registry.npmjs.org/-/npm/v1/security/advisories/bulk failed, reason: connect ECONNRESET",
  "error": {"summary": "", "detail": ""}
}`);

/** A report with one high (must fail) and one moderate (must not). */
const MIXED_REPORT = JSON.parse(`{
  "auditReportVersion": 2,
  "vulnerabilities": {
    "dompurify": {
      "name": "dompurify", "severity": "high", "range": "*",
      "via": [{"title": "DOMPurify mutation XSS", "url": "https://github.com/advisories/GHSA-fake-test-1234",
               "severity": "high", "range": "*"}],
      "fixAvailable": {"name": "dompurify", "version": "3.4.16", "isSemVerMajor": false}
    },
    "marked": {
      "name": "marked", "severity": "moderate", "range": "*",
      "via": [{"title": "marked ReDoS", "url": "https://github.com/advisories/GHSA-fake-moder-9999",
               "severity": "moderate", "range": "*"}],
      "fixAvailable": false
    }
  },
  "metadata": {
    "vulnerabilities": {"info":0,"low":0,"moderate":1,"high":1,"critical":0,"total":2},
    "dependencies": {"prod":1,"dev":140,"optional":2,"peer":0,"peerOptional":0,"total":140}
  }
}`);

test("a report is recognised as a report, whatever the exit code", () => {
  assert.equal(classify({ stdout: JSON.stringify(CLEAN_REPORT), status: 0 }).kind, "report");
  // npm exits 1 when it finds something; that is still a usable report.
  assert.equal(classify({ stdout: JSON.stringify(MIXED_REPORT), status: 1 }).kind, "report");
});

test("an unreachable advisory endpoint is an endpoint error, never a finding", () => {
  const out = classify({ stdout: JSON.stringify(ENDPOINT_ERROR), stderr: "npm error audit endpoint returned an error", status: 1 });
  assert.equal(out.kind, "endpoint-error");
  assert.match(out.message, /ECONNRESET/);
});

test("npm crashing outright is an endpoint error too, not a silent pass", () => {
  const out = classify({ stdout: "", stderr: "npm error code ENOENT", status: 1 });
  assert.equal(out.kind, "endpoint-error");
  assert.match(out.message, /ENOENT/);
  const empty = classify({ stdout: "{not json", stderr: "", status: 1 });
  assert.equal(empty.kind, "endpoint-error");
  // Unparseable stdout is quoted verbatim - more useful than a generic message.
  assert.match(empty.message, /\{not json/);
  // Nothing at all on either stream still has to be an error, not a pass.
  const nothing = classify({ stdout: "", stderr: "", status: 1 });
  assert.equal(nothing.kind, "endpoint-error");
  assert.match(nothing.message, /no usable output/);
});

test("a structured npm error is reported by its summary, not its hint line", () => {
  // Real shape for a missing lockfile - not retryable, and the summary is the
  // sentence a reader needs rather than the "try this instead" detail.
  const out = classify({
    stdout: JSON.stringify({
      error: {
        code: "ENOLOCK",
        summary: "This command requires an existing lockfile.",
        detail: "Try creating one first with: npm i --package-lock-only",
      },
    }),
    status: 1,
  });
  assert.equal(out.kind, "endpoint-error");
  assert.equal(out.message, "This command requires an existing lockfile.");
  assert.equal(isRetryable(out.message), false);
});

test("a clean audit over 140 packages has nothing to fix", () => {
  const s = summarize(CLEAN_REPORT, "high");
  assert.equal(s.packagesAudited, 140);
  assert.deepEqual(s.mustFix, []);
  assert.deepEqual(s.informational, []);
  assert.equal(s.counts.total, 0);
});

test("only high and critical block at --audit-level=high", () => {
  const s = summarize(MIXED_REPORT, "high");
  assert.deepEqual(s.mustFix.map((f) => f.name), ["dompurify"]);
  assert.deepEqual(s.informational.map((f) => f.name), ["marked"]);
  assert.equal(s.packagesAudited, 140);
});

test("dropping to --audit-level=moderate makes the moderate finding blocking", () => {
  const s = summarize(MIXED_REPORT, "moderate");
  assert.deepEqual(s.mustFix.map((f) => f.name).sort(), ["dompurify", "marked"]);
  assert.deepEqual(s.informational, []);
});

test("blockingSeverities is the tail of npm's severity ladder", () => {
  assert.deepEqual(blockingSeverities("high"), ["high", "critical"]);
  assert.deepEqual(blockingSeverities("critical"), ["critical"]);
  assert.deepEqual(blockingSeverities("low"), ["low", "moderate", "high", "critical"]);
  assert.throws(() => blockingSeverities("bogus"), /unknown audit level/);
  // Guard the ladder itself: the slice logic assumes this exact ordering.
  assert.deepEqual(SEVERITY_ORDER, ["info", "low", "moderate", "high", "critical"]);
});

test("findings carry the shipped version, the advisory id and a fix hint", () => {
  const locked = new Map([["dompurify", "3.4.15"], ["marked", "12.0.2"]]);
  const s = summarize(MIXED_REPORT, "high", locked);
  const out = formatFindings(s.mustFix);
  assert.match(out, /dompurify@3\.4\.15 \(high\)/);
  assert.match(out, /GHSA-fake-test-1234/);
  assert.match(out, /fix: dompurify@3\.4\.16/);
  assert.match(formatFindings(s.informational), /marked@12\.0\.2 \(moderate\)/);
  assert.match(formatFindings(s.informational), /no automated fix/);
  // npm reports a directly pinned package's affected range as "*", which is
  // noise; it must not leak into the message.
  assert.doesNotMatch(out, /\*/);
});

test("transient failures are retryable; npm's own complaints are not", () => {
  for (const msg of [
    "connect ECONNRESET",
    "npm error audit endpoint returned an error",
    "npm warn audit 500 Internal Server Error",
    "request to ... failed, reason: socket hang up",
    "ETIMEDOUT",
    "npm warn audit 429 Too Many Requests",
  ]) {
    assert.equal(isRetryable(msg), true, `expected retryable: ${msg}`);
  }
  // A missing lockfile or a bad flag fails identically on the third try.
  for (const msg of ["This command requires an existing lockfile", "npm error code EUSAGE"]) {
    assert.equal(isRetryable(msg), false, `retrying will not fix: ${msg}`);
  }
});

test("a vacuous audit is detectable - zero packages audited is not a pass", () => {
  const vacuous = JSON.parse(JSON.stringify(CLEAN_REPORT));
  vacuous.metadata.dependencies.total = 0;
  assert.equal(summarize(vacuous, "high").packagesAudited, 0);
});

test("readLockedVersions reads the pins the audit is run against", () => {
  const locked = readLockedVersions();
  const pkg = JSON.parse(readFileSync(join(repoRoot, "package.json"), "utf8"));
  assert.equal(locked.size > 100, true, "lockfile should describe the whole tree");
  // Every direct devDependency must be resolvable, and must match its pin.
  for (const [name, pin] of Object.entries(pkg.devDependencies)) {
    assert.equal(locked.get(name), pin, `${name} in the lockfile does not match package.json`);
  }
  assert.equal(locked.get("dompurify"), "3.4.15");
  // A missing lockfile degrades to an empty map instead of throwing.
  assert.equal(readLockedVersions(join(repoRoot, "does-not-exist.json")).size, 0);
});
