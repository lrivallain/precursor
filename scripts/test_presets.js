// DEMO_PORT=<port> NODE_PATH=.demo/node_modules node scripts/test_presets.js
const assert = require("node:assert/strict");
const { chromium } = require("playwright");
const { BASE, prepareDemoPage } = require("./demo_browser");

async function main() {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    await prepareDemoPage(page);
    const base = await (await page.request.get(`${BASE}/api/settings`)).json();
    const state = { ...base, model_fallbacks: { categories: [] }, github_token_source: "settings" };
    const writes = [];
    await page.route("**/api/settings", async (route) => {
      if (route.request().method() === "PUT") {
        const payload = route.request().postDataJSON();
        writes.push(payload);
        Object.assign(state, payload);
      }
      await route.fulfill({ json: state });
    });
    await page.route("**/api/llm/models*", (route) => route.fulfill({ json: ["a-model", "b-model"].map((id) => ({
      id, name: id, publisher: "Demo", context_window: 128000,
      supported_reasoning_efforts: ["low", "high"], catalog_provider: "github_copilot",
    })) }));
    await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
    await page.getByRole("button", { name: "Settings" }).first().click();
    await page.getByRole("tab", { name: "Presets" }).or(page.getByRole("button", { name: "Presets", exact: true })).first().click();
    const root = page.locator("[data-presets-settings]");
    await root.waitFor();
    await root.getByRole("button", { name: /Efficiency/ }).first().click();
    assert.ok(await root.getByText("Efficiency").count());
    await root.getByRole("button", { name: /Add a-model|^\+/ }).first().click().catch(() => {});
    await page.getByRole("button", { name: "Save" }).first().click();
    await page.waitForTimeout(400);
    assert.ok(writes.some((w) => w.model_fallbacks?.categories?.length >= 1), "categories saved");
    console.log("presets ok", JSON.stringify(writes.at(-1).model_fallbacks).slice(0, 200));
  } finally {
    await browser.close();
  }
}
main().catch((e) => { console.error(e); process.exit(1); });
