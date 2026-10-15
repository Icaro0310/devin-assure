"use strict";

const vscode = require("vscode");
const cp = require("child_process");
// Shared Restricted-Mode-safe executable resolver — byte-identical copy of
// devin-powerups' adapters/shared/safe-executable.cjs (lockstep enforced by
// config-restricted-check). On Windows, CreateProcess searches cwd before
// PATH: spawn only the absolute path this returns.
const { resolveSafeExecutable } = require("./safe-executable.cjs");
let statusBar, output;
// Monotonic counter — a slow audit finishing late must not overwrite a
// newer verdict already rendered on the status bar (anti-race guard).
// lastRun holds the newest completed audit — the badge re-runs that scope.
let auditSeq = 0, lastRun = null;

// Same range as the "devin-qa-pack-cli" pin in package.json (see README) —
// test/cli-contract.test.js asserts the pin, these bounds and the real CLI
// version agree. The floor is patch-level: --within (audit scoping) first
// shipped in CLI 0.1.2 — 0.1.0 accepts the argv but argparse exits 2 on it.
const COMPATIBLE_CLI = { min: [0, 1, 2], maxExclusive: [0, 2, 0] };
const CLI_RANGE = ">=0.1.2,<0.2";

function parseVersion(version) {
  const m = /^(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]*)?$/.exec(
    String(version || "").trim(),
  );
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
}

