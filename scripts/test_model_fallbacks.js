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
      llm_model_category: null, llm_category_preset: null, agents_model_category: null,
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
        if ("llm_model" in update && !("llm_model_category" in update)) state.llm_model_category = null;
        if ("agents_default_model" in update && !("agents_model_category" in update)) state.agents_model_category = null;
        const selected = state.model_fallbacks[state.llm_provider]?.[state.llm_model_category] ?? [];
        state.llm_category_preset = state.llm_model_category
          ? selected.find((preset) => catalog.some((model) => model.id === preset.model)) ?? selected[0] ?? null
          : null;
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
    const summary = page.locator("[data-model-fallback-settings]");
    const editor = page.getByRole("dialog", { name: "Manage model alternatives", exact: true });
    const cell = (runtime, category) => page.getByRole("button", { name: `Edit ${runtime} ${category}`, exact: true });
    const selectCell = async (runtime, category) => cell(runtime, category).click();
    const openEditor = async (review = false) => {
      await page.getByRole("button", { name: review ? "Review configuration" : "Manage presets", exact: true }).click();
      await editor.waitFor();
      assert.equal(await page.locator('[aria-labelledby="settings-panel-title"]').getAttribute("inert"), "", "Parent Settings is inert while the editor is open");
    };
    const closeEditor = async (apply = false) => {
      await editor.getByRole("button", { name: apply ? "Apply to settings" : "Cancel", exact: true }).click();
      await editor.waitFor({ state: "hidden" });
      assert.equal(await page.locator('[aria-labelledby="settings-panel-title"]').getAttribute("inert"), null);
    };
    const open = async () => {
      await page.locator('[data-tooltip="Settings"]').first().click();
      await page.getByRole("button", { name: "Model", exact: true }).click();
      await page.getByRole("heading", { name: "Model alternatives", exact: true }).waitFor();
      await openEditor();
    };
    const category = (label) => editor.locator(`[data-model-fallback-category="${label.toLowerCase()}"]`);
    const chatScope = page.locator('[data-model-fallback-summary="provider"]');
    const agentScope = page.locator('[data-model-fallback-summary="agents"]');
    const scope = async (label) => {
      const active = await editor.locator("[data-model-fallback-category]").getAttribute("data-model-fallback-category");
      const category = active.charAt(0).toUpperCase() + active.slice(1);
      await selectCell(label.includes("agent and") ? "Agents & workflows" : "Chat & live", category);
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
    assert.match(await cell("Chat & live", "Efficiency").innerText(), /2\/2 listed/);
    assert.equal(await cell("Chat & live", "Balanced").getAttribute("data-category-health"), "empty");
    assert.equal(await chatScope.count(), 1);
    assert.equal(await agentScope.count(), 1);
    assert.match(await chatScope.innerText(), /2 presets/);
    assert.match(await agentScope.innerText(), /Not configured/);
    assert.equal(await cell("Chat & live", "Efficiency").getAttribute("aria-pressed"), "true");
    assert.equal(await cell("Agents & workflows", "Efficiency").getAttribute("aria-pressed"), "false");
    assert.equal(await editor.locator("[data-model-fallback-cell]").count(), 6);
    assert.equal(await page.getByRole("heading", { name: "Efficiency / Chat & live", exact: true }).count(), 1);
    // Native button keyboard activation edits the independent SDK configuration.
    await cell("Agents & workflows", "Efficiency").focus();
    await page.keyboard.press("Enter");
    assert.equal(await cell("Agents & workflows", "Efficiency").getAttribute("aria-pressed"), "true");
    assert.equal(await page.getByRole("heading", { name: "Efficiency / Agents & workflows", exact: true }).count(), 1);
    await selectCell("Chat & live", "Efficiency");
    // Cancel and Escape discard only the child draft and restore parent focus.
    await category("Efficiency").getByRole("button", { name: "Add preset", exact: true }).click();
    await closeEditor();
    assert.match(await chatScope.innerText(), /2 presets/);
    await openEditor();
    assert.equal(await category("Efficiency").locator("[data-preset-health]").count(), 2);
    await page.keyboard.press("Escape");
    await editor.waitFor({ state: "hidden" });
    assert.equal(await page.getByRole("dialog", { name: "Settings", exact: true }).count(), 1);
    assert.equal(await page.getByRole("button", { name: "Manage presets", exact: true }).evaluate((element) => element === document.activeElement), true);
    await openEditor();
    await modelPicker("Efficiency model 1").click();
    const firstMenu = page.getByRole("listbox", { name: "Efficiency model 1", exact: true });
    await firstMenu.getByRole("textbox", { name: "Filter models…" }).fill("Anthropic");
    assert.equal(await firstMenu.getByRole("option", { name: "Model A", exact: true }).count(), 1);
    assert.equal(await firstMenu.getByRole("option", { name: "Model B", exact: true }).count(), 0);
    await firstMenu.getByRole("textbox", { name: "Filter models…" }).press("Escape");
    await firstMenu.waitFor({ state: "hidden" });
    assert.equal(await editor.count(), 1, "Escape closes only the model menu, not the preset editor");
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
    await selectCell("Chat & live", "Balanced");
    await category("Balanced").getByRole("button", { name: "Add current selection" }).click();
    assert.equal(await modelPicker("Balanced model 1").innerText(), "Model A");
    assert.equal(await category("Balanced").getAttribute("data-category-health"), "valid");
    await page.getByLabel("Balanced context 1", { exact: true }).fill("256000");
    assert.match(await category("Balanced").innerText(), /exceeds the advertised/);
    await page.getByLabel("Balanced context 1", { exact: true }).fill("0");
    assert.match(await category("Balanced").innerText(), /between 1,000 and 5,000,000/);
    await page.getByLabel("Balanced context 1", { exact: true }).fill("128000");
    assert.equal(await category("Balanced").getAttribute("data-category-health"), "valid");
    await selectCell("Agents & workflows", "Efficiency");
    await category("Efficiency").getByRole("button", { name: "Add current selection" }).click();
    assert.equal(await modelPicker("Efficiency model 1").innerText(), "Model B");
    await chooseModel("Efficiency model 1", "model-c");
    assert.equal(await modelPicker("Efficiency model 1").innerText(), "Model C");
    await chooseModel("Efficiency model 1", "model-b");
    assert.equal(await page.getByRole("button", { name: "Efficiency context 1" }).innerText(), "Long context");
    for (const label of ["Efficiency effort 1", "Efficiency context 1"]) {
      await page.getByRole("button", { name: label, exact: true }).click();
      const menu = page.getByRole("listbox", { name: label, exact: true });
      assert.equal(await menu.getByRole("option", { selected: true }).evaluate((element) => element === document.activeElement), true);
      await page.keyboard.press("Escape");
      await menu.waitFor({ state: "hidden" });
      assert.equal(await editor.count(), 1, "Escape dismisses compact effort/context menus before the modal");
    }
    assert.equal(await category("Efficiency").getAttribute("data-category-health"), "valid");
    assert.match(await cell("Agents & workflows", "Efficiency").innerText(), /1 preset/);
    assert.match(await chatScope.innerText(), /2 presets/, "Child draft does not update the parent before Apply");
    await scope("Configure chat and live model alternatives");
    assert.equal(await modelPicker("Efficiency model 1").innerText(), "retired-id");
    await closeEditor(true);
    assert.match(await chatScope.innerText(), /3 presets/);
    assert.match(await agentScope.innerText(), /1 preset/);
    assert.equal(writes.length, 0, "Applying the child draft does not persist settings");
    assert.equal(await summary.locator("[data-model-fallback-category]").count(), 0, "Settings keeps only the compact summary");
    await openEditor(true);
    assert.equal(await cell("Chat & live", "Efficiency").getAttribute("aria-pressed"), "true", "Review opens the affected configuration directly");
    await closeEditor();
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
    await closeEditor();
    await page.getByRole("button", { name: "Apply & refresh models", exact: true }).click();
    await page.getByRole("button", { name: "Apply & refresh models", exact: true }).waitFor();
    assert.equal(state.llm_model, "retired-id");
    assert.equal(writes.length, 2);
    assert.equal("llm_model" in writes[1], false);
    const checkModels = async () => {
      const reopen = await editor.isVisible();
      if (reopen) await closeEditor();
      await page.getByRole("button", { name: "Check models", exact: true }).click();
      await page.getByRole("button", { name: "Check models", exact: true }).waitFor();
      if (reopen) await openEditor();
    };
    await openEditor();
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
    assert.equal(await cell("Chat & live", "Balanced").getAttribute("data-category-health"), "setup");
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
    await closeEditor();
    await page.getByRole("button", { name: "Apply & refresh models", exact: true }).click();
    await openEditor();
    await page.getByRole("status").filter({ hasText: "Connect GitHub" }).waitFor();
    assert.equal(await cell("Chat & live", "Balanced").getAttribute("data-category-health"), "setup");
    assert.equal(await cell("Chat & live", "Efficiency").getAttribute("data-category-health"), "setup");
    state.github_token_source = "settings";
    await closeEditor();
    await page.getByRole("button", { name: "Apply & refresh models", exact: true }).click();
    await openEditor();
    await selectCell("Chat & live", "Balanced");
    await category("Balanced").locator('[data-preset-health="valid"]').waitFor();
    await closeEditor();
    await page.getByRole("button", { name: "LLM provider", exact: true }).click();
    await page.getByRole("option", { name: "Azure AI Foundry", exact: true }).click();
    await page.getByRole("button", { name: "Check models", exact: true }).waitFor();
    await openEditor();
    await selectCell("Chat & live", "Efficiency");
    await category("Efficiency").getByRole("button", { name: "Add current selection" }).click();
    assert.equal(await category("Efficiency").getAttribute("data-category-health"), "unverified");
    await page.getByRole("status").filter({ hasText: "does not publish a model catalogue" }).waitFor();
    await closeEditor(true);
    await page.getByRole("button", { name: "LLM provider", exact: true }).click();
    await page.getByRole("option", { name: "GitHub Copilot", exact: true }).click();
    await openEditor();
    await selectCell("Chat & live", "Balanced");
    await category("Balanced").locator('[data-preset-health="valid"]').waitFor();
    await closeEditor();
    const writesBeforeFailedSave = writes.length;
    rejectSave = true;
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await page.getByRole("alert").filter({ hasText: "Duplicate effective preset" }).waitFor();
    assert.equal(await page.getByRole("dialog").count(), 1);
    assert.equal(writes.length, writesBeforeFailedSave);
    await openEditor();
    await selectCell("Agents & workflows", "Efficiency");
    await page.getByRole("button", { name: "Remove Efficiency preset 1" }).click();
    assert.equal(await cell("Agents & workflows", "Efficiency").getAttribute("data-category-health"), "empty");
    assert.match(await agentScope.innerText(), /1 preset/, "Removal is isolated in the child draft until Apply");
    await closeEditor(true);
    assert.match(await agentScope.innerText(), /Not configured/);
    assert.match(await chatScope.innerText(), /3 presets/);
    await openEditor();
    await selectCell("Chat & live", "Efficiency");
    const configurations = editor.getByRole("group", { name: "Categories and runtimes" });
    const artifactDir = process.env.MODEL_FALLBACK_UI_ARTIFACTS;
    if (artifactDir) require("node:fs").mkdirSync(artifactDir, { recursive: true });
    for (const width of [1200, 600, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await configurations.scrollIntoViewIfNeeded();
      const chatBox = await cell("Chat & live", "Efficiency").boundingBox();
      const agentBox = await cell("Agents & workflows", "Efficiency").boundingBox();
      assert.ok(chatBox && agentBox);
      assert.equal(Math.round(chatBox.y), Math.round(agentBox.y), "Both runtimes stay visible beside each category");
      assert.ok(await configurations.evaluate((el) => el.scrollWidth <= el.clientWidth + 1), "Overview fits the dedicated modal");
      assert.ok(await editor.evaluate((el) => el.scrollWidth <= el.clientWidth + 1), "Preset editor fits the dedicated modal");
      await cell("Chat & live", "Efficiency").focus();
      await page.keyboard.press("Enter");
      assert.equal(await cell("Chat & live", "Efficiency").getAttribute("aria-pressed"), "true");
      const apply = editor.getByRole("button", { name: "Apply to settings", exact: true });
      const close = editor.getByRole("button", { name: "Close preset editor", exact: true });
      await apply.focus();
      await page.keyboard.press("Tab");
      assert.equal(await close.evaluate((element) => element === document.activeElement), true, "Tab wraps inside the dedicated modal");
      await page.keyboard.press("Shift+Tab");
      assert.equal(await apply.evaluate((element) => element === document.activeElement), true, "Shift+Tab wraps inside the dedicated modal");
      await modelPicker("Efficiency model 1").click();
      const popup = page.getByRole("listbox", { name: "Efficiency model 1", exact: true });
      assert.equal(await popup.evaluate((el) => getComputedStyle(el).position), "fixed");
      const box = await popup.boundingBox();
      assert.ok(box && box.x >= 0 && box.y >= 0 && box.x + box.width <= width + 1 && box.y + box.height <= 901, "Shared model menu stays within the viewport");
      if (artifactDir) {
        await page.screenshot({ path: require("node:path").join(artifactDir, `model-alternatives-menu-${width}.png`) });
      }
      await popup.getByRole("textbox", { name: "Filter models…" }).focus();
      await page.keyboard.press("Shift+Tab");
      assert.equal(await popup.getByRole("option").last().evaluate((element) => element === document.activeElement), true, "Portaled menu traps keyboard focus");
      await page.keyboard.press("Tab");
      assert.equal(await popup.getByRole("textbox", { name: "Filter models…" }).evaluate((element) => element === document.activeElement), true);
      await popup.getByRole("textbox", { name: "Filter models…" }).press("Escape");
      assert.equal(await editor.count(), 1);
      if (artifactDir) {
        await page.screenshot({ path: require("node:path").join(artifactDir, `model-alternatives-${width}.png`) });
      }
    }
    await closeEditor();
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
    await composerModel.click();
    assert.equal(await composerMenu.locator('[data-menu-parent="Presets"]').count(), 1);
    const configuredMenuBox = await composerMenu.boundingBox();
    const configuredHeaderBox = await composerMenu.locator('[data-menu-parent="Presets"]').boundingBox();
    assert.ok(configuredMenuBox && configuredHeaderBox && configuredHeaderBox.y >= configuredMenuBox.y && configuredHeaderBox.y < configuredMenuBox.y + configuredMenuBox.height, "Configured Presets start at the top, not scrolled away to the catalogue model");
    const chatCategories = composerMenu.getByRole("group", { name: "Presets", exact: true });
    assert.equal(await chatCategories.getByRole("option", { name: /^Efficiency/ }).count(), 1);
    assert.equal(await chatCategories.getByRole("option", { name: /^Balanced/ }).count(), 1);
    assert.equal(await chatCategories.getByRole("option", { name: /^Intelligence/ }).count(), 0, "Unconfigured categories are hidden");
    await composerMenu.getByRole("textbox", { name: "Filter models…" }).fill("Balanced");
    const beforeChatPreset = writes.length;
    const chatDefaults = [state.llm_model, state.llm_reasoning_effort, state.llm_max_input_tokens];
    await composerMenu.getByRole("textbox", { name: "Filter models…" }).press("Enter");
    await composerMenu.waitFor({ state: "hidden" });
    await composerModel.filter({ hasText: "Balanced" }).waitFor();
    assert.deepEqual(writes[beforeChatPreset], { llm_model_category: "balanced" }, "A category selection stores intent, not a fixed model/configuration");
    assert.deepEqual([state.llm_model, state.llm_reasoning_effort, state.llm_max_input_tokens], chatDefaults);
    assert.equal(await page.getByRole("button", { name: "Reasoning effort", exact: true }).count(), 0);
    assert.equal(await page.getByRole("button", { name: "Context size", exact: true }).count(), 0);
    await composerModel.click();
    await composerMenu.getByRole("textbox", { name: "Filter models…" }).fill("Balanced");
    assert.equal(await composerMenu.getByRole("option", { selected: true }).count(), 1, "The selected profile is checked");
    await composerMenu.getByRole("textbox", { name: "Filter models…" }).fill("retired-id");
    assert.equal(await composerMenu.getByRole("option").count(), 1, "Preset search matches the underlying model id");
    assert.match(await composerMenu.getByRole("option").innerText(), /Efficiency/);
    assert.match(await composerMenu.getByRole("option").innerText(), /Engine chooses/);
    await composerMenu.getByRole("textbox", { name: "Filter models…" }).fill("model-c");
    await composerMenu.getByRole("textbox", { name: "Filter models…" }).press("Enter");
    await composerModel.filter({ hasText: "Model C" }).waitFor();
    assert.equal(state.llm_model_category, null, "Selecting a direct model exits category mode");
    state.agents_default_model = "model-c";
    state.agents_reasoning_effort = "high";
    state.agents_context_tier = "default";
    const agents = await (await page.request.get(`${BASE}/api/agents`)).json();
    assert.ok(agents.length > 0, "The anonymous demo provides a seeded agent");
    await page.goto(`${BASE}/agents/${agents[0].public_id}`, { waitUntil: "networkidle" });
    await page.reload({ waitUntil: "networkidle" });
    const agentModel = page.getByRole("button", { name: "Agent model", exact: true });
    await agentModel.click();
    const agentMenu = page.getByRole("listbox", { name: "Agent model", exact: true });
    assert.equal(await agentMenu.locator('[data-menu-parent="Presets"]').count(), 1);
    const agentCategories = agentMenu.getByRole("group", { name: "Presets", exact: true });
    assert.equal(await agentCategories.getByRole("option", { name: /^Efficiency/ }).count(), 1);
    assert.equal(await agentCategories.getByRole("option", { name: /^Balanced/ }).count(), 0, "Chat presets do not leak into SDK menus");
    await agentMenu.getByRole("textbox", { name: "Filter models…" }).fill("Presets");
    assert.equal(await agentMenu.getByRole("option").count(), 1, "The parent group can be searched");
    const beforeAgentPreset = writes.length;
    const agentDefaults = [state.agents_default_model, state.agents_reasoning_effort, state.agents_context_tier];
    await agentMenu.getByRole("option").first().click();
    await agentMenu.waitFor({ state: "hidden" });
    await agentModel.filter({ hasText: "Efficiency" }).waitFor();
    assert.deepEqual(writes[beforeAgentPreset], { agents_model_category: "efficiency" }, "SDK category intent is independent from manual model/configuration");
    assert.deepEqual([state.agents_default_model, state.agents_reasoning_effort, state.agents_context_tier], agentDefaults);
    assert.equal(state.llm_model, "model-c", "An agent category does not change chat configuration");
    assert.equal(await page.getByRole("button", { name: "Context tier", exact: true }).count(), 0);
    assert.deepEqual(state.model_fallbacks, saved, "Selecting presets never changes the preset definitions");
    const beforeManage = writes.length;
    await agentModel.click();
    await agentMenu.getByRole("button", { name: "Manage presets in Settings...", exact: true }).click();
    await page.getByRole("heading", { name: "Model alternatives", exact: true }).waitFor();
    assert.equal(writes.length, beforeManage, "Managing existing presets only opens Settings");
    await page.getByRole("button", { name: "Close", exact: true }).click();
    await agentModel.click();
    await agentMenu.getByRole("textbox", { name: "Filter models…" }).fill("model-c");
    await agentMenu.getByRole("textbox", { name: "Filter models…" }).press("Enter");
    await agentModel.filter({ hasText: "Model C" }).waitFor();
    assert.equal(state.agents_model_category, null);
    state.model_fallbacks = {};
    const beforeEmptyMenus = writes.length;
    for (const [route, label] of [
      [`/chats/regex-for-semver-tags`, "Model"],
      [`/topics/onboarding-checklist`, "Model"],
      [`/agents/${agents[0].public_id}`, "Agent model"],
    ]) {
      await page.goto(`${BASE}${route}`, { waitUntil: "networkidle" });
      await page.reload({ waitUntil: "networkidle" });
      await page.getByRole("button", { name: label, exact: true }).click();
      const menu = page.getByRole("listbox", { name: label, exact: true });
      assert.equal(await menu.locator('[data-menu-parent="Presets"]').count(), 0, "Presets group is absent without configured profiles");
      assert.equal(await menu.locator("[data-menu-category]").count(), 0);
      assert.equal(await menu.getByRole("button", { name: "Manage presets in Settings...", exact: true }).count(), 0);
      assert.ok(await menu.getByRole("option").count() > 0, "Ordinary catalogue models remain available");
      assert.equal(writes.length, beforeEmptyMenus);
      await page.keyboard.press("Escape");
    }
    console.log("Model alternatives UI: configured-only presets in chat/topics/agents, atomic selection, management link, modal drafts, keyboard, health and persistence passed.");
  } finally {
    await browser.close();
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
