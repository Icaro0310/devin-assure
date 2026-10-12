"use strict";

const vscode = require("vscode");
const cp = require("child_process");
let statusBar, output;
// Monotonic counter — a slow audit finishing late must not overwrite a
// newer verdict already rendered on the status bar (anti-race guard).
// lastRun holds the newest completed audit — the badge re-runs that scope.
let auditSeq = 0, lastRun = null;

// Same range as the "devin-qa-pack-cli" pin in package.json (see README).
const CLI_RANGE = ">=0.1,<0.2";
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
// Injectable for tests. Exit codes are results, not errors: 0 = all PASS,
// 1 = flagged (PARTIAL/UNVERIFIED), 2 = could not run.
const deps = {
  runCli: (args) =>
    new Promise((resolve, reject) => {
      const { executable, extraArgs } = config();
      cp.execFile(executable, args.concat(sanitizeArgs(extraArgs)),
        { timeout: 120000, maxBuffer: 8 * 1024 * 1024 },
        (err, stdout, stderr) => {
          if (err && err.killed) return reject(new Error("devin-qa-pack timed out after 120s"));
          if (err && err.code === "ENOENT") {
            return reject(new Error("devin-qa-pack not found on PATH. Install it " +
              "(uv tool install devin-qa-pack) or set devinQaPack.executable."));
          }
          resolve({ code: err ? err.code : 0, stdout, stderr });
        });
    }),
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
async function runJson(args) {
  const { code, stdout, stderr } = await deps.runCli(args);
  try {
    return JSON.parse(stdout);
  } catch {
    output.appendLine((stdout || "") + (stderr || ""));
    throw new Error(`devin-qa-pack audit returned no JSON (exit ${code}) — see output channel`);
  }
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
  audit, auditWorkspace, auditSession, CLI_RANGE,
};
