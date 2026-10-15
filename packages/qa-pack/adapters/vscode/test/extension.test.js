"use strict";

// Minimal vscode stub injected before requiring the extension — enough for
// the pure rendering paths and the seq-guard to run outside the editor.
const statusBarStub = { text: "", tooltip: "", command: "", show() {}, hide() {} };
const outputStub = { lines: [], appendLine(l) { this.lines.push(l); }, clear() { this.lines = []; }, show() {} };
const errorMessages = [];
const registered = {}; // command id → handler, captured at activate()
let docsOpened = 0;
let testExtras = [];

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
    showErrorMessage: (m) => { errorMessages.push(m); return Promise.resolve(); },
    showTextDocument: () => { docsOpened++; return Promise.resolve({ edit: async () => true }); },
    showQuickPick: () => Promise.resolve(undefined),
    showInputBox: () => Promise.resolve("session-123"),
  },
  workspace: {
    workspaceFolders: undefined,
    getConfiguration: () => ({
      get: (k, d) =>
        k === "executable"
          ? process.env.QA_VSCODE_TEST_EXE || "devin-qa-pack"
          : k === "extraArgs"
            ? testExtras
            : d,
    }),
    openTextDocument: () => Promise.resolve({}),
  },
  commands: {
    registerCommand: (id, fn) => {
      registered[id] = fn;
      return { dispose() {} };
    },
  },
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
  working_directory: "/repo/u/repo",
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
  const deferreds = [];
  const p1 = __internals__.audit(() => new Promise((r) => deferreds.push(r)));
  const p2 = __internals__.audit(() => new Promise((r) => deferreds.push(r)));
  assert.strictEqual(deferreds.length, 2); // thunks run synchronously
  // Newer audit resolves first and renders PARTIAL.
  deferreds[1]({
    sessions_db: "/x",
    sessions: [{ ...SESSION, verdict: "PARTIAL" }],
  });
  await p2;
  assert.strictEqual(statusBarStub.text, "$(warning) qa: PARTIAL");
  // The stale audit resolves late with PASS — must not render.
  deferreds[0](PAYLOAD);
  const r1 = await p1;
  assert.strictEqual(statusBarStub.text, "$(warning) qa: PARTIAL");
  assert.strictEqual(r1.sessions.length, 1);
});

test("a claim picked after a newer audit started does not open", async () => {
  ext.activate({ subscriptions: [] });
  const origPick = vscodeStub.window.showQuickPick;
  const picks = [];
  vscodeStub.window.showQuickPick = (items, opts) =>
    new Promise((resolve) => picks.push({ items, opts, resolve }));
  const docsBefore = docsOpened;
  try {
    const p1 = __internals__.audit(() => Promise.resolve(PAYLOAD)); // seq=1
    await new Promise((r) => setImmediate(r)); // p1 renders → picks[0] open
    assert.strictEqual(picks.length, 1);
    const p2 = __internals__.audit(() => Promise.resolve(PAYLOAD)); // seq=2
    await new Promise((r) => setImmediate(r)); // p2 renders → picks[1] open
    assert.strictEqual(picks.length, 2);
    // Selecting from the superseded picker must not open the stale sheet.
    picks[0].resolve(picks[0].items[0]);
    await p1;
    assert.strictEqual(docsOpened, docsBefore);
    // Sanity: a pick on the current seq does open the claim sheet.
    picks[1].resolve(picks[1].items[0]);
    await p2;
    assert.strictEqual(docsOpened, docsBefore + 1);
  } finally {
    vscodeStub.window.showQuickPick = origPick;
  }
});

test("a failed audit clears a previous warning background", async () => {
  ext.activate({ subscriptions: [] });
  __internals__.renderVerdicts([{ ...SESSION, verdict: "PARTIAL" }]);
  assert.strictEqual(
    statusBarStub.backgroundColor.id,
    "statusBarItem.warningBackground",
  );
  await __internals__.audit(() => Promise.reject(new Error("cli gone")));
  assert.strictEqual(statusBarStub.text, "$(error) qa: unavailable");
  assert.strictEqual(statusBarStub.backgroundColor, undefined);
});

test("multi-root auditWorkspace runs --within per folder and dedupes", async () => {
  ext.activate({ subscriptions: [] });
  const real = __internals__.deps.runCli;
  const calls = [];
  const shared = { ...SESSION, session_id: "shared001" };
  __internals__.deps.runCli = (args) => {
    calls.push(args);
    const within = args[args.indexOf("--within") + 1];
    const own = { ...SESSION, session_id: `s-${within}` };
    return Promise.resolve({
      code: 0,
      stdout: JSON.stringify({
        sessions_db: "/x",
        sessions: [own, shared],
      }),
      stderr: "",
    });
  };
  vscodeStub.workspace.workspaceFolders = [
    { uri: { fsPath: "/repo/api" } },
    { uri: { fsPath: "/repo/web" } },
  ];
  try {
    const payload = await __internals__.auditWorkspace();
    assert.strictEqual(calls.length, 2);
    assert.deepStrictEqual(calls[0], [
      "audit", "--all", "--within", "/repo/api", "--limit", "20", "--json",
    ]);
    assert.deepStrictEqual(calls[1], [
      "audit", "--all", "--within", "/repo/web", "--limit", "20", "--json",
    ]);
    // Two own sessions + the shared one deduped to a single entry.
    assert.strictEqual(payload.sessions.length, 3);
    assert.strictEqual(
      payload.sessions.filter((s) => s.session_id === "shared001").length,
      1,
    );
  } finally {
    __internals__.deps.runCli = real;
    vscodeStub.workspace.workspaceFolders = undefined;
  }
});

