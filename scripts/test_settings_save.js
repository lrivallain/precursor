// DEMO_PORT=<port> NODE_PATH=.demo/node_modules node scripts/test_settings_save.js
const assert = require("node:assert/strict");
const { chromium } = require("playwright");
const { BASE, prepareDemoPage } = require("./demo_browser");

async function main() {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    await prepareDemoPage(page);
    const state = await (await page.request.get(`${BASE}/api/settings`)).json();
    const writes = [];
    let fail = false;
    let hold = null;
    await page.route("**/api/settings", async (route) => {
      if (route.request().method() === "PUT") {
        const payload = route.request().postDataJSON();
        writes.push(payload);
        if (hold) await hold;
        if (fail) {
          await route.fulfill({ status: 503, json: { detail: "Save temporarily unavailable" } });
          return;
        }
        const { api_keys, ...preferences } = payload;
        Object.assign(state, preferences);
        if (payload.llm_model === "") state.llm_model = "demo-model";
        state.llm_providers = structuredClone(state.llm_providers ?? {});
        for (const [provider, config] of Object.entries(state.llm_providers)) {
          if (config.key) {
            state.llm_providers_present = {
              ...state.llm_providers_present,
              [provider]: { ...state.llm_providers_present?.[provider], key: true },
            };
            delete config.key;
          }
        }
        if (api_keys) {
          state.api_keys_present = { ...state.api_keys_present };
          for (const key of Object.keys(api_keys)) state.api_keys_present[key] = true;
        }
      }
      await route.fulfill({ json: state });
    });
    await page.route("**/api/llm/models*", (route) => route.fulfill({
      json: [{ id: "demo-model", name: "Demo model", publisher: "Demo", context_window: 128000 }],
    }));
    await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
    const panel = page.getByRole("dialog", { name: "Settings", exact: true });
    const confirmation = page.getByRole("alertdialog");
    const open = async () => {
      await page.locator('[data-tooltip="Settings"]').first().click();
      await panel.getByRole("button", { name: "GitHub", exact: true }).click();
      await panel.getByPlaceholder("owner/name").waitFor();
    };
    const save = panel.getByRole("button", { name: "Save", exact: true });
    const repository = panel.getByPlaceholder("owner/name");
    const secret = panel.getByPlaceholder("ghp_...");
    const success = panel.getByRole("status").filter({ hasText: "Settings saved." });

    await open();
    await repository.fill("demo/settings-save");
    await secret.fill("demo-token-not-a-credential");
    await save.click();
    await success.waitFor();
    assert.equal(await repository.inputValue(), "demo/settings-save", "The current category stays open");
    assert.equal(await secret.inputValue(), "", "Saved secrets are cleared");
    assert.equal(writes.at(-1).api_keys.github_token, "demo-token-not-a-credential");
    await save.click();
    await success.waitFor();
    assert.equal(writes.at(-1).api_keys, undefined, "Repeat saves do not resubmit a cleared secret");
    await panel.getByRole("button", { name: "Cancel", exact: true }).click();
    await panel.waitFor({ state: "hidden" });
    assert.equal(await confirmation.count(), 0, "Saved settings close without a discard warning");

    await open();
    assert.equal(await repository.inputValue(), "demo/settings-save", "Reopening loads the saved preference");
    await repository.fill("demo/unsaved");
    await panel.getByRole("button", { name: "Cancel", exact: true }).click();
    await confirmation.getByRole("button", { name: "Keep editing", exact: true }).click();
    assert.equal(await repository.inputValue(), "demo/unsaved");
    fail = true;
    await save.click();
    await panel.getByRole("alert").filter({ hasText: "Save temporarily unavailable" }).waitFor();
    assert.equal(await repository.inputValue(), "demo/unsaved", "Failed saves preserve edits");
    fail = false;
    await save.click();
    await success.waitFor();
    assert.equal(await panel.getByRole("alert").count(), 0, "Retry clears the error");

    let release;
    hold = new Promise((resolve) => { release = resolve; });
    await repository.fill("demo/submitted");
    await secret.fill("demo-submitted-token");
    const request = page.waitForRequest((request) =>
      request.url().endsWith("/api/settings") && request.method() === "PUT",
    );
    await save.click();
    await request;
    assert.ok(await panel.getByRole("button", { name: "Saving..." }).isDisabled());
    await repository.fill("demo/edited-during-save");
    await secret.fill("demo-newer-token");
    release();
    hold = null;
    await save.waitFor();
    assert.equal(await repository.inputValue(), "demo/edited-during-save");
    assert.equal(await secret.inputValue(), "demo-newer-token", "A late save cannot clear a newer secret edit");
    assert.equal(await success.count(), 0, "A late save cannot mark newer edits as saved");
    await page.keyboard.press("Escape");
    await confirmation.getByRole("button", { name: "Keep editing", exact: true }).click();
    await save.click();
    await success.waitFor();
    await page.keyboard.press("Escape");
    await panel.waitFor({ state: "hidden" });
    assert.equal(await confirmation.count(), 0, "The latest successful save becomes the close baseline");

    await open();
    await panel.getByRole("button", { name: "Model", exact: true }).click();
    await panel.getByRole("button", { name: "LLM provider", exact: true }).click();
    await page.getByRole("option", { name: /^OpenAI / }).click();
    const providerKey = panel.locator('[data-llm-provider-config="openai"] input[type="password"]');
    await providerKey.fill("demo-provider-key-not-a-credential");
    await panel.getByRole("button", { name: "Global default provider", exact: true }).click();
    await page.getByRole("option", { name: /^OpenAI / }).click();
    await save.click();
    await success.waitFor();
    assert.equal(await providerKey.inputValue(), "", "Provider secrets are cleared after saving");
    assert.ok(await panel.getByRole("button", { name: "Global default model", exact: true }).filter({ hasText: "Demo model" }).isVisible(),
      "The default model reflects the saved server selection");
    await save.click();
    await success.waitFor();
    assert.equal(writes.at(-1).llm_providers?.openai?.key, undefined, "Repeat saves omit cleared provider secrets");
    assert.equal(writes.at(-1).llm_model, undefined, "Repeat saves preserve the resolved default model");
    await panel.getByRole("button", { name: "Cancel", exact: true }).click();
    await panel.waitFor({ state: "hidden" });
    assert.equal(await confirmation.count(), 0, "Redacted provider fields do not trigger a discard warning");
    console.log("Settings save passed");
  } finally {
    await browser.close();
  }
}

main().catch((error) => { console.error(error); process.exit(1); });
