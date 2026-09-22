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
        agents_runtime_started: true,
        agents_unavailable_reason: null,
        stt_azure_ready: true,
      },
    });
  });
}

// Mutations are intercepted too: UI tests and screenshots must never provision
// a CLI or restart the demo, even if someone accidentally clicks an action.
async function mockAgentRuntime(page, overrides = {}) {
  const settings = await (await page.request.get(`${BASE}/api/settings`)).json();
  const state = {
    enabled: null,
    installCalls: 0,
    restartCalls: 0,
    settingsWrites: [],
    runtimeError: false,
    settingsError: false,
    installError: false,
    restartError: false,
    runtimeDelay: 0,
    runtime: {
      available: false,
      unavailable_reason: "Copilot CLI runtime not installed yet.",
      runtime_started: false,
      sdk_installed: true,
      cli_path: null,
      can_install_cli: true,
      install_blocked_reason: null,
      can_restart: true,
      restart_blocked_reason: null,
      has_archived_events: false,
      job: null,
      ...overrides,
    },
  };
  await page.route("**/api/settings", async (route) => {
    if (route.request().method() === "PUT") {
      const update = route.request().postDataJSON();
      state.settingsWrites.push(update);
      if ("agents_enabled" in update) state.enabled = update.agents_enabled;
    }
    await route.fulfill(state.settingsError
      ? { status: 503, json: { detail: "Settings refresh failed" } }
      : { json: {
        ...settings,
        agents_enabled: state.enabled ?? state.runtime.available,
        agents_available: state.runtime.available,
        agents_runtime_started: state.runtime.runtime_started,
        agents_unavailable_reason: state.runtime.unavailable_reason,
      } });
  });
  await page.route("**/api/agents/runtime**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/cli")) {
      state.installCalls++;
      if (state.installError) {
        await route.fulfill({ status: 503, json: { detail: "Install request failed" } });
        return;
      }
      state.runtime.job = {
        state: "running", detail: "Downloading the Copilot CLI...", error: null,
        cli_path: null, runtime_started: false, started_at: state.installCalls, finished_at: null,
      };
    } else if (path.endsWith("/restart")) {
      state.restartCalls++;
      await route.fulfill(state.restartError
        ? { status: 409, json: { detail: "Demo restart rejected" } }
        : { status: 202, body: "" });
      return;
    } else {
      if (state.runtimeDelay) await new Promise((resolve) => setTimeout(resolve, state.runtimeDelay));
      if (state.runtimeError) {
        await route.fulfill({ status: 503, json: { detail: "Runtime probe failed" } });
        return;
      }
    }
    await route.fulfill({ json: state.runtime });
  });
  return state;
}

module.exports = { BASE, prepareDemoPage, mockAgentRuntime };
