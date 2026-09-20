// Browser-only fixtures: never enable or start the real agent runtime for a demo.
const PORT = process.env.DEMO_PORT || "8899";
const BASE = process.env.DEMO_BASE || `http://127.0.0.1:${PORT}`;

async function prepareDemoPage(page) {
  const url = new URL(BASE);
  if (!["127.0.0.1", "localhost"].includes(url.hostname) || url.port !== PORT || PORT === "9000") {
    throw new Error(`Use the isolated seeded demo on localhost:${PORT}, not a live instance.`);
  }
  const response = await page.request.get(`${BASE}/api/me`);
  if (!response.ok()) throw new Error(`Demo unavailable: ${response.status()}`);
  const me = await response.json();
  if (me.github || me.github_token_source !== "none") {
    throw new Error("The screenshot demo must be Guest / Not connected.");
  }
  await page.route("**/api/settings", async (route) => {
    const response = await route.fetch();
    const settings = await response.json();
    await route.fulfill({
      response,
      json: {
        ...settings,
        agents_enabled: true,
        agents_available: true,
        agents_unavailable_reason: null,
        stt_azure_ready: true,
      },
    });
  });
}

module.exports = { BASE, prepareDemoPage };
