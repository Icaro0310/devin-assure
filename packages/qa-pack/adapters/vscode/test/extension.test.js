"use strict";

// Minimal vscode stub injected before requiring the extension — enough for
// the pure rendering paths and the seq-guard to run outside the editor.
const statusBarStub = { text: "", tooltip: "", command: "", show() {}, hide() {} };
const outputStub = { lines: [], appendLine(l) { this.lines.push(l); }, clear() { this.lines = []; }, show() {} };

const vscodeStub = {
  ThemeColor: class {
    constructor(id) {
      this.id = id;
    }
  },
  StatusBarAlignment: { Left: 1, Right: 2 },
  window: {
    createStatusBarItem: () => statusBarStub,
    createOutputChannel: () => outputStub,
    showInformationMessage: () => Promise.resolve(),
    showWarningMessage: () => Promise.resolve(),
    showErrorMessage: () => Promise.resolve(),
    showTextDocument: () => Promise.resolve({ edit: async () => true }),
    showQuickPick: () => Promise.resolve(undefined),
    showInputBox: () => Promise.resolve("session-123"),
  },
  workspace: {
    workspaceFolders: undefined,
    getConfiguration: () => ({
      get: (k, d) =>
        k === "executable"
          ? process.env.QA_VSCODE_TEST_EXE || "devin-qa-pack"
          : d,
    }),
    openTextDocument: () => Promise.resolve({}),
  },
  commands: { registerCommand: () => ({ dispose() {} }) },
};

const Module = require("module");
const origRequire = Module.prototype.require;
Module.prototype.require = function (id) {
  if (id === "vscode") return vscodeStub;
  return origRequire.apply(this, arguments);
};

const ext = require("../extension.js");
const { __internals__ } = ext;
const test = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const fs = require("node:fs");

const SESSION = {
  session_id: "abcdef1234567890",
  title: "fix tests",
  working_directory: "/home/u/repo",
  verdict: "PASS",
  summary: { verified: 2, disputed: 0, unverifiable: 0 },
  claims: [
    {
      kind: "tests",
      detail: "pytest",
      status: "verified",
      evidence: "tool_call_state #3 ran pytest",
      node_id: 42,
      excerpt: "ran the suite",
    },
  ],
};

const PAYLOAD = { sessions_db: "/tmp/sessions.db", sessions: [SESSION] };

test("validateAuditPayload accepts the documented contract", () => {
  const v = __internals__.validateAuditPayload;
  assert.deepStrictEqual(v(PAYLOAD), { ok: true, missing: [] });
  assert.strictEqual(v({ sessions_db: "/x", sessions: [] }).ok, true);
});

test("validateAuditPayload flags drifted payloads", () => {
  const v = __internals__.validateAuditPayload;
  const cases = [
    [null, ["<root>"]],
    [{}, ["sessions_db", "sessions"]],
    [{ sessions_db: "/x", sessions: {} }, ["sessions"]],
    [
      { sessions_db: "/x", sessions: [{ ...SESSION, verdict: "BOGUS" }] },
      ["sessions[0].verdict"],
    ],
    [
      { sessions_db: "/x", sessions: [{ ...SESSION, claims: null }] },
      ["sessions[0].claims"],
    ],
    [
      { sessions_db: "/x", sessions: [{ ...SESSION, summary: "x" }] },
      ["sessions[0].summary"],
    ],
    [
      { sessions_db: "/x", sessions: [{ ...SESSION, session_id: 7 }] },
      ["sessions[0].session_id"],
    ],
  ];
  for (const [payload, expected] of cases) {
    const r = v(payload);
    assert.strictEqual(r.ok, false, JSON.stringify(payload));
    assert.deepStrictEqual(r.missing.sort(), expected.sort());
  }
});

test("renderVerdicts shows the worst verdict and per-verdict counts", async () => {
  ext.activate({ subscriptions: [] });
  __internals__.renderVerdicts([SESSION, SESSION]);
  assert.strictEqual(statusBarStub.text, "$(pass) qa: PASS (2)");
  assert.strictEqual(statusBarStub.backgroundColor, undefined);
  __internals__.renderVerdicts([SESSION, { ...SESSION, verdict: "PARTIAL" }]);
  assert.strictEqual(statusBarStub.text, "$(warning) qa: PARTIAL");
  assert.strictEqual(
    statusBarStub.backgroundColor.id,
    "statusBarItem.warningBackground",
  );
  assert.match(statusBarStub.tooltip, /1 PASS · 1 PARTIAL · 0 UNVERIFIED/);
  __internals__.renderVerdicts([{ ...SESSION, verdict: "UNVERIFIED" }]);
  assert.strictEqual(statusBarStub.text, "$(warning) qa: UNVERIFIED");
});

