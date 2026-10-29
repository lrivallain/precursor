// DEMO_PORT=<port> NODE_PATH=.demo/node_modules node scripts/test_foundry_models.js
const assert = require("node:assert/strict");
const { chromium } = require("playwright");
const { BASE, prepareDemoPage } = require("./demo_browser");

async function main() {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const [width, colorScheme] of [[1200, "light"], [390, "dark"]]) {
      const page = await browser.newPage({ viewport: { width, height: 900 }, colorScheme });
      await prepareDemoPage(page);
      const base = await (await page.request.get(`${BASE}/api/settings`)).json();
      const state = {
        ...base, llm_provider: "azure_foundry", llm_model: "legacy-deployment",
        llm_providers: { azure_foundry: {
          endpoint: "https://demo.openai.azure.com", deployment: "legacy-deployment",
        } },
        llm_providers_present: { azure_foundry: { key: true } },
      };
      const writes = [];
      await page.route("**/api/settings", async (route) => {
        if (route.request().method() === "PUT") {
          const payload = route.request().postDataJSON();
          writes.push(payload);
          Object.assign(state, payload);
        }
        await route.fulfill({ json: state });
      });
      await page.route("**/api/llm/models*", (route) => route.fulfill({ json:
        state.llm_providers.azure_foundry.deployment.split(",").map((id) => ({
          id: id.trim(), name: id.trim(), publisher: "Azure", catalog_provider: "azure_foundry",
        })).filter((model) => model.id),
      }));
      await page.goto(BASE, { waitUntil: "networkidle" });
      async function open() {
        const settings = page.locator('[data-tooltip="Settings"]').first();
        const box = await settings.boundingBox();
        if (box && box.x < 0) await page.getByRole("button", { name: "Open navigation", exact: true }).click();
        await settings.click();
        await page.getByRole("button", { name: "Model", exact: true }).click();
        await page.locator("[data-deployment-names]").waitFor();
      }
      await open();
      const field = page.locator("[data-deployment-names]");
      const names = () => field.getByRole("listitem").allTextContents();
      const input = field.getByRole("textbox", { name: "Deployment names", exact: true });
      const add = field.getByRole("button", { name: "Add model", exact: true });
      assert.deepEqual(await names(), ["legacy-deploymentDefault"]);
      assert.equal(await add.isDisabled(), true);
      await input.fill(" fast-model ");
      await input.press("Enter");
      assert.deepEqual(await names(), ["legacy-deploymentDefault", "fast-model"]);
      assert.equal(await input.inputValue(), "");
      assert.equal(writes.length, 0, "Adding a bubble only changes the Settings draft");
      await input.fill("fast-model");
      await add.click();
      assert.equal(await field.getByRole("alert").textContent(), "This deployment is already added.");
      assert.equal(await field.getByRole("listitem").count(), 2);
      await input.fill("one,two");
      await input.press("Enter");
      assert.match(await field.getByRole("alert").textContent(), /one deployment name at a time/);
      await input.fill("smart-model");
      await add.click();
      await field.getByRole("button", { name: "Remove deployment legacy-deployment", exact: true }).click();
      assert.deepEqual(await names(), ["fast-modelDefault", "smart-model"]);
      await input.fill("");
      await page.getByRole("button", { name: "Apply & refresh models", exact: true }).click();
      await page.getByText("2 models", { exact: true }).waitFor();
      assert.equal(writes.at(-1).llm_providers.azure_foundry.deployment, "fast-model, smart-model");
      assert.equal("key" in writes.at(-1).llm_providers.azure_foundry, false, "Blank keys remain omitted");
      await page.getByRole("button", { name: "Close", exact: true }).click();
      await open();
      assert.deepEqual(await names(), ["fast-modelDefault", "smart-model"]);
      await input.fill("model-" + "long-name-".repeat(12));
      await input.press("Enter");
      assert.equal(await field.evaluate((element) => element.scrollWidth <= element.clientWidth), true, "Long names fit without horizontal overflow");
      await field.getByRole("button", { name: "Remove deployment fast-model", exact: true }).click();
      await field.getByRole("button", { name: "Remove deployment smart-model", exact: true }).click();
      await field.getByRole("button", { name: /Remove deployment model-long-name-/ }).click();
      assert.equal(await field.getByRole("listitem").count(), 0);
      assert.equal(await add.isDisabled(), true);
      await input.fill("replacement");
      await input.press("Enter");
      assert.deepEqual(await names(), ["replacementDefault"]);
      await page.getByRole("button", { name: "Close", exact: true }).click();
      await open();
      assert.deepEqual(await names(), ["fast-modelDefault", "smart-model"], "Closing discards unsaved edits");
      await page.close();
    }
    console.log("Foundry model bubbles: desktop/mobile, add/remove, validation, persistence and cancellation passed.");
  } finally {
    await browser.close();
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