test("reaudit re-runs the last scope, not a global audit", async () => {
  ext.activate({ subscriptions: [] });
  const real = __internals__.deps.runCli;
  const calls = [];
  __internals__.deps.runCli = (args) => {
    calls.push(args);
    return Promise.resolve({ code: 0, stdout: JSON.stringify(PAYLOAD), stderr: "" });
  };
  vscodeStub.workspace.workspaceFolders = [{ uri: { fsPath: "/repo/here" } }];
  try {
    await registered["devin-qa-pack.auditWorkspace"]();
    assert.deepStrictEqual(calls[0], [
      "audit", "--all", "--within", "/repo/here", "--limit", "20", "--json",
    ]);
    // Badge click = reaudit — must repeat the --within scope, not --all.
    await registered["devin-qa-pack.reaudit"]();
    assert.deepStrictEqual(calls[1], calls[0]);
  } finally {
    __internals__.deps.runCli = real;
    vscodeStub.workspace.workspaceFolders = undefined;
  }
});

test("sanitizeArgs strips network/evidence-mode flags and their values", () => {
  const s = __internals__.sanitizeArgs;
  assert.deepStrictEqual(s(["--sessions-db", "/x", "--online"]), ["--sessions-db", "/x"]);
  assert.deepStrictEqual(s(["--allow-domain", "evil.com", "--limit", "5"]), ["--limit", "5"]);
  assert.deepStrictEqual(s(["--allow-domain=evil.com"]), []);
  assert.deepStrictEqual(s(["--transcript", "/tmp/t.jsonl"]), []);
  assert.deepStrictEqual(s(["--transcript=/tmp/t.jsonl"]), []);
  assert.deepStrictEqual(s(["--source", "mcp"]), []);
  assert.deepStrictEqual(s(["--source=mcp"]), []);
  // Legitimate local flags pass through untouched.
  assert.deepStrictEqual(
    s(["--sessions-db", "/x", "--within", "/w", "--limit", "9"]),
    ["--sessions-db", "/x", "--within", "/w", "--limit", "9"],
  );
});

test("runJson maps argparse rejections to a version-floor message", async () => {
  const real = __internals__.deps.runCli;
  __internals__.deps.runCli = () =>
    Promise.resolve({
      code: 2,
      stdout: "",
      stderr: "usage: devin-qa-pack [-h] ...\nerror: unrecognized arguments: --within /repo",
    });
  try {
    await assert.rejects(
      __internals__.runJson(["audit", "--all", "--within", "/repo", "--json"]),
      /too old for this extension.*>=0\.1\.2.*upgrade: uv tool upgrade/s,
    );
  } finally {
    __internals__.deps.runCli = real;
  }
});

test("runJson rejects a self-reported out-of-range CLI version", async () => {
  const real = __internals__.deps.runCli;
  __internals__.deps.runCli = () =>
    Promise.resolve({
      code: 0,
      stdout: JSON.stringify({ ...PAYLOAD, version: "0.3.0" }),
      stderr: "",
    });
  try {
    await assert.rejects(
      __internals__.runJson(["audit", "--all", "--json"]),
      /CLI 0\.3\.0 is outside the range.*update the extension/s,
    );
  } finally {
    __internals__.deps.runCli = real;
  }
});

