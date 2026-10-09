// Node 22.18+: node --experimental-strip-types --test scripts/test_model_preset_health.js
const assert = require("node:assert/strict");
const { before, test } = require("node:test");
let health;
before(async () => { health = await import("../frontend/src/lib/modelPresetHealth.ts"); });
const preset = (model = "available", patch = {}) => ({
  model, reasoning_effort: "", context_tokens: 128000, context_tier: "default", ...patch,
});
const categories = (efficiency = [], balanced = [], intelligence = []) => ({ efficiency, balanced, intelligence });
const catalog = (state = "ready") => ({
  state, detail: "Catalogue check unavailable.",
  models: [{ id: "available", context_window: 128000, supported_reasoning_efforts: ["low", "high"] }],
});

test("empty categories remain optional, not invalid", () => {
  const summary = health.summarizePresets([], catalog("setup"));
  assert.equal(summary.state, "empty");
  assert.equal(summary.label, "Not configured");
});

test("all listed, compatible presets validate without inference", () => {
  const checks = health.checkPresets(categories([preset("available", { reasoning_effort: "high" })]), catalog(), false);
  assert.deepEqual(checks.efficiency, [{ listed: true, issues: [] }]);
  assert.equal(health.summarizePresets(checks.efficiency, catalog()).state, "valid");
  assert.equal(health.summarizePresets(checks.efficiency, catalog()).label, "1/1 listed");
});

test("a missing model needs review but a listed alternative still counts", () => {
  const checks = health.checkPresets(categories([preset("retired"), preset()]), catalog(), false);
  assert.match(checks.efficiency[0].issues[0], /Model not listed/);
  assert.equal(checks.efficiency[1].listed, true);
  const summary = health.summarizePresets(checks.efficiency, catalog());
  assert.equal(summary.state, "review");
  assert.equal(summary.listed, 1);
  assert.equal(summary.total, 2);
});

test("failed, unsupported, pending or missing-setup catalogues cannot declare a model absent", () => {
  for (const state of ["unavailable", "unsupported", "unchecked", "setup", "checking"]) {
    const check = catalog(state);
    const result = health.checkPresets(categories([preset("retired")]), check, false);
    assert.equal(result.efficiency[0].listed, false);
    assert.deepEqual(result.efficiency[0].issues, []);
    assert.notEqual(health.summarizePresets(result.efficiency, check).state, "valid");
    assert.notEqual(health.summarizePresets(result.efficiency, check).state, "review");
  }
});

test("missing model, Auto, invalid budgets and duplicate effective profiles need review", () => {
  for (const entry of [preset(""), preset("auto"), preset("available", { context_tokens: 999 }),
                       preset("available", { context_tokens: 5000001 }), preset("available", { context_tokens: NaN })]) {
    const checks = health.checkPresets(categories([entry]), catalog(), false);
    assert.ok(checks.efficiency[0].issues.length > 0);
  }
  const duplicates = health.checkPresets(categories([preset()], [preset()]), catalog(), false);
  assert.match(duplicates.efficiency[0].issues[0], /duplicated/);
  assert.match(duplicates.balanced[0].issues[0], /duplicated/);
});

test("budget boundaries match the persisted schema", () => {
  for (const tokens of [1000, 5000000]) {
    const checks = health.checkPresets(categories([preset("available", { context_tokens: tokens })]), catalog("unchecked"), false);
    assert.deepEqual(checks.efficiency[0].issues, []);
  }
});

test("only advertised effort/window capabilities produce compatible or review signals", () => {
  const result = health.checkPresets(categories([
    preset("available", { reasoning_effort: "max" }),
    preset("available", { context_tokens: 256000 }),
  ]), catalog(), false);
  assert.match(result.efficiency[0].issues[0], /Effort "max"/);
  assert.match(result.efficiency[1].issues[0], /exceeds the advertised/);
  const unknown = catalog();
  unknown.models[0].supported_reasoning_efforts = [];
  assert.match(health.checkPresets(categories([preset("available", { reasoning_effort: "high" })]), unknown, false).efficiency[0].issues[0], /not advertised/);
});

test("agents compare context tiers, not chat budgets, and do not infer long-context support", () => {
  const checks = health.checkPresets(categories([
    preset("available", { context_tier: "long_context", context_tokens: 5000000 }),
    preset(),
  ]), catalog(), true);
  assert.deepEqual(checks.efficiency.map((check) => check.issues), [[], []]);
  const duplicates = health.checkPresets(categories([preset()], [preset("available", { context_tokens: 32000 })]), catalog(), true);
  assert.match(duplicates.balanced[0].issues[0], /duplicated/);
});

test("provider credentials are checked from presence metadata, never secret values", () => {
  const provider = { id: "github_copilot", label: "GitHub Copilot", uses_github_token: true, fields: [] };
  assert.match(health.providerSetupIssue({ github_token_source: "none" }, provider), /Connect GitHub/);
  assert.equal(health.providerSetupIssue({ github_token_source: "gh-cli" }, provider), null);
  const openai = { id: "openai", label: "OpenAI", uses_github_token: false, fields: [
    { name: "key", label: "API key", secret: true, required: true },
    { name: "endpoint", label: "Endpoint", secret: false, required: true },
  ] };
  const settings = { llm_providers: { openai: { endpoint: "https://example.test" } }, llm_providers_present: { openai: { key: true } } };
  assert.equal(health.providerSetupIssue(settings, openai), null);
  settings.llm_providers_present.openai.key = false;
  assert.match(health.providerSetupIssue(settings, openai), /API key/);
});