const cmp3 = (a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2];
// "compatible" | "too-old" | "too-new" | "unknown"
// Semver precedence: a prerelease sits BELOW its base release —
// 0.1.2-rc1 does not meet the >=0.1.2 floor (no --within), while
// 0.2.0-rc1 is still inside <0.2.
function cliCompatStatus(version) {
  const v = parseVersion(version);
  if (!v) return "unknown";
  const pre = /-/.test(String(version).trim().split("+")[0]);
  if (cmp3(v, COMPATIBLE_CLI.min) < 0 || (pre && cmp3(v, COMPATIBLE_CLI.min) === 0)) {
    return "too-old";
  }
  if (cmp3(v, COMPATIBLE_CLI.maxExclusive) > 0 ||
      (!pre && cmp3(v, COMPATIBLE_CLI.maxExclusive) === 0)) {
    return "too-new";
  }
  return "compatible";
}
function config() {
  const c = vscode.workspace.getConfiguration("devinQaPack");
  return { executable: c.get("executable", "devin-qa-pack"),
    extraArgs: c.get("extraArgs", []), limit: c.get("limit", 20) };
}
// extraArgs must never smuggle network or non-local evidence modes into
// the audit — this surface is strictly offline/read-only. Value 1 means
// the flag takes a value, which is skipped too (unless --flag=v).
const FORBIDDEN = { "--online": 0, "--allow-domain": 1, "--transcript": 1, "--source": 1 };
function sanitizeArgs(extra) {
  const keep = [];
  for (let i = 0; i < extra.length; i++) {
    const flag = extra[i].split("=")[0];
    if (flag in FORBIDDEN) {
      if (FORBIDDEN[flag] && !extra[i].includes("=")) i++; // skip its value
    } else keep.push(extra[i]);
  }
  return keep;
}
// All open workspace folders (multi-root) — used only to bound
// resolveSafeExecutable's inside-workspace rejection, not as spawn cwd.
function workspaceDirs() {
  const folders = vscode.workspace.workspaceFolders;
  return folders ? folders.map((f) => f.uri.fsPath) : [];
}
// Injectable for tests. Exit codes are results, not errors: 0 = all PASS,
// 1 = flagged (PARTIAL/UNVERIFIED), 2 = could not run.
const deps = {
  // opts.includeExtraArgs:false skips devinQaPack.extraArgs — used by the
  // --version probe so a bad setting cannot break the probe itself.
  runCli: (args, opts = {}) => {
    const { executable, extraArgs } = config();
    const spec = resolveSafeExecutable(executable, {
      workspaceDirs: workspaceDirs(),
      trusted: vscode.workspace.isTrusted,
      npmPackage: null,
    });
    return new Promise((resolve, reject) => {
      if (!spec) {
        return reject(new Error("devin-qa-pack not found on PATH. Install it " +
          "(uv tool install devin-qa-pack) or set devinQaPack.executable."));
      }
      cp.execFile(spec.file, spec.argv.concat(args,
        ...(opts.includeExtraArgs === false ? [] : sanitizeArgs(extraArgs))),
        { timeout: 120000, maxBuffer: 8 * 1024 * 1024 },
        (err, stdout, stderr) => {
          if (err && err.killed) return reject(new Error("devin-qa-pack timed out after 120s"));
          if (err && err.code === "ENOENT") {
            return reject(new Error("devin-qa-pack not found on PATH. Install it " +
              "(uv tool install devin-qa-pack) or set devinQaPack.executable."));
          }
          resolve({ code: err ? err.code : 0, stdout, stderr });
        });
    });
  },
};
const VERDICTS = ["PASS", "PARTIAL", "UNVERIFIED"];
// Contract: `devin-qa-pack audit --json` → {sessions_db, sessions:
// [{session_id, title, working_directory, verdict, summary, claims}]} —
// see README. Drift surfaces loudly, never renders blank.
function validateAuditPayload(payload) {
  const missing = [];
  if (!payload || typeof payload !== "object") return { ok: false, missing: ["<root>"] };
  if (typeof payload.sessions_db !== "string") missing.push("sessions_db");
  if (!Array.isArray(payload.sessions)) {
    missing.push("sessions");
  } else {
    payload.sessions.forEach((s, i) => {
      if (!s || typeof s.session_id !== "string") missing.push(`sessions[${i}].session_id`);
      if (!s || !VERDICTS.includes(s.verdict)) missing.push(`sessions[${i}].verdict`);
      if (!s || typeof s.summary !== "object" || s.summary === null) missing.push(`sessions[${i}].summary`);
      if (s && !Array.isArray(s.claims)) missing.push(`sessions[${i}].claims`);
    });
  }
  return { ok: missing.length === 0, missing };
}
// Once-per-session `devin-qa-pack --version` probe (best-effort). An
// argparse rejection is ambiguous — a too-old CLI missing a built-in
// flag, or a typo in devinQaPack.extraArgs. The probe plus the
// flag/setting comparison in runJson decides which.
// Cached as a promise so concurrent audits share one probe, and keyed by
// devinQaPack.executable — changing the configured binary re-probes
// instead of reusing a stale verdict from the previous CLI.
let versionProbe;
let probedExecutable;
function probeVersion() {
  const exe = config().executable;
  if (versionProbe && probedExecutable === exe) return versionProbe;
  probedExecutable = exe;
  versionProbe = (async () => {
    try {
      const { stdout } = await deps.runCli(["--version"], { includeExtraArgs: false });
      const m = /(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]*)?)/.exec(stdout || "");
      return m ? m[1] : null;
    } catch {
      return null; /* probe is best-effort — falls back to the flag check */
    }
  })();
  return versionProbe;
}