test("runJson accepts payloads with a compatible or absent version", async () => {
  const real = __internals__.deps.runCli;
  __internals__.deps.runCli = () =>
    Promise.resolve({
      code: 0,
      stdout: JSON.stringify({ ...PAYLOAD, version: "0.1.9" }),
      stderr: "",
    });
  try {
    const p = await __internals__.runJson(["audit", "--all", "--json"]);
    assert.strictEqual(p.version, "0.1.9");
    __internals__.deps.runCli = () =>
      Promise.resolve({ code: 0, stdout: JSON.stringify(PAYLOAD), stderr: "" });
    const p2 = await __internals__.runJson(["audit", "--all", "--json"]);
    assert.strictEqual(p2.sessions.length, 1); // no version field — still fine
  } finally {
    __internals__.deps.runCli = real;
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
  // The pin is the single source of truth for CLI_RANGE in extension.js.
  assert.strictEqual(range, __internals__.CLI_RANGE);
  const m = /^>=(\d+)\.(\d+)(?:\.(\d+))?,<(\d+)\.(\d+)(?:\.(\d+))?$/.exec(range);
  assert.ok(m, `unparseable range: ${range}`);
  // The pin must cover the real package version (../pyproject.toml).
  const pyproject = fs.readFileSync(
    path.join(__dirname, "..", "..", "..", "pyproject.toml"),
    "utf8",
  );
  const v = /version\s*=\s*"(\d+)\.(\d+)\.(\d+)"/.exec(pyproject);
  assert.ok(v, "package version not found in pyproject.toml");
  const ver = [Number(v[1]), Number(v[2]), Number(v[3])];
  const lo = [Number(m[1]), Number(m[2]), Number(m[3] || 0)];
  const hi = [Number(m[4]), Number(m[5]), Number(m[6] || 0)];
  const cmp = (a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2];
  assert.ok(
    cmp(ver, lo) >= 0 && cmp(ver, hi) < 0,
    `package ${v[0]} outside pinned range ${range}`,
  );
});

test("cliCompatStatus honors prerelease precedence at the bounds", async () => {
  const s = __internals__.cliCompatStatus;
  assert.strictEqual(s("0.1.2-rc1"), "too-old", "0.1.2-rc1 is below the 0.1.2 floor");
  assert.strictEqual(s("0.1.3-rc1"), "compatible");
  assert.strictEqual(s("0.2.0-rc1"), "compatible", "0.2.0-rc1 is below 0.2.0");
  assert.strictEqual(s("0.2.0"), "too-new");
  assert.strictEqual(s("0.1.2+build5"), "compatible", "build metadata does not downgrade");
});

test("unrecognized args on a compatible CLI blames devinQaPack.extraArgs", async () => {
  __internals__.resetProbe();
  const real = __internals__.deps.runCli;
  __internals__.deps.runCli = (args) =>
    args[0] === "--version"
      ? Promise.resolve({ code: 0, stdout: "devin-qa-pack 0.1.2\n", stderr: "" })
      : Promise.resolve({ code: 2, stdout: "", stderr: "error: unrecognized arguments: --sessoin" });
  try {
    await assert.rejects(
      __internals__.runJson(["audit", "--all", "--json"]),
      /extraArgs contains unsupported option/,
    );
  } finally {
    __internals__.deps.runCli = real;
    __internals__.resetProbe();
  }
});

test("unrecognized args on a too-old CLI names floor and version", async () => {
  __internals__.resetProbe();
  const real = __internals__.deps.runCli;
  __internals__.deps.runCli = (args) =>
    args[0] === "--version"
      ? Promise.resolve({ code: 0, stdout: "devin-qa-pack 0.1.0\n", stderr: "" })
      : Promise.resolve({ code: 2, stdout: "", stderr: "error: unrecognized arguments: --within" });
  try {
    await assert.rejects(
      __internals__.runJson(["audit", "--all", "--within", "/repo", "--json"]),
      /too old.*found 0\.1\.0.*uv tool upgrade devin-qa-pack/s,
    );
  } finally {
    __internals__.deps.runCli = real;
    __internals__.resetProbe();
  }
});

test("a built-in flag duplicated in extraArgs still blames an old CLI", async () => {
  __internals__.resetProbe();
  testExtras = ["--within"]; // user repeats a built-in flag
  const real = __internals__.deps.runCli;
  __internals__.deps.runCli = (args) =>
    args[0] === "--version"
      ? Promise.resolve({ code: 0, stdout: "devin-qa-pack 0.1.0\n", stderr: "" })
      : Promise.resolve({ code: 2, stdout: "", stderr: "error: unrecognized arguments: --within" });
  try {
    await assert.rejects(
      __internals__.runJson(["audit", "--all", "--within", "/repo", "--json"]),
      /too old.*found 0\.1\.0/s,
    );
  } finally {
    testExtras = [];
    __internals__.deps.runCli = real;
    __internals__.resetProbe();
  }
});

test("changing devinQaPack.executable re-probes the version", async () => {
  __internals__.resetProbe();
  testExtras = ["--bogus"];
  const real = __internals__.deps.runCli;
  let probes = 0;
  __internals__.deps.runCli = (args) => {
    if (args[0] === "--version") {
      probes++;
      return Promise.resolve({ code: 0, stdout: "devin-qa-pack 0.1.2\n", stderr: "" });
    }
    return Promise.resolve({ code: 2, stdout: "", stderr: "error: unrecognized arguments: --bogus" });
  };
  try {
    await assert.rejects(__internals__.runJson(["audit", "--all", "--json"]));
    assert.strictEqual(probes, 1);
    await assert.rejects(__internals__.runJson(["audit", "--all", "--json"]));
    assert.strictEqual(probes, 1, "same executable reuses the probe");
    process.env.QA_VSCODE_TEST_EXE = "/opt/other/devin-qa-pack";
    await assert.rejects(__internals__.runJson(["audit", "--all", "--json"]));
    assert.strictEqual(probes, 2, "new executable triggers a fresh probe");
  } finally {
    testExtras = [];
    delete process.env.QA_VSCODE_TEST_EXE;
    __internals__.deps.runCli = real;
    __internals__.resetProbe();
  }
});
