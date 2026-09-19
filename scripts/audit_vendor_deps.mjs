#!/usr/bin/env node
/*
 * Audit the vendored browser dependencies for known CVEs - the JavaScript half
 * of the "dependency audit" CI job.
 *
 *     node scripts/audit_vendor_deps.mjs
 *
 * Why this is not just `npm audit --audit-level=high`
 * ----------------------------------------------------
 * `npm audit` exits 1 for two completely different reasons, and the exit code
 * alone cannot tell them apart:
 *
 *   1. it found a vulnerability at or above --audit-level, or
 *   2. it could not reach the audit endpoint at all ("audit endpoint returned
 *      an error" - a 429/5xx/ECONNRESET from registry.npmjs.org).
 *
 * Case 2 is a transient network failure, not a finding about this project, but
 * on its own it turns CI red with nothing in the log but "Process completed
 * with exit code 1". This script separates the two: a real high/critical
 * finding fails (and prints what to fix), while an unreachable endpoint is
 * retried and then reported as an infrastructure failure with the npm error
 * quoted, not silently treated as either a pass or a vulnerability.
 *
 * It also refuses to report a vacuous pass: an audit that covered zero
 * packages is a broken audit, not a clean one.
 *
 * Node builtins only, so this runs in the audit job without `npm ci`.
 */
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

/**
 * name -> version for everything pinned in the lockfile.
 *
 * npm's report identifies a finding by its dependency *range*, which for a
 * directly pinned package is just "*". The version we actually ship is the
 * useful part of the message, so read it from the same lockfile npm audited.
 */
export function readLockedVersions(lockPath = join(repoRoot, "package-lock.json")) {
  const versions = new Map();
  let lock;
  try {
    lock = JSON.parse(readFileSync(lockPath, "utf8"));
  } catch {
    return versions;
  }
  for (const [path, info] of Object.entries(lock?.packages ?? {})) {
    if (!path || typeof info?.version !== "string") continue;
    versions.set(path.split("node_modules/").pop(), info.version);
  }
  return versions;
}

/** npm's own severity ladder, weakest first. */
export const SEVERITY_ORDER = ["info", "low", "moderate", "high", "critical"];

/** Severities that must fail the build for a given --audit-level. */
export function blockingSeverities(level = "high") {
  const idx = SEVERITY_ORDER.indexOf(level);
  if (idx === -1) throw new Error(`unknown audit level: ${level}`);
  return SEVERITY_ORDER.slice(idx);
}

/**
 * Decide what an `npm audit --json` invocation actually produced.
 *
 * A real report always carries `auditReportVersion`; an endpoint failure emits
 * a JSON object with a `message` and an `error` instead. Anything that is not
 * parseable JSON at all (npm crashing, an OOM, a truncated write) is also an
 * endpoint/runner failure rather than a finding.
 */
export function classify({ stdout = "", stderr = "", status = 0 } = {}) {
  let parsed = null;
  try {
    parsed = JSON.parse(stdout);
  } catch {
    parsed = null;
  }

  if (parsed && typeof parsed === "object" && "auditReportVersion" in parsed) {
    return { kind: "report", report: parsed };
  }

  const err = parsed && typeof parsed === "object" ? parsed.error : null;
  const detail =
    (parsed && parsed.message) ||
    (err && (err.summary || err.detail)) ||
    stderr.trim() ||
    stdout.trim() ||
    `npm audit produced no usable output (exit ${status})`;
  return { kind: "endpoint-error", message: String(detail).split("\n")[0] };
}

/** Split a report into what must fail CI and what is merely worth knowing. */
export function summarize(report, level = "high", locked = new Map()) {
  const blocking = new Set(blockingSeverities(level));
  const counts = { ...(report?.metadata?.vulnerabilities ?? {}) };
  const findings = [];

  for (const [name, vuln] of Object.entries(report?.vulnerabilities ?? {})) {
    const via = Array.isArray(vuln?.via) ? vuln.via : [];
    findings.push({
      name,
      installed: locked.get(name) ?? "",
      severity: vuln?.severity ?? "info",
      range: vuln?.range ?? "",
      blocking: blocking.has(vuln?.severity ?? "info"),
      fixAvailable: vuln?.fixAvailable ?? false,
      titles: via
        .map((v) => (typeof v === "string" ? v : v?.title))
        .filter((t) => typeof t === "string" && t.length > 0),
      ghsa: via
        .flatMap((v) => (v && typeof v === "object" ? [v.url] : []))
        .filter((u) => typeof u === "string" && u.includes("/advisories/"))
        .map((u) => u.split("/advisories/")[1]),
    });
  }

  findings.sort(
    (a, b) =>
      SEVERITY_ORDER.indexOf(b.severity) - SEVERITY_ORDER.indexOf(a.severity) ||
      a.name.localeCompare(b.name),
  );

  return {
    counts,
    findings,
    mustFix: findings.filter((f) => f.blocking),
    informational: findings.filter((f) => !f.blocking),
    packagesAudited: report?.metadata?.dependencies?.total ?? 0,
  };
}

