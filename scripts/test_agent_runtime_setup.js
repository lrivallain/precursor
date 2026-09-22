const assert = require("node:assert/strict");
const { BASE, prepareDemoPage, mockAgentRuntime } = require("./demo_browser");

async function testAgentRuntimeSetup(browser) {
  async function scenario(name, run, overrides = {}) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const page = await context.newPage();
    page.setDefaultTimeout(10000);
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    try {
      await prepareDemoPage(page);
      const state = await mockAgentRuntime(page, overrides);
      await run(page, state);
      assert.deepEqual(errors, [], name);
      assert.deepEqual(state.settingsWrites, [], "Installing must not change the saved preference");
      console.log(`Runtime setup: ${name}.`);
    } finally {
      await context.close();
    }
  }

  const card = (page) => page.locator("main").getByRole("region", { name: "Copilot runtime", exact: true });
  const install = (page) => card(page).getByRole("button", { name: "Install the Copilot CLI (~90 MB)", exact: true });
  const settingsPanel = (page) => page.getByRole("heading", { name: "Agents mode", exact: true }).locator("..");
  const confirmInstall = async (page) => {
    await install(page).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Download", exact: true }).click();
  };
  const downloading = (page) => card(page).getByRole("status").filter({ hasText: "Downloading" }).waitFor();
  const finish = (state, started = true) => {
    Object.assign(state.runtime, {
      available: true,
      unavailable_reason: null,
      runtime_started: started,
      cli_path: "/demo/copilot",
      job: {
        ...state.runtime.job,
        state: "succeeded", runtime_started: started,
        detail: "Copilot CLI installed.", finished_at: 10,
      },
    });
  };

  for (const enabled of [null, true, false]) {
    await scenario(`missing CLI with preference ${enabled}`, async (page, state) => {
      state.enabled = enabled;
      await page.goto(`${BASE}/agents`);
      await install(page).waitFor();
      assert.equal(await page.getByPlaceholder("e.g. Investigate the flaky CI test and propose a fix…").count(), 0);
      await page.getByRole("navigation", { name: "Sections", exact: true })
        .getByRole("button", { name: "Agents", exact: true }).waitFor();
      await page.keyboard.press("ControlOrMeta+k");
      await page.getByRole("dialog").getByRole("button", { name: /Agents Autonomous coding agents/ }).click();
      await install(page).click();
      await page.getByRole("alertdialog").getByRole("button", { name: "Cancel", exact: true }).click();
      assert.equal(state.installCalls, 0, "Cancellation and navigation are read-only");
      await confirmInstall(page);
      await downloading(page);
      assert.equal(await install(page).isDisabled(), true);
      assert.equal(state.installCalls, 1);
      // The backend job outlives the view; returning must pick up its progress.
      await page.goto(`${BASE}/topics`);
      await page.goto(`${BASE}/agents`);
      await downloading(page);
      finish(state, enabled !== false);
      if (enabled === false) {
        await page.getByText("Agents mode is off", { exact: true }).waitFor();
        assert.equal(await page.getByRole("button", { name: "Restart now" }).count(), 0);
        await page.locator("main").getByRole("button", { name: "Open Settings", exact: true }).click();
        await page.getByRole("checkbox", { name: /^Enable Agents mode/ }).waitFor();
        assert.equal(await page.getByRole("checkbox", { name: /^Enable Agents mode/ }).isChecked(), false);
        assert.equal(await settingsPanel(page).getByRole("region", { name: "Copilot runtime" }).count(), 0);
      } else {
        await page.getByRole("heading", { name: "Agent fleet", exact: true }).waitFor();
      }
      assert.equal(state.installCalls, 1);
    });
  }

  await scenario("Home, Settings and mobile share setup", async (page, state) => {
    await page.goto(`${BASE}/`);
    await page.locator("main").getByRole("button", { name: "New agent", exact: true }).click();
    await install(page).waitFor();
    await page.locator("main").getByRole("button", { name: "Open Settings", exact: true }).click();
    await settingsPanel(page).getByRole("button", { name: "Install the Copilot CLI (~90 MB)", exact: true }).waitFor();
    assert.equal(await settingsPanel(page).getByRole("checkbox", { name: /^Enable Agents mode/ }).isChecked(), false);
    await settingsPanel(page).getByRole("button", { name: "Install the Copilot CLI (~90 MB)", exact: true }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Download", exact: true }).click();
    await settingsPanel(page).getByRole("status").filter({ hasText: "Downloading" }).waitFor();
    await page.keyboard.press("Escape");
    await settingsPanel(page).waitFor({ state: "detached" });
    await downloading(page);
    finish(state);
    await card(page).waitFor({ state: "detached" });
    await page.getByRole("heading", { name: "Start an agent task", exact: true }).waitFor();
    Object.assign(state.runtime, { available: false, runtime_started: false, job: null });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`${BASE}/agents`);
    await install(page).waitFor();
    assert.equal(await install(page).isEnabled(), true);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.getByRole("button", { name: "Open navigation", exact: true }).click();
    await page.getByRole("navigation", { name: "Sections", exact: true })
      .getByRole("button", { name: "Agents", exact: true }).click();
    await install(page).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Cancel", exact: true }).click();
  });

  await scenario("the preference cannot be turned on without a CLI", async (page, state) => {
    state.enabled = false;
    const toggle = () => settingsPanel(page).getByRole("checkbox", { name: /^Enable Agents mode/ });
    await page.goto(`${BASE}/agents`);
    await page.locator("main").getByRole("button", { name: "Open Settings", exact: true }).click();
    await toggle().waitFor();
    assert.equal(await toggle().isDisabled(), true, "A missing runtime must not be enablable");
    await settingsPanel(page).getByText("Install the Copilot CLI below to turn this on", { exact: false }).waitFor();
    await toggle().click({ force: true });
    await settingsPanel(page).getByRole("button", { name: "Install the Copilot CLI (~90 MB)", exact: true }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Download", exact: true }).click();
    await settingsPanel(page).getByRole("status").filter({ hasText: "Downloading" }).waitFor();
    finish(state);
    await settingsPanel(page).getByRole("region", { name: "Copilot runtime" })
      .waitFor({ state: "detached" });
    assert.equal(await toggle().isDisabled(), false, "A fresh install unlocks the preference");
    assert.equal(await toggle().isChecked(), false, "Installing never flips the preference itself");
  });

  await scenario("a saved preference survives a runtime that went missing", async (page, state) => {
    state.enabled = true;
    const toggle = () => settingsPanel(page).getByRole("checkbox", { name: /^Enable Agents mode/ });
    await page.goto(`${BASE}/agents`);
    await page.locator("main").getByRole("button", { name: "Open Settings", exact: true }).click();
    await toggle().waitFor();
    assert.equal(await toggle().isChecked(), true);
    assert.equal(await toggle().isDisabled(), false, "An outage must not revoke an opted-in user");
    assert.equal(await settingsPanel(page).getByText("Install the Copilot CLI below to turn this on").count(), 0);
  });

  await scenario("runtime loading and status errors are retryable", async (page, state) => {    state.runtimeDelay = 700;
    state.runtimeError = true;
    await page.goto(`${BASE}/agents`);
    await card(page).getByText("Loading runtime status...").waitFor();
    assert.equal(await install(page).count(), 0);
    assert.equal(await page.getByText("Agents mode is off", { exact: true }).count(), 0);
    await card(page).getByRole("alert").filter({ hasText: "Runtime probe failed" }).waitFor();
    state.runtimeDelay = 0;
    state.runtimeError = false;
    await card(page).getByRole("button", { name: "Retry runtime status", exact: true }).click();
    await install(page).waitFor();
    assert.equal(await install(page).isEnabled(), true);
  });

  await scenario("request, job and settings failures retain recovery", async (page, state) => {
    state.installError = true;
    await page.goto(`${BASE}/agents`);
    await confirmInstall(page);
    await card(page).getByRole("alert").filter({ hasText: "Install request failed" }).waitFor();
    state.installError = false;
    await confirmInstall(page);
    await downloading(page);
    Object.assign(state.runtime.job, { state: "failed", detail: "Could not install.", error: "Download denied by proxy." });
    await card(page).getByRole("alert").filter({ hasText: "Download denied by proxy." }).waitFor();
    await confirmInstall(page);
    await downloading(page);
    state.runtimeError = true;
    await card(page).getByRole("alert").filter({ hasText: "Runtime probe failed" }).waitFor();
    state.runtimeError = false;
    state.settingsError = true;
    finish(state);
    await card(page).getByRole("button", { name: "Retry runtime status", exact: true }).click();
    await card(page).getByRole("alert").filter({ hasText: "Could not refresh Agents settings" }).waitFor();
    assert.equal(await page.getByRole("heading", { name: "Agent fleet", exact: true }).count(), 0);
    state.settingsError = false;
    await card(page).getByRole("button", { name: "Retry runtime status", exact: true }).click();
    await page.getByRole("heading", { name: "Agent fleet", exact: true }).waitFor();
    assert.equal(state.installCalls, 3);
  });

  await scenario("initial settings failure is not mistaken for disabled Agents", async (page, state) => {
    state.settingsError = true;
    await page.goto(`${BASE}/agents`);
    await card(page).getByRole("alert").filter({ hasText: "Could not refresh Agents settings" }).waitFor();
    assert.equal(await page.getByText("Agents mode is off", { exact: true }).count(), 0);
    state.settingsError = false;
    await card(page).getByRole("button", { name: "Retry runtime status", exact: true }).click();
    await page.getByRole("heading", { name: "Agent fleet", exact: true }).waitFor();
  }, { available: true, runtime_started: true });

  await scenario("SDK repair guidance does not advertise installation", async (page, state) => {
    await page.goto(`${BASE}/agents`);
    await card(page).getByText("Reinstall Precursor: SDK missing.", { exact: true }).waitFor();
    assert.equal(await install(page).count(), 0);
    assert.equal(state.installCalls, 0);
  }, { sdk_installed: false, can_install_cli: false, unavailable_reason: "Reinstall Precursor: SDK missing." });

  await scenario("blocked installation offers manual guidance", async (page) => {
    await page.goto(`${BASE}/agents`);
    await card(page).getByText("COPILOT_CLI_PATH", { exact: true }).waitFor();
    assert.equal(await install(page).count(), 0);
  }, { can_install_cli: false, install_blocked_reason: "Download unsupported." });

  await scenario("startup failure after install offers confirmed restart", async (page, state) => {
    state.enabled = true;
    await page.goto(`${BASE}/agents`);
    await confirmInstall(page);
    await downloading(page);
    finish(state, false);
    const restart = card(page).getByRole("button", { name: "Restart now", exact: true });
    await restart.waitFor();
    assert.equal(await install(page).count(), 0);
    await restart.click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Cancel", exact: true }).click();
    assert.equal(state.restartCalls, 0);
    state.restartError = true;
    await restart.click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Restart", exact: true }).click();
    await card(page).getByRole("alert").filter({ hasText: "Demo restart rejected" }).waitFor();
    assert.equal(state.restartCalls, 1);
  });

  await scenario("unsupervised startup failure offers manual restart", async (page, state) => {
    state.enabled = true;
    await page.goto(`${BASE}/agents`);
    await card(page).getByText("precursor service restart", { exact: true }).waitFor();
    assert.equal(await install(page).count(), 0);
    assert.equal(await page.getByRole("button", { name: "Restart now" }).count(), 0);
  }, { available: true, can_restart: false, restart_blocked_reason: "Not supervised." });

  await scenario("selected history survives missing-runtime recovery", async (page, state) => {
    state.enabled = true;
    const agents = await (await page.request.get(`${BASE}/api/agents`)).json();
    const agent = agents.find((item) => item.title === "Digest writer");
    await page.goto(`${BASE}/agents/${agent.public_id}`);
    await page.getByText("The draft is ready for review before publishing.").waitFor();
    assert.equal(await page.getByRole("button", { name: "Run agent", exact: true }).isDisabled(), true);
    await confirmInstall(page);
    await downloading(page);
    finish(state);
    await card(page).waitFor({ state: "detached" });
    await page.getByText("The draft is ready for review before publishing.").waitFor();
    assert.equal(page.url(), `${BASE}/agents/${agent.public_id}`);
  });
}

module.exports = { testAgentRuntimeSetup };

if (require.main === module) {
  const { chromium } = require("playwright");
  (async () => {
    const browser = await chromium.launch();
    try {
      await testAgentRuntimeSetup(browser);
    } finally {
      await browser.close();
    }
  })().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}