async function runJson(args) {
  const { code, stdout, stderr } = await deps.runCli(args);
  let payload;
  try {
    payload = JSON.parse(stdout);
  } catch {
    output.appendLine((stdout || "") + (stderr || ""));
    // argparse rejects flags it does not know (e.g. --within in CLI
    // 0.1.0) — but that is ambiguous: a too-old CLI missing a built-in
    // flag, or a bad entry in devinQaPack.extraArgs. Probe --version
    // once per session and compare rejected flags against the setting
    // before blaming the CLI.
    if (/unrecognized arguments|invalid choice/.test(stderr || "")) {
      const rejected = (stderr.match(/unrecognized arguments:\s*(.+)/) || [])[1]
        ?.trim().split(/\s+/).filter((t) => t.startsWith("-")) || [];
      const userFlags = new Set(config().extraArgs.filter((t) => t.startsWith("-")));
      // A rejected flag is the CLI's fault only if THIS call sent it —
      // --within on a session audit, or any flag the extension never
      // passes, must have come from extraArgs even on an old CLI.
      const sentFlags = new Set(args.filter((t) => t.startsWith("-")));
      const onlySettings = rejected.length > 0 &&
        rejected.every((t) => userFlags.has(t) && !sentFlags.has(t));
      const probed = await probeVersion();
      const compat = probed ? cliCompatStatus(probed) : "unknown";
      if (onlySettings || compat === "compatible") {
        throw new Error(
          `devinQaPack.extraArgs contains unsupported option(s): ${rejected.join(", ")} — ` +
          `fix the setting (the installed CLI${probed ? ` ${probed}` : ""} is inside ${CLI_RANGE})`);
      }
      const found = probed ? `, found ${probed}` : "";
      const hint = probed
        ? "upgrade: uv tool upgrade devin-qa-pack"
        : "upgrade: uv tool upgrade devin-qa-pack — or check devinQaPack.extraArgs";
      throw new Error(`devin-qa-pack CLI is too old for this extension ` +
        `(requires ${CLI_RANGE}${found}); the installed CLI rejected an argument — ${hint}`);
    }
    throw new Error(`devin-qa-pack audit returned no JSON (exit ${code}) — see output channel`);
  }
  // CLIs ≥0.1.2 self-report "version" in the payload; older ones simply omit
  // it, so an absent field is not proof of drift — shape validation is.
  const compat = cliCompatStatus(payload && payload.version);
  if (compat === "too-old" || compat === "too-new") {
    throw new Error(`devin-qa-pack CLI ${payload.version} is outside the range ` +
      `this extension supports (${CLI_RANGE}) — ` +
      (compat === "too-old"
        ? "upgrade: uv tool upgrade devin-qa-pack"
        : "downgrade the CLI or update the extension"));
  }
  return payload;
}

function sessionMd(a) {
  const s = a.summary || {};
  return [
    `- **verdict**: ${a.verdict}`,
    a.title ? `- **title**: ${a.title}` : null,
    `- **cwd**: ${a.working_directory}`,
    `- **claims**: ${a.claims.length} — ${s.verified || 0} verified · ${s.disputed || 0} disputed · ${s.unverifiable || 0} unverifiable`,
    "",
    ...a.claims.map((c) => `- [${c.status}] ${c.kind}/${c.detail || "-"}: ${c.evidence}`),
  ].filter((l) => l !== null).join("\n");
}
function renderVerdicts(sessions) {
  const counts = { PASS: 0, PARTIAL: 0, UNVERIFIED: 0 };
  sessions.forEach((a) => { counts[a.verdict] = (counts[a.verdict] || 0) + 1; });
  const worst = counts.PARTIAL ? "PARTIAL" : counts.UNVERIFIED ? "UNVERIFIED" : "PASS";
  statusBar.text = worst === "PASS" ? `$(pass) qa: PASS (${sessions.length})`
    : `$(warning) qa: ${worst}`;
  statusBar.backgroundColor = worst === "PASS" ? undefined
    : new vscode.ThemeColor("statusBarItem.warningBackground");
  statusBar.tooltip = `devin-qa-pack — ${sessions.length} session(s): ` +
    `${counts.PASS} PASS · ${counts.PARTIAL} PARTIAL · ${counts.UNVERIFIED} UNVERIFIED`;
}

const pickItem = (a) => ({
  label: `${a.verdict}  ${a.session_id.slice(0, 8)}` + (a.title ? `  ${a.title}` : ""),
  description: `${a.claims.length} claim(s)`, audit: a,
});
// Multi-root: --within takes ONE path, so every workspace folder gets its
// own audit; payloads merge, deduped by session_id (nested folders could
// audit the same session twice).
function runWithin(folders) {
  return Promise.all(folders.map((f) =>
    runJson(["audit", "--all", "--within", f.uri.fsPath,
      "--limit", String(config().limit), "--json"]))).then((payloads) => {
    // Each folder payload gets the same contract check as the merged one —
    // p.sessions is dereferenced below, so drift has to fail loudly here
    // naming the missing field and folder, not as a TypeError in flatMap.
    payloads.forEach((p, i) => {
      const shape = validateAuditPayload(p);
      if (!shape.ok) throw new Error(`audit JSON missing ${shape.missing.join(", ")} ` +
        `(folder ${folders[i].uri.fsPath}) — extension/CLI drift ` +
        `(compatible: devin-qa-pack ${CLI_RANGE})`);
    });
    const seen = new Set();
    return { sessions_db: folders.map((f) => f.uri.fsPath).join(", "),
      sessions: payloads.flatMap((p) => p.sessions)
        .filter((s) => !seen.has(s.session_id) && seen.add(s.session_id)) };
  });
}