/** Render the human-readable block that CI prints on failure. */
export function formatFindings(findings) {
  return findings
    .map((f) => {
      const fix =
        f.fixAvailable === true
          ? "fix available"
          : typeof f.fixAvailable === "object"
            ? `fix: ${f.fixAvailable.name}@${f.fixAvailable.version}${f.fixAvailable.isSemVerMajor ? " (major)" : ""}`
            : "no automated fix";
      const advisory = f.ghsa.length > 0 ? ` [${f.ghsa.join(", ")}]` : "";
      const title = f.titles.length > 0 ? `: ${f.titles[0]}` : "";
      const pinned = f.installed ? `${f.name}@${f.installed}` : f.name;
      // npm reports a directly pinned package's affected range as "*", which
      // tells the reader nothing; only show it when it is actually a range.
      const affected = f.range && f.range !== "*" ? ` (affected: ${f.range})` : "";
      return `  - ${pinned} (${f.severity})${advisory}${title}${affected}\n      ${fix}`;
    })
    .join("\n");
}

/** Transient failures worth retrying; a genuine finding is never retried. */
export function isRetryable(message = "") {
  return /ECONNRESET|ECONNREFUSED|ETIMEDOUT|EAI_AGAIN|EPIPE|ENOTFOUND|socket hang up|ENOAUDIT|E429|\b(429|500|502|503|504)\b|endpoint returned an error|network|timeout/i.test(
    message,
  );
}

/**
 * Run the audit with retries.
 *
 * `--package-lock-only` keeps this honest about what it covers: the lockfile is
 * the pinned set that build_vendor.py copies into static/vendor/, and this job
 * deliberately does not `npm ci`, so there is no node_modules tree to audit.
 */
export async function audit({ cwd = repoRoot, level = "high", attempts = 3, sleep = (ms) => new Promise((r) => setTimeout(r, ms)), log = console.error } = {}) {
  const args = ["audit", "--json", "--package-lock-only", `--audit-level=${level}`];
  let last = null;

  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const res = spawnSync("npm", args, { cwd, encoding: "utf8", maxBuffer: 32 * 1024 * 1024 });
    last = { stdout: res.stdout ?? "", stderr: res.stderr ?? "", status: res.status ?? 1 };
    const outcome = classify(last);

    if (outcome.kind === "report") return { outcome, attempt };

    // A missing lockfile or an auth failure will fail identically on the third
    // try; only network-shaped errors are worth waiting out.
    if (!isRetryable(outcome.message)) {
      log(`npm audit failed in a way that retrying will not fix: ${outcome.message}`);
      return { outcome, attempt };
    }

    log(`npm audit could not reach the advisory endpoint (attempt ${attempt}/${attempts}): ${outcome.message}`);
    if (attempt < attempts) await sleep(1000 * attempt);
  }

  return { outcome: last ? classify(last) : classify({}), attempt: attempts };
}

async function main() {
  const level = process.env.AUDIT_LEVEL ?? "high";
  const attempts = Number(process.env.AUDIT_ATTEMPTS ?? 3);

  const { outcome, attempt } = await audit({ level, attempts });

  if (outcome.kind === "endpoint-error") {
    const msg = isRetryable(outcome.message)
      ? `npm audit could not reach the advisory endpoint after ${attempt} attempt(s) - ` +
        `this is a registry/network failure, not a vulnerability finding: ${outcome.message}`
      : `npm audit could not run, and retrying will not fix it: ${outcome.message}`;
    if (process.env.GITHUB_ACTIONS === "true") console.log(`::error::${msg}`);
    console.error(msg);
    process.exitCode = 2;
    return;
  }

  const s = summarize(outcome.report, level, readLockedVersions());

  // A green audit over nothing is the most dangerous kind of green.
  if (s.packagesAudited === 0) {
    const msg = "npm audit reported 0 packages - the lockfile was not actually audited";
    if (process.env.GITHUB_ACTIONS === "true") console.log(`::error::${msg}`);
    console.error(msg);
    process.exitCode = 3;
    return;
  }

  console.log(
    `ok - npm audit: ${s.packagesAudited} packages checked against the advisory database ` +
      `(critical=${s.counts.critical ?? 0} high=${s.counts.high ?? 0} moderate=${s.counts.moderate ?? 0} low=${s.counts.low ?? 0})`,
  );

  if (s.informational.length > 0) {
    // Printed, not failed on: the vendored assets are served from our own
    // origin under a strict CSP, and --audit-level=high is the agreed bar.
    console.log(`\ninformational (below --audit-level=${level}, not failing):`);
    console.log(formatFindings(s.informational));
  }

  if (s.mustFix.length > 0) {
    const n = s.mustFix.length;
    const found = attempt > 1 ? ` (reported on attempt ${attempt})` : "";
    const msg =
      `${n} vendored browser ${n === 1 ? "dependency has" : "dependencies have"} ` +
      `a known ${level}+ vulnerability${found}. ` +
      `Bump the pin in package.json, run \`npm install && npm run vendor\`, ` +
      `and commit the regenerated vendor/ + MANIFEST.json:\n${formatFindings(s.mustFix)}`;
    if (process.env.GITHUB_ACTIONS === "true") {
      for (const f of s.mustFix) console.log(`::error::${f.installed ? `${f.name}@${f.installed}` : f.name} ${f.severity}${f.ghsa.length ? ` ${f.ghsa.join(",")}` : ""}`);
    }
    console.error(`\n${msg}`);
    process.exitCode = 1;
    return;
  }

  console.log(`no ${level}+ vulnerabilities in the pinned browser dependencies`);
}

const isMain = process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  await main();
}
