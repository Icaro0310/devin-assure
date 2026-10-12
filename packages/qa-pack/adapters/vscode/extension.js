"use strict";

const vscode = require("vscode");
const cp = require("child_process");

let statusBar;
let output;
// Monotonic counter — a slow audit finishing late must not overwrite a
// newer verdict already rendered on the status bar (anti-race guard).
let auditSeq = 0;

function config() {
  const c = vscode.workspace.getConfiguration("devinQaPack");
  return {
    executable: c.get("executable", "devin-qa-pack"),
    extraArgs: c.get("extraArgs", []),
    limit: c.get("limit", 20),
  };
}

// Injectable so tests can fake the CLI. Exit codes are results, not
// errors: 0 = all PASS, 1 = flagged (PARTIAL/UNVERIFIED), 2 = could not run.
const deps = {
  runCli: (args) =>
    new Promise((resolve, reject) => {
      const { executable, extraArgs } = config();
      cp.execFile(executable, args.concat(extraArgs),
        { timeout: 120000, maxBuffer: 8 * 1024 * 1024 },
        (err, stdout, stderr) => {
          if (err && err.killed) {
            return reject(new Error("devin-qa-pack timed out after 120s"));
          }
          if (err && err.code === "ENOENT") {
            return reject(new Error(
              "devin-qa-pack not found on PATH. Install it " +
                "(uv tool install devin-qa-pack) or set devinQaPack.executable."));
          }
          resolve({ code: err ? err.code : 0, stdout, stderr });
        });
    }),
};

const VERDICTS = ["PASS", "PARTIAL", "UNVERIFIED"];

// Contract: `devin-qa-pack audit --json` → {sessions_db, sessions:
// [{session_id, title, working_directory, verdict, summary: {verified,
// disputed, unverifiable}, claims: [{kind, detail, status, evidence,
// node_id, excerpt}]}]}. Drift surfaces loudly, never renders blank.
function validateAuditPayload(payload) {
  const missing = [];
  if (!payload || typeof payload !== "object") {
    return { ok: false, missing: ["<root>"] };
  }
  if (typeof payload.sessions_db !== "string") missing.push("sessions_db");
  if (!Array.isArray(payload.sessions)) {
    missing.push("sessions");
  } else {
    payload.sessions.forEach((s, i) => {
      if (!s || typeof s.session_id !== "string") missing.push(`sessions[${i}].session_id`);
      if (!s || !VERDICTS.includes(s.verdict)) missing.push(`sessions[${i}].verdict`);
      if (!s || typeof s.summary !== "object" || s.summary === null) {
        missing.push(`sessions[${i}].summary`);
      }
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
    output.appendLine(stdout || "");
    output.appendLine(stderr || "");
    throw new Error(`devin-qa-pack audit returned no JSON (exit ${code}) — ` +
      "see output channel");
  }
}

async function openMarkdown(title, body) {
  const doc = await vscode.workspace.openTextDocument({
    language: "markdown",
    content: `${title}\n\n${body}`,
  });
  await vscode.window.showTextDocument(doc, { preview: true });
}

function sessionMd(a) {
  const s = a.summary || {};
  return [
    `- **verdict**: ${a.verdict}`,
    a.title ? `- **title**: ${a.title}` : null,
    `- **cwd**: ${a.working_directory}`,
    `- **claims**: ${a.claims.length} — ${s.verified || 0} verified · ` +
      `${s.disputed || 0} disputed · ${s.unverifiable || 0} unverifiable`,
    "",
    ...a.claims.map((c) => `- [${c.status}] ${c.kind}/${c.detail || "-"}: ${c.evidence}`),
  ].filter((l) => l !== null).join("\n");
}

function renderVerdicts(sessions) {
  const counts = { PASS: 0, PARTIAL: 0, UNVERIFIED: 0 };
  sessions.forEach((a) => { counts[a.verdict] = (counts[a.verdict] || 0) + 1; });
  const worst = counts.PARTIAL ? "PARTIAL" : counts.UNVERIFIED ? "UNVERIFIED" : "PASS";
  statusBar.text =
    worst === "PASS" ? `$(pass) qa: PASS (${sessions.length})`
      : `$(warning) qa: ${worst}`;
  statusBar.backgroundColor = worst === "PASS" ? undefined
    : new vscode.ThemeColor("statusBarItem.warningBackground");
  statusBar.tooltip = `devin-qa-pack — ${sessions.length} session(s): ` +
    `${counts.PASS} PASS · ${counts.PARTIAL} PARTIAL · ${counts.UNVERIFIED} UNVERIFIED`;
  statusBar.show();
}

async function audit(args) {
  const seq = ++auditSeq;
  statusBar.text = "$(sync~spin) qa: auditing";
  statusBar.show();
  try {
    const payload = await runJson(args);
    const shape = validateAuditPayload(payload);
    if (!shape.ok) {
      throw new Error(`audit JSON missing ${shape.missing.join(", ")} — ` +
        "extension/CLI drift (compatible: devin-qa-pack >=0.1,<0.2)");
    }
    if (seq === auditSeq) {
      renderVerdicts(payload.sessions);
      const pick = await vscode.window.showQuickPick(
        payload.sessions.map((a) => ({
          label: `${a.verdict}  ${a.session_id.slice(0, 8)}` +
            (a.title ? `  ${a.title}` : ""),
          description: `${a.claims.length} claim(s)`,
          audit: a,
        })),
        { placeHolder: "audit verdicts — pick a session for claim detail" },
      );
      if (pick) {
        await openMarkdown(`# qa audit — ${pick.audit.session_id}`, sessionMd(pick.audit));
      }
    }
    return payload;
  } catch (e) {
    if (seq === auditSeq) {
      statusBar.text = "$(error) qa: unavailable";
      statusBar.tooltip = e.message;
      statusBar.show();
      vscode.window.showErrorMessage(e.message);
    }
    return null;
  }
}

const auditAll = () =>
  audit(["audit", "--all", "--limit", String(config().limit), "--json"]);

function auditWorkspace() {
  const folder = (vscode.workspace.workspaceFolders || [])[0];
  if (!folder) {
    vscode.window.showErrorMessage("devin-qa-pack: no workspace folder open");
    return Promise.resolve(null);
  }
  return audit(["audit", "--all", "--within", folder.uri.fsPath,
    "--limit", String(config().limit), "--json"]);
}

async function auditSession() {
  const id = await vscode.window.showInputBox({
    prompt: "Session to audit (exact id or unique prefix)",
  });
  if (id === undefined || id.trim() === "") return null;
  return audit(["audit", "--session", id.trim(), "--json"]);
}

function activate(context) {
  output = vscode.window.createOutputChannel("devin-qa-pack");
  statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
  statusBar.command = "devin-qa-pack.audit";
  statusBar.text = "$(shield) qa";
  statusBar.tooltip = "Click to audit Devin sessions";
  statusBar.show();

  context.subscriptions.push(
    output,
    statusBar,
    vscode.commands.registerCommand("devin-qa-pack.audit", auditAll),
    vscode.commands.registerCommand("devin-qa-pack.auditWorkspace", auditWorkspace),
    vscode.commands.registerCommand("devin-qa-pack.auditSession", auditSession),
  );
}

function deactivate() {}

module.exports = { activate, deactivate };
// Test-only surface — internals reachable without the editor.
module.exports.__internals__ = {
  deps, validateAuditPayload, sessionMd, renderVerdicts, audit, auditWorkspace,
};
