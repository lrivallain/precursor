// DEMO_PORT=<port> NODE_PATH=.demo/node_modules node scripts/test_conversation_providers.js
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
        ...base, llm_provider: "github_copilot", llm_model: "shared-model",
        llm_reasoning_effort: "low", llm_max_input_tokens: 128000,
        github_token_source: "gh-cli", model_fallbacks: { categories: [] },
        llm_providers: { azure_foundry: {
          endpoint: "https://demo.openai.azure.com", deployment: "shared-model",
        } },
        llm_providers_present: { azure_foundry: { key: true } },
      };
      const writes = [];
      let azureDelay = 0;
      let azureUnavailable = false;
      await page.route("**/api/settings", async (route) => {
        if (route.request().method() === "PUT") {
          const payload = route.request().postDataJSON();
          writes.push(payload);
          if (payload.llm_providers) {
            for (const [id, config] of Object.entries(payload.llm_providers)) {
              state.llm_providers[id] = { ...state.llm_providers[id], ...config };
            }
          }
          Object.assign(state, { ...payload, llm_providers: state.llm_providers });
        }
        await route.fulfill({ json: state });
      });
      await page.route("**/api/llm/models*", async (route) => {
        const provider = new URL(route.request().url()).searchParams.get("provider") || state.llm_provider;
        if (provider === "azure_foundry" && azureDelay) await new Promise((resolve) => setTimeout(resolve, azureDelay));
        if (provider === "azure_foundry" && (azureUnavailable || !state.llm_providers_present.azure_foundry.key)) {
          await route.fulfill({ status: azureUnavailable ? 502 : 422, json: { detail: azureUnavailable ? "Foundry catalogue is unavailable." : "Azure AI Foundry is not configured." } });
          return;
        }
        if (!["github_copilot", "azure_foundry"].includes(provider)) {
          await route.fulfill({ json: [] });
          return;
        }
        const prefix = provider === "azure_foundry" ? "Foundry" : "Copilot";
        await route.fulfill({ json: [{
          id: "shared-model", name: `${prefix} shared`, publisher: prefix,
          catalog_provider: provider, context_window: 128000,
          supported_reasoning_efforts: ["low", "high"],
        }] });
      });
      const chat = await (await page.request.post(`${BASE}/api/chats`, { data: { title: "Provider browser fixture" } })).json();
      const other = await (await page.request.post(`${BASE}/api/chats`, { data: { title: "Other provider fixture" } })).json();
      const topic = await (await page.request.post(`${BASE}/api/topics`, { data: { title: "Provider topic fixture" } })).json();
      try {
        await page.goto(`${BASE}/chats/${chat.slug}`, { waitUntil: "networkidle" });
        const modelButton = page.getByRole("button", { name: "Model", exact: true });
        assert.equal(await page.getByRole("button", { name: "Conversation provider", exact: true }).count(), 0);
        const settings = page.locator('[data-tooltip="Settings"]').first();
        const box = await settings.boundingBox();
        if (box && box.x < 0) await page.getByRole("button", { name: "Open navigation", exact: true }).click();
        await settings.click();
        await page.getByRole("dialog", { name: "Settings", exact: true }).getByRole("button", { name: "Model", exact: true }).click();
        await page.getByRole("button", { name: "LLM provider", exact: true }).click();
        assert.match(await page.getByRole("option", { name: /^GitHub Copilot/ }).textContent(), /✓ Configured/);
        assert.match(await page.getByRole("option", { name: /^Azure AI Foundry/ }).textContent(), /✓ Configured/);
        assert.match(await page.getByRole("option", { name: /^OpenAI/ }).textContent(), /○ Not configured/);
        assert.match(await page.getByRole("option", { name: /^Ollama/ }).textContent(), /○ Not configured/);
        const contrasts = await page.locator('[role="option"] [data-provider-configuration]').evaluateAll((badges) => {
          const canvas = document.createElement("canvas");
          canvas.width = canvas.height = 1;
          const context = canvas.getContext("2d");
          const rgba = (color) => {
            context.clearRect(0, 0, 1, 1);
            context.fillStyle = color;
            context.fillRect(0, 0, 1, 1);
            return Array.from(context.getImageData(0, 0, 1, 1).data);
          };
          const luminance = (rgb) => rgb.slice(0, 3).reduce((sum, value, i) => {
            const channel = value / 255;
            return sum + (channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4) * [0.2126, 0.7152, 0.0722][i];
          }, 0);
          return badges.map((badge) => {
            const ancestors = [];
            for (let node = badge; node; node = node.parentElement) ancestors.push(node);
            let background = [255, 255, 255];
            for (const node of ancestors.reverse()) {
              const color = rgba(getComputedStyle(node).backgroundColor);
              background = background.map((channel, i) => color[i] * color[3] / 255 + channel * (1 - color[3] / 255));
            }
            const foreground = luminance(rgba(getComputedStyle(badge).color));
            const surface = luminance(background);
            return (Math.max(foreground, surface) + 0.05) / (Math.min(foreground, surface) + 0.05);
          });
        });
        assert.ok(contrasts.every((ratio) => ratio >= 4.5), `Badge text passes WCAG AA (${colorScheme}): ${contrasts}`);
        await page.getByRole("option", { name: /^Azure AI Foundry/ }).click();
        await page.getByRole("button", { name: "Close", exact: true }).click();
        if (width < 768) await page.getByRole("button", { name: "Close navigation", exact: true }).click();
        await modelButton.click();
        assert.equal(await page.getByRole("group", { name: /^OpenAI -/ }).count(), 0, "Unconfigured providers are excluded");
        assert.equal(await page.getByRole("group", { name: /^Ollama/ }).count(), 0, "An unsaved default URL does not configure Ollama");
        await page.getByRole("option", { name: "Copilot shared", exact: true }).waitFor();
        await page.getByRole("option", { name: "Foundry shared", exact: true }).click();
        await page.waitForFunction(async (id) => (await (await fetch(`/api/chats/${id}`)).json()).llm_selection?.provider === "azure_foundry", chat.id);
        await page.getByRole("button", { name: "Reasoning effort", exact: true }).click();
        await page.getByRole("option", { name: "High", exact: true }).click();
        await page.waitForFunction(async (id) => (await (await fetch(`/api/chats/${id}`)).json()).llm_selection?.reasoning_effort === "high", chat.id);
        await page.getByRole("button", { name: "Context size", exact: true }).click();
        await page.getByRole("option", { name: "32K", exact: true }).click();
        await page.waitForFunction(async (id) => (await (await fetch(`/api/chats/${id}`)).json()).llm_selection?.context_tokens === 32000, chat.id);
        assert.equal(writes.length, 0, "Conversation controls never write global settings");
        assert.equal((await (await page.request.get(`${BASE}/api/chats/${other.id}`)).json()).llm_selection, null);
        await page.reload({ waitUntil: "networkidle" });
        assert.match(await modelButton.getAttribute("data-tooltip"), /Azure AI Foundry/);
        assert.match(await modelButton.textContent(), /Foundry shared/);

        // Same ids from different providers remain distinct even after delayed loads.
        azureDelay = 200;
        await page.reload({ waitUntil: "networkidle" });
        await modelButton.click();
        await page.getByRole("option", { name: "Copilot shared", exact: true }).waitFor();
        assert.equal(await page.getByRole("option", { name: "Foundry shared", exact: true, selected: true }).count(), 1);
        assert.equal(await page.getByRole("option", { selected: true }).count(), 1);
        await page.getByRole("option", { name: "Copilot shared", exact: true }).click();
        await page.waitForFunction(async (id) => (await (await fetch(`/api/chats/${id}`)).json()).llm_selection?.provider === "github_copilot", chat.id);
        assert.match(await modelButton.getAttribute("data-tooltip"), /GitHub Copilot/);
        await modelButton.click();
        await page.getByRole("option", { name: "Use global default", exact: true }).click();
        await page.waitForFunction(async (id) => (await (await fetch(`/api/chats/${id}`)).json()).llm_selection === null, chat.id);
        assert.match(await modelButton.textContent(), /Default/);

        // Topic controls use the same contract and support manual deployment names.
        await page.goto(`${BASE}/t/${topic.public_id}`, { waitUntil: "networkidle" });
        await modelButton.click();
        await page.getByPlaceholder("Filter models…").fill("manual-deployment");
        await page.getByRole("option", { name: 'Use model id "manual-deployment" Azure AI Foundry', exact: true }).click();
        await page.waitForFunction(async (id) => (await (await fetch(`/api/topics/${id}`)).json()).llm_selection?.model === "manual-deployment", topic.id);
        assert.equal(writes.length, 0);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);

        // Saving provider config must not change the global default.
        const settingsBox = await settings.boundingBox();
        if (settingsBox && settingsBox.x < 0) await page.getByRole("button", { name: "Open navigation", exact: true }).click();
        await settings.click();
        await page.getByRole("dialog", { name: "Settings", exact: true }).getByRole("button", { name: "Model", exact: true }).click();
        await page.getByRole("button", { name: "LLM provider", exact: true }).click();
        await page.getByRole("option", { name: /^Azure AI Foundry/ }).click();
        await page.getByRole("button", { name: "Apply & refresh models", exact: true }).click();
        await page.getByText("1 models", { exact: true }).waitFor();
        assert.equal("llm_provider" in writes.at(-1), false);
        assert.equal(state.llm_provider, "github_copilot");
        await page.getByRole("button", { name: "Global default provider", exact: true }).click();
        assert.match(await page.getByRole("option", { name: /^Azure AI Foundry/ }).textContent(), /✓ Configured/);
        await page.keyboard.press("Escape");
        await page.getByRole("button", { name: "Close", exact: true }).click();
        if (width < 768) await page.getByRole("button", { name: "Close navigation", exact: true }).click();
        azureUnavailable = true;
        await page.reload({ waitUntil: "networkidle" });
        await modelButton.click();
        assert.equal(await page.getByRole("group", { name: "Azure AI Foundry", exact: true }).getByRole("option", { name: /^Models unavailable/ }).isDisabled(), true);
        assert.equal(await page.getByRole("option", { name: "Copilot shared", exact: true }).isEnabled(), true);
        await page.keyboard.press("Escape");
        azureUnavailable = false;
        state.llm_providers_present.azure_foundry.key = false;
        await page.reload({ waitUntil: "networkidle" });
        await modelButton.click();
        assert.equal(await page.getByRole("group", { name: /^Azure AI Foundry/ }).count(), 0);
        await page.keyboard.press("Escape");
        await page.getByRole("alert").filter({ hasText: "Azure AI Foundry is not configured" }).waitFor();
        await page.route(`**/api/topics/${topic.id}/messages/stream`, (route) => route.fulfill({
          status: 422, json: { detail: "Selected provider credentials are missing." },
        }));
        await page.locator("textarea").fill("Check provider error handling");
        await page.getByRole("button", { name: "Send", exact: true }).click();
        await page.getByRole("alert").filter({ hasText: "Selected provider credentials are missing." }).waitFor();
        await page.waitForLoadState("networkidle");
        assert.equal(await page.getByRole("alert").filter({ hasText: "Selected provider credentials are missing." }).count(), 1);

        // The placeholder alone is not configuration; saving the URL opts Ollama in.
        if (width < 768) await page.getByRole("button", { name: "Open navigation", exact: true }).click();
        await settings.click();
        await page.getByRole("dialog", { name: "Settings", exact: true }).getByRole("button", { name: "Model", exact: true }).click();
        await page.getByRole("button", { name: "LLM provider", exact: true }).click();
        await page.getByRole("option", { name: /^Ollama/ }).click();
        await page.getByPlaceholder("http://localhost:11434/v1", { exact: true }).fill("http://localhost:11434/v1");
        await page.getByRole("button", { name: "Apply & refresh models", exact: true }).click();
        await page.waitForFunction(() => document.querySelector('[data-provider-configuration="configured"]'));
        assert.equal(state.llm_providers.ollama.base_url, "http://localhost:11434/v1");
        assert.equal("llm_provider" in writes.at(-1), false);
        await page.getByRole("button", { name: "LLM provider", exact: true }).click();
        assert.match(await page.getByRole("option", { name: /^Ollama/ }).textContent(), /✓ Configured/);
        await page.keyboard.press("Escape");
        await page.getByRole("button", { name: "Close", exact: true }).click();
        if (width < 768) await page.getByRole("button", { name: "Close navigation", exact: true }).click();
        await modelButton.click();
        assert.equal(await page.getByRole("group", { name: "Ollama (local)", exact: true }).count(), 1);
      } finally {
        await page.request.delete(`${BASE}/api/chats/${chat.id}`);
        await page.request.delete(`${BASE}/api/chats/${other.id}`);
        await page.request.delete(`${BASE}/api/topics/${topic.id}`);
        await page.close();
      }
    }
    console.log("Unified model picker: configured-only catalogues, provider/model isolation, reload/reset, failures, manual models and mobile passed.");
  } finally {
    await browser.close();
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
