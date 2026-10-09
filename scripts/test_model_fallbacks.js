// Hermetic model-alternative settings smoke test against the anonymous seeded demo.
// DEMO_PORT=<port> NODE_PATH=.demo/node_modules node scripts/test_model_fallbacks.js
const assert = require("node:assert/strict");
const { chromium } = require("playwright");
const { BASE, prepareDemoPage } = require("./demo_browser");

async function main() {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1200, height: 900 } });
    await prepareDemoPage(page);
    const base = await (await page.request.get(`${BASE}/api/settings`)).json();
    const preset = (model, effort = "") => ({
      model, reasoning_effort: effort, context_tokens: 128000, context_tier: "default",
    });
    const state = {
      ...base, llm_model: "model-a", llm_reasoning_effort: "high",
      llm_max_input_tokens: 128000, agents_default_model: "model-b",
      agents_reasoning_effort: "low", agents_context_tier: "long_context",
      github_token_source: "settings", agents_enabled: true, agents_available: true, agents_runtime_started: true,
      llm_providers: { ...base.llm_providers, azure_foundry: { endpoint: "https://example.test", deployment: "demo-deployment" } },
      llm_providers_present: { ...base.llm_providers_present, azure_foundry: { key: true } },
      model_fallbacks: { github_copilot: {
        efficiency: [preset("model-a"), preset("model-b", "low")],
        balanced: [], intelligence: [],
      } },
    };
    const writes = [];
    let rejectSave = false;
    let rejectCatalog = false;
    let rejectAgentCatalog = false;
    let wrongCatalogSource = false;
    let providerChecks = 0;
    let agentChecks = 0;
    await page.route("**/api/settings", async (route) => {
      if (route.request().method() === "PUT") {
        if (rejectSave) {
          await route.fulfill({ status: 422, json: { detail: "Duplicate effective preset in agents" } });
          return;
        }
        const update = route.request().postDataJSON();
        writes.push(update);
        Object.assign(state, update);
      }
      await route.fulfill({ json: state });
    });
    const catalog = ["model-a", "model-b", "model-c"].map((id) => ({
      id, name: `Model ${id.slice(-1).toUpperCase()}`,
      publisher: id === "model-a" ? "Anthropic" : "OpenAI",
      supported_reasoning_efforts: ["low", "high"], context_window: 128000,
    }));
    await page.route("**/api/llm/models*", (route) => {
      providerChecks++;
      const provider = new URL(route.request().url()).searchParams.get("provider") || state.llm_provider;
      return route.fulfill(rejectCatalog
        ? { status: 502, json: { detail: "Provider catalogue temporarily unavailable" } }
        : { json: catalog.map((model) => ({ ...model, catalog_provider: wrongCatalogSource ? "mock" : provider })) });
    });
    await page.route("**/api/agents/models", (route) => {
      agentChecks++;
      return route.fulfill(rejectAgentCatalog
        ? { status: 502, json: { detail: "SDK catalogue temporarily unavailable" } }
        : { json: catalog });
    });
    await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
    const open = async () => {
      await page.locator('[data-tooltip="Settings"]').first().click();
      await page.getByRole("button", { name: "Model", exact: true }).click();
      await page.getByRole("heading", { name: "Model alternatives", exact: true }).waitFor();
    };
    const category = (label) => page.locator("[data-model-fallback-category]")
      .filter({ has: page.getByRole("heading", { name: label, exact: true }) });
    const chatScope = page.getByRole("button", { name: "Configure chat and live model alternatives" });
    const agentScope = page.getByRole("button", { name: "Configure agent and workflow model alternatives" });
    const scope = async (label) => {
      await page.getByRole("button", { name: label, exact: true }).click();
    };
    const modelPicker = (label) => page.getByRole("button", { name: label, exact: true });
    const chooseModel = async (label, id, custom = false) => {
      await modelPicker(label).click();
      const menu = page.getByRole("listbox", { name: label, exact: true });
      await menu.getByRole("textbox", { name: "Filter models…" }).fill(id);
      if (custom) await menu.getByRole("textbox", { name: "Filter models…" }).press("Enter");
      else await menu.getByRole("option").first().click();
      await menu.waitFor({ state: "hidden" });
    };
    await open();
    await category("Efficiency").locator('[data-preset-health="valid"]').first().waitFor();
    assert.equal(await category("Efficiency").getAttribute("data-category-health"), "valid");
    assert.match(await category("Efficiency").innerText(), /2\/2 listed/);
    assert.equal(await category("Balanced").getAttribute("data-category-health"), "empty");
    assert.equal(await chatScope.count(), 1);
    assert.equal(await agentScope.count(), 1);
    assert.match(await chatScope.innerText(), /2 presets/);
    assert.match(await agentScope.innerText(), /Not configured/);
    assert.equal(await chatScope.getAttribute("aria-pressed"), "true");
    assert.equal(await agentScope.getAttribute("aria-pressed"), "false");
    assert.equal(await page.getByRole("heading", { name: "Chat & live presets", exact: true }).count(), 1);
    // Native button keyboard activation edits the independent SDK configuration.
    await agentScope.focus();
    await page.keyboard.press("Enter");
    assert.equal(await agentScope.getAttribute("aria-pressed"), "true");
    assert.equal(await page.getByRole("heading", { name: "Agent & workflow presets", exact: true }).count(), 1);
    await chatScope.click();
    await modelPicker("Efficiency model 1").click();
    const firstMenu = page.getByRole("listbox", { name: "Efficiency model 1", exact: true });
    await firstMenu.getByRole("textbox", { name: "Filter models…" }).fill("Anthropic");
    assert.equal(await firstMenu.getByRole("option", { name: "Model A", exact: true }).count(), 1);
    assert.equal(await firstMenu.getByRole("option", { name: "Model B", exact: true }).count(), 0);
    await firstMenu.getByRole("textbox", { name: "Filter models…" }).press("Escape");
    await firstMenu.waitFor({ state: "hidden" });
    assert.equal(await page.getByRole("dialog").count(), 1, "Escape closes only the model menu, not Settings");
    assert.equal(await modelPicker("Efficiency model 1").evaluate((el) => el === document.activeElement), true);
    await chooseModel("Efficiency model 1", "retired-id", true);
    assert.equal(await category("Efficiency").getAttribute("data-category-health"), "review");
    assert.match(await category("Efficiency").innerText(), /Model not listed/);
    await page.getByRole("button", { name: "Move Efficiency preset 1 down" }).click();
    assert.equal(await modelPicker("Efficiency model 2").innerText(), "retired-id");
    await page.getByRole("button", { name: "Remove Efficiency preset 1" }).click();
    await category("Efficiency").getByRole("button", { name: "Add preset", exact: true }).click();
    await chooseModel("Efficiency model 2", "model-c");
    assert.equal(await modelPicker("Efficiency model 2").innerText(), "Model C");
    await chooseModel("Efficiency model 2", "replacement-id", true);
    await page.getByLabel("Efficiency context 2", { exact: true }).fill("64000");
    await category("Balanced").getByRole("button", { name: "Add current selection" }).click();
    assert.equal(await modelPicker("Balanced model 1").innerText(), "Model A");
    assert.equal(await category("Balanced").getAttribute("data-category-health"), "valid");
    await page.getByLabel("Balanced context 1", { exact: true }).fill("256000");
    assert.match(await category("Balanced").innerText(), /exceeds the advertised/);
    await page.getByLabel("Balanced context 1", { exact: true }).fill("0");
    assert.match(await category("Balanced").innerText(), /between 1,000 and 5,000,000/);
    await page.getByLabel("Balanced context 1", { exact: true }).fill("128000");
    assert.equal(await category("Balanced").getAttribute("data-category-health"), "valid");
    await scope("Configure agent and workflow model alternatives");
    await category("Efficiency").getByRole("button", { name: "Add current selection" }).click();
    assert.equal(await modelPicker("Efficiency model 1").innerText(), "Model B");
    await chooseModel("Efficiency model 1", "model-c");
    assert.equal(await modelPicker("Efficiency model 1").innerText(), "Model C");
    await chooseModel("Efficiency model 1", "model-b");
    assert.equal(await page.getByRole("button", { name: "Efficiency context 1" }).innerText(), "Long context");
    assert.equal(await category("Efficiency").getAttribute("data-category-health"), "valid");
    assert.match(await agentScope.innerText(), /1 preset/);
    assert.match(await chatScope.innerText(), /3 presets/);
    await scope("Configure chat and live model alternatives");
    assert.equal(await modelPicker("Efficiency model 1").innerText(), "retired-id");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await page.getByRole("dialog").waitFor({ state: "hidden" });
    assert.equal(writes.length, 1);
    const saved = writes[0].model_fallbacks;
    assert.deepEqual(saved.github_copilot.efficiency.map((p) => p.model), ["retired-id", "replacement-id"]);
    assert.equal(saved.github_copilot.efficiency[1].context_tokens, 64000);
    assert.equal(saved.github_copilot.balanced[0].reasoning_effort, "high");
    assert.equal(saved.agents.efficiency[0].context_tier, "long_context");
    assert.equal(saved.agents.efficiency[0].reasoning_effort, "low");
    state.llm_model = "retired-id";
    await open();
    assert.equal(await modelPicker("Efficiency model 2").innerText(), "replacement-id");
    await modelPicker("Efficiency model 2").click();
    const savedMenu = page.getByRole("listbox", { name: "Efficiency model 2", exact: true });
    await savedMenu.getByRole("textbox", { name: "Filter models…" }).fill("replacement-id");
    assert.equal(await savedMenu.getByRole("option", { name: /replacement-id/ }).getAttribute("aria-selected"), "true");
    await savedMenu.getByRole("option", { name: /replacement-id/ }).click();
    await page.getByRole("button", { name: "Apply & refresh models", exact: true }).click();
    await page.getByRole("button", { name: "Apply & refresh models", exact: true }).waitFor();
    assert.equal(state.llm_model, "retired-id");
    assert.equal(writes.length, 2);
    assert.equal("llm_model" in writes[1], false);
    const checkModels = async () => {
      await page.getByRole("button", { name: "Check models", exact: true }).click();
      await page.getByRole("button", { name: "Check models", exact: true }).waitFor();
    };
    const beforeChecks = writes.length;
    const previousProviderChecks = providerChecks;
    const previousAgentChecks = agentChecks;
    rejectCatalog = true;
    await checkModels();
    assert.equal(await category("Efficiency").getAttribute("data-category-health"), "unverified");
    assert.doesNotMatch(await category("Efficiency").innerText(), /Model not listed/);
    await page.getByRole("status").filter({ hasText: "Provider catalogue temporarily unavailable" }).waitFor();
    rejectCatalog = false;
    await checkModels();
    assert.equal(await category("Efficiency").getAttribute("data-category-health"), "review");
    assert.ok(providerChecks > previousProviderChecks && agentChecks > previousAgentChecks);
    assert.equal(writes.length, beforeChecks, "Catalogue checks make no settings writes or inference calls");
    wrongCatalogSource = true;
    await checkModels();
    assert.equal(await category("Balanced").getAttribute("data-category-health"), "setup");
    await page.getByRole("status").filter({ hasText: "different catalogue source" }).waitFor();
    wrongCatalogSource = false;
    await checkModels();
    rejectAgentCatalog = true;
    await checkModels();
    await scope("Configure agent and workflow model alternatives");
    assert.equal(await category("Efficiency").getAttribute("data-category-health"), "unverified");
    assert.doesNotMatch(await category("Efficiency").innerText(), /Model not listed/);
    rejectAgentCatalog = false;
    await checkModels();
    assert.equal(await category("Efficiency").getAttribute("data-category-health"), "valid");
    await scope("Configure chat and live model alternatives");
    state.github_token_source = "none";
    await page.getByRole("button", { name: "Apply & refresh models", exact: true }).click();
    await page.getByRole("status").filter({ hasText: "Connect GitHub" }).waitFor();
    assert.equal(await category("Balanced").getAttribute("data-category-health"), "setup");
    state.github_token_source = "settings";
    await page.getByRole("button", { name: "Apply & refresh models", exact: true }).click();
    await category("Balanced").locator('[data-preset-health="valid"]').waitFor();
    await page.getByRole("button", { name: "LLM provider", exact: true }).click();
    await page.getByRole("option", { name: "Azure AI Foundry", exact: true }).click();
    await page.getByRole("button", { name: "Check models", exact: true }).waitFor();
    await category("Efficiency").getByRole("button", { name: "Add current selection" }).click();
    assert.equal(await category("Efficiency").getAttribute("data-category-health"), "unverified");
    await page.getByRole("status").filter({ hasText: "does not publish a model catalogue" }).waitFor();
    await page.getByRole("button", { name: "LLM provider", exact: true }).click();
    await page.getByRole("option", { name: "GitHub Copilot", exact: true }).click();
    await category("Balanced").locator('[data-preset-health="valid"]').waitFor();
    const writesBeforeFailedSave = writes.length;
    rejectSave = true;
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await page.getByRole("alert").filter({ hasText: "Duplicate effective preset" }).waitFor();
    assert.equal(await page.getByRole("dialog").count(), 1);
    assert.equal(writes.length, writesBeforeFailedSave);
    await scope("Configure agent and workflow model alternatives");
    await page.getByRole("button", { name: "Remove Efficiency preset 1" }).click();
    assert.match(await agentScope.innerText(), /Not configured/);
    assert.match(await chatScope.innerText(), /3 presets/);
    const configurations = page.getByRole("group", { name: "Independent model alternative configurations" });
    const artifactDir = process.env.MODEL_FALLBACK_UI_ARTIFACTS;
    if (artifactDir) require("node:fs").mkdirSync(artifactDir, { recursive: true });
    for (const width of [1200, 600, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await configurations.scrollIntoViewIfNeeded();
      const chatBox = await chatScope.boundingBox();
      const agentBox = await agentScope.boundingBox();
      assert.ok(chatBox && agentBox);
      const paneWidth = await page.locator("[data-model-fallback-settings]").evaluate((el) => el.clientWidth);
      if (paneWidth >= 384) assert.equal(Math.round(chatBox.y), Math.round(agentBox.y));
      else assert.ok(agentBox.y > chatBox.y, "Narrow panes stack the independent configurations");
      if (width === 390) {
        assert.ok(chatBox.width >= 240, "The mobile settings rail leaves useful space for configuration cards");
        assert.equal(await page.getByRole("button", { name: "Model", exact: true }).getAttribute("aria-label"), "Model");
      }
      assert.ok(await configurations.evaluate((el) => el.scrollWidth <= el.clientWidth + 1), "Scope cards fit the settings pane");
      assert.ok(await page.locator("[data-model-fallback-settings]").evaluate((el) => el.scrollWidth <= el.clientWidth + 1), "Preset editor fits the settings pane");
      await chatScope.focus();
      await page.keyboard.press("Enter");
      assert.equal(await chatScope.getAttribute("aria-pressed"), "true");
      await modelPicker("Efficiency model 1").click();
      const popup = page.getByRole("listbox", { name: "Efficiency model 1", exact: true });
      assert.equal(await popup.evaluate((el) => getComputedStyle(el).position), "fixed");
      const box = await popup.boundingBox();
      assert.ok(box && box.x >= 0 && box.y >= 0 && box.x + box.width <= width + 1 && box.y + box.height <= 901, "Shared model menu stays within the viewport");
      if (artifactDir) {
        await page.screenshot({ path: require("node:path").join(artifactDir, `model-alternatives-menu-${width}.png`) });
      }
      await popup.getByRole("textbox", { name: "Filter models…" }).press("Escape");
      assert.equal(await page.getByRole("dialog").count(), 1);
      if (artifactDir) {
        await page.screenshot({ path: require("node:path").join(artifactDir, `model-alternatives-${width}.png`) });
      }
    }
    rejectSave = false;
    await page.getByRole("button", { name: "Close", exact: true }).click();
    await page.setViewportSize({ width: 1200, height: 900 });
    await page.goto(`${BASE}/chats/regex-for-semver-tags`, { waitUntil: "networkidle" });
    const composerModel = page.getByRole("button", { name: "Model", exact: true });
    await composerModel.click();
    const composerMenu = page.getByRole("listbox", { name: "Model", exact: true });
    assert.equal(await composerMenu.evaluate((el) => getComputedStyle(el).position), "absolute");
    await composerMenu.getByRole("textbox", { name: "Filter models…" }).fill("model-c");
    await composerMenu.getByRole("textbox", { name: "Filter models…" }).press("Enter");
    await composerMenu.waitFor({ state: "hidden" });
    await composerModel.filter({ hasText: "Model C" }).waitFor();
    assert.equal(await composerModel.innerText(), "Model C");
    assert.equal(state.llm_model, "model-c");
    console.log("Model alternatives UI: shared prompt dropdowns, search, custom/retired ids, viewport-safe menus, scope isolation, keyboard, persistence and save errors passed.");
  } finally {
    await browser.close();
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