async function audit(run) {
  const seq = ++auditSeq;
  statusBar.text = "$(sync~spin) qa: auditing"; statusBar.show();
  statusBar.backgroundColor = undefined; // clear any stale warning color
  try {
    const payload = await run();
    const shape = validateAuditPayload(payload);
    if (!shape.ok) throw new Error(`audit JSON missing ${shape.missing.join(", ")} — ` +
      `extension/CLI drift (compatible: devin-qa-pack ${CLI_RANGE})`);
    if (seq === auditSeq) {
      lastRun = run;
      renderVerdicts(payload.sessions);
      const pick = await vscode.window.showQuickPick(
        payload.sessions.map(pickItem),
        { placeHolder: "audit verdicts — pick a session for claim detail" },
      );
      // Re-check after the await: a newer audit supersedes this pick.
      if (pick && seq === auditSeq) {
        const doc = await vscode.workspace.openTextDocument({
          language: "markdown",
          content: `# qa audit — ${pick.audit.session_id}\n\n${sessionMd(pick.audit)}`,
        });
        await vscode.window.showTextDocument(doc, { preview: true });
      }
    }
    return payload;
  } catch (e) {
    if (seq === auditSeq) {
      statusBar.text = "$(error) qa: unavailable";
      statusBar.backgroundColor = undefined;
      statusBar.tooltip = e.message;
      vscode.window.showErrorMessage(e.message);
    }
    return null;
  }
}

const auditAll = () =>
  audit(() => runJson(["audit", "--all", "--limit", String(config().limit), "--json"]));

function auditWorkspace() {
  const folders = vscode.workspace.workspaceFolders || [];
  if (!folders.length) {
    vscode.window.showErrorMessage("devin-qa-pack: no workspace folder open");
    return Promise.resolve(null);
  }
  return audit(() => runWithin(vscode.workspace.workspaceFolders || []));
}

async function auditSession() {
  const id = await vscode.window.showInputBox({
    prompt: "Session to audit (exact id or unique prefix)" });
  if (id === undefined || id.trim() === "") return null;
  return audit(() => runJson(["audit", "--session", id.trim(), "--json"]));
}

function activate(context) {
  output = vscode.window.createOutputChannel("devin-qa-pack");
  statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
  statusBar.command = "devin-qa-pack.reaudit";
  statusBar.text = "$(shield) qa"; statusBar.show();
  statusBar.tooltip = "Click to re-run the last audit";
  context.subscriptions.push(
    output, statusBar,
    vscode.commands.registerCommand("devin-qa-pack.audit", auditAll),
    vscode.commands.registerCommand("devin-qa-pack.auditWorkspace", auditWorkspace),
    vscode.commands.registerCommand("devin-qa-pack.auditSession", auditSession),
    vscode.commands.registerCommand("devin-qa-pack.reaudit",
      () => (lastRun ? audit(lastRun) : auditAll())),
  );
}

function deactivate() {}
module.exports = { activate, deactivate };
// Test-only surface — internals reachable without the editor.
module.exports.__internals__ = {
  deps, sanitizeArgs, validateAuditPayload, sessionMd, renderVerdicts, runWithin,
  audit, auditWorkspace, auditSession, CLI_RANGE, COMPATIBLE_CLI,
  parseVersion, cliCompatStatus, runJson,
  probeVersion, resetProbe: () => { versionProbe = undefined; probedExecutable = undefined; },
};