test("overlapping audits: a stale result never overwrites a newer one", async () => {
  ext.activate({ subscriptions: [] });
  const real = __internals__.deps.runCli;
  const deferreds = [];
  __internals__.deps.runCli = (args) =>
    new Promise((resolve) => deferreds.push({ args, resolve }));
  try {
    const args = ["audit", "--all", "--json"];
    const p1 = __internals__.audit(args); // seq=1
    const p2 = __internals__.audit(args); // seq=2
    // Newer audit resolves first and renders PARTIAL.
    deferreds[1].resolve({
      code: 1,
      stdout: JSON.stringify({
        sessions_db: "/x",
        sessions: [{ ...SESSION, verdict: "PARTIAL" }],
      }),
      stderr: "",
    });
    await p2;
    assert.strictEqual(statusBarStub.text, "$(warning) qa: PARTIAL");
    // The stale audit resolves late with PASS — must not render.
    deferreds[0].resolve({ code: 0, stdout: JSON.stringify(PAYLOAD), stderr: "" });
    const r1 = await p1;
    assert.strictEqual(statusBarStub.text, "$(warning) qa: PARTIAL");
    assert.strictEqual(r1.sessions.length, 1);
  } finally {
    __internals__.deps.runCli = real;
  }
});

test("auditWorkspace passes --within <workspaceFolder> to the CLI", async () => {
  ext.activate({ subscriptions: [] });
  const real = __internals__.deps.runCli;
  const calls = [];
  __internals__.deps.runCli = (args) => {
    calls.push(args);
    return Promise.resolve({
      code: 0,
      stdout: JSON.stringify(PAYLOAD),
      stderr: "",
    });
  };
  vscodeStub.workspace.workspaceFolders = [{ uri: { fsPath: "/repo/here" } }];
  try {
    await __internals__.auditWorkspace();
    assert.deepStrictEqual(calls[0], [
      "audit", "--all", "--within", "/repo/here", "--limit", "20", "--json",
    ]);
  } finally {
    __internals__.deps.runCli = real;
    vscodeStub.workspace.workspaceFolders = undefined;
  }
});

test("deps.runCli rejects with an actionable error when the CLI is absent", async () => {
  process.env.QA_VSCODE_TEST_EXE = "devin-qa-pack-definitely-missing-9f3b";
  try {
    await assert.rejects(
      __internals__.deps.runCli(["audit", "--all", "--json"]),
      /not found on PATH/,
    );
  } finally {
    delete process.env.QA_VSCODE_TEST_EXE;
  }
});

test("package.json devin-qa-pack-cli pin covers the package version", () => {
  const pkg = JSON.parse(
    fs.readFileSync(path.join(__dirname, "..", "package.json"), "utf8"),
  );
  const range = pkg["devin-qa-pack-cli"];
  assert.ok(range, 'package.json must declare "devin-qa-pack-cli"');
  const m = /^>=(\d+)\.(\d+),<(\d+)\.(\d+)$/.exec(range);
  assert.ok(m, `unparseable range: ${range}`);
  // The pin must cover the real package version (../pyproject.toml).
  const pyproject = fs.readFileSync(
    path.join(__dirname, "..", "..", "..", "pyproject.toml"),
    "utf8",
  );
  const v = /version\s*=\s*"(\d+)\.(\d+)\.\d+"/.exec(pyproject);
  assert.ok(v, "package version not found in pyproject.toml");
  const [maj, min] = [Number(v[1]), Number(v[2])];
  assert.ok(
    (maj > Number(m[1]) || (maj === Number(m[1]) && min >= Number(m[2]))) &&
      (maj < Number(m[3]) || (maj === Number(m[3]) && min < Number(m[4]))),
    `package ${v[0]} outside pinned range ${range}`,
  );
});
