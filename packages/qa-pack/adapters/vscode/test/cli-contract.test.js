"use strict";

// Contract guard: the CLI-version pin in package.json, the COMPATIBLE_CLI
// bounds in extension.js, and the real devin-qa-pack version must agree —
// and the floor is patch-level: `--within` first shipped in CLI 0.1.2, so
// a 0.1.0 CLI accepts the argv but argparse exits 2 on the flag. The
// extension must map that to an upgrade message, not "no JSON".

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const ADAPTER = path.join(__dirname, "..");
const pkg = JSON.parse(fs.readFileSync(path.join(ADAPTER, "package.json"), "utf8"));
const src = fs.readFileSync(path.join(ADAPTER, "extension.js"), "utf8");

// packages/qa-pack/pyproject.toml is the source of truth for the CLI version.
const pyproject = fs.readFileSync(
  path.join(ADAPTER, "..", "..", "pyproject.toml"),
  "utf8",
);
const realVersion = /^version\s*=\s*"([^"]+)"/m.exec(pyproject)[1];

function parseRange(range) {
  const m = /^>=(\d+)\.(\d+)(?:\.(\d+))?,<(\d+)\.(\d+)(?:\.(\d+))?$/.exec(range);
  assert.ok(m, `pin "${range}" must be a ">=x.y[.z],<x.y[.z]" range`);
  return {
    min: [Number(m[1]), Number(m[2]), Number(m[3] || 0)],
    maxExclusive: [Number(m[4]), Number(m[5]), Number(m[6] || 0)],
  };
}

test("package.json pins the devin-qa-pack CLI range", () => {
  assert.ok(pkg["devin-qa-pack-cli"], "missing devin-qa-pack-cli field");
  const { min } = parseRange(pkg["devin-qa-pack-cli"]);
  // The floor must name the patch that shipped --within, not just 0.1.
  assert.ok(min[2] > 0, "min bound must be patch-level (>=0.1.2)");
});

test("COMPATIBLE_CLI matches the package.json pin", () => {
  const { min, maxExclusive } = parseRange(pkg["devin-qa-pack-cli"]);
  const m = /COMPATIBLE_CLI\s*=\s*\{\s*min:\s*\[(\d+),\s*(\d+),\s*(\d+)\],\s*maxExclusive:\s*\[(\d+),\s*(\d+),\s*(\d+)\]/.exec(src);
  assert.ok(m, "COMPATIBLE_CLI bounds not found in extension.js");
  assert.deepStrictEqual(
    [Number(m[1]), Number(m[2]), Number(m[3])], min);
  assert.deepStrictEqual(
    [Number(m[4]), Number(m[5]), Number(m[6])], maxExclusive);
});

test("the real devin-qa-pack version falls inside the pinned range", () => {
  const { min, maxExclusive } = parseRange(pkg["devin-qa-pack-cli"]);
  const ver = realVersion.split(".").map(Number);
  const cmp = (a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2];
  assert.ok(
    cmp(ver, min) >= 0 && cmp(ver, maxExclusive) < 0,
    `devin-qa-pack ${realVersion} outside ${pkg["devin-qa-pack-cli"]}`,
  );
});

test("a too-old CLI (argparse rejects new flags) maps to an upgrade hint", () => {
  // Without this mapping, CLI 0.1.0 exits 2 on --within and the user sees
  // only "no JSON" — the extension must name the version floor instead.
  assert.ok(
    /unrecognized arguments|invalid choice/.test(src) &&
      src.includes("uv tool upgrade devin-qa-pack"),
    "extension.js must map argparse rejections to an upgrade message",
  );
});

test("the CLI self-reports its version inside the audit payload", () => {
  // report.py's audits_payload must emit "version" so future drift is a
  // precise version comparison, not a missing-field failure.
  const report = fs.readFileSync(
    path.join(ADAPTER, "..", "..", "src", "devin_qa_pack", "report.py"),
    "utf8",
  );
  assert.ok(
    /"version":\s*__version__/.test(report),
    'audits_payload must emit "version": __version__',
  );
});
