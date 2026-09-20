// Run against scripts/demo_server.sh after seeding and building the SPA:
// NODE_PATH=.demo/node_modules node scripts/test_section_navigation.js
// Uses the same Playwright installation as capture_screenshots.js.
const assert = require("node:assert/strict");
const { chromium } = require("playwright");
const { BASE, prepareDemoPage } = require("./demo_browser");

async function run() {
  const browser = await chromium.launch();
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await prepareDemoPage(page);
    const agents = await (await page.request.get(`${BASE}/api/agents`)).json();
    const workflows = await (await page.request.get(`${BASE}/api/workflows`)).json();
    assert.ok(agents.length >= 2 && workflows.length >= 2, "Seed the demo first.");
    const agent = agents.find((item) => item.workflow_count > 0);
    const standalone = agents.filter((item) => item.workflow_count === 0);
    assert.ok(agent && standalone.length > 0, "Seed both standalone and workflow agents.");
    const workflow = workflows[0];
    const sections = page.getByRole("navigation", { name: "Sections", exact: true });
    const agentList = page.getByRole("navigation", { name: "Agents list", exact: true });
    const workflowList = page.getByRole("navigation", { name: "Workflows list", exact: true });
    const overview = (list) => list.getByRole("button", { name: "Overview", exact: true });
    const row = (list, name) => list.getByRole("button").filter({ hasText: name });
    const current = async (list, name) => {
      await list.locator('button[aria-current="page"]').filter({ hasText: name }).waitFor();
    };
    const enter = async (name) => {
      await sections.getByRole("button", { name, exact: true }).click();
      await page.waitForURL(`${BASE}/${name.toLowerCase()}`);
    };

    await page.goto(`${BASE}/topics`);
    await sections.getByRole("button", { name: "Agents", exact: true }).waitFor();
    const railBefore = await sections.getByRole("button", { name: "Agents", exact: true }).boundingBox();
    await enter("Agents");
    await page.getByRole("heading", { name: "Agent fleet" }).waitFor();
    await current(agentList, "Overview");
    assert.deepEqual(await sections.getByRole("button", { name: "Agents", exact: true }).boundingBox(), railBefore);
    const main = page.locator("main");
    const membership = (container, name) => container.getByRole("region", { name, exact: true });
    const workflowToggle = (container) =>
      container.getByRole("checkbox", { name: "Show workflow agents", exact: true });
    for (const container of [agentList, main]) {
      await membership(container, "Workflow agents").waitFor();
      assert.equal(await membership(container, "Standalone agents").locator("li").count(), standalone.length);
      assert.equal(await membership(container, "Workflow agents").locator("li").count(), agents.length - standalone.length);
      assert.equal(await container.locator("li").count(), agents.length, "No duplicate shared agents");
    }
    await workflowToggle(agentList).uncheck();
    await membership(main, "Workflow agents").waitFor({ state: "detached" });
    assert.equal(await workflowToggle(main).isChecked(), false);
    assert.equal(await agentList.locator("li").count(), standalone.length);
    await main.getByRole("button", { name: new RegExp(`^${standalone.length}\\s*Idle · done$`) }).waitFor();
    await agentList.getByRole("searchbox").fill(agent.title);
    await agentList.getByText("No matching agents.").waitFor();
    await agentList.getByRole("button", { name: "Clear search" }).click();
    assert.equal(await workflowToggle(agentList).isChecked(), false, "Clearing search preserves membership filter");
    await row(agentList, standalone[0].title).click();
    await overview(agentList).click();
    assert.equal(await workflowToggle(main).isChecked(), false, "Choice survives opening an agent");
    await main.getByRole("textbox", { name: "Filter agents by name" }).fill(agent.title);
    await main.getByText("No agents match", { exact: false }).waitFor();
    await main.getByRole("button", { name: "Clear filters", exact: true }).click();
    await membership(main, "Workflow agents").waitFor();
    assert.equal(await workflowToggle(agentList).isChecked(), true);
    await row(agentList, agent.title).click();
    await workflowToggle(agentList).uncheck();
    await page.waitForURL(`${BASE}/agents/${agent.public_id}`);
    await page.getByRole("button", { name: "Run agent", exact: true }).waitFor();
    assert.equal(await row(agentList, agent.title).count(), 0, "Filtering does not close an open workflow agent");
    await overview(agentList).click();
    await workflowToggle(main).check();
    await membership(agentList, "Workflow agents").waitFor();
    console.log("Workflow agents: separate groups, shared visibility, search, counts and stable selection.");
    await agentList.getByRole("searchbox").fill("NO SUCH AGENT");
    await agentList.getByText("No matching agents.").waitFor();
    await agentList.getByRole("button", { name: "Clear search" }).click();
    await row(agentList, agent.title).click();
    await page.waitForURL(`${BASE}/agents/${agent.public_id}`);
    await current(agentList, agent.title);
    const runButton = page.getByRole("button", { name: "Run agent", exact: true });
    assert.equal(await runButton.isEnabled(), true);
    const runningAgents = agents.map((item) =>
      item.id === agent.id ? { ...item, status: "running" } : item);
    let started = false;
    await page.route(`**/api/agents/${agent.id}/start`, (route) => {
      started = true;
      return route.fulfill({ json: runningAgents.find((item) => item.id === agent.id) });
    });
    await page.route("**/api/agents", (route) =>
      route.fulfill({ json: started ? runningAgents : agents }));
    await runButton.click();
    await row(agentList, agent.title).filter({ hasText: "Running" }).waitFor();
    assert.equal(await runButton.isDisabled(), true);
    await overview(agentList).click();
    await page.waitForURL(`${BASE}/agents`);
    await page.locator("main").getByRole("button").filter({ hasText: agent.title }).filter({ hasText: "Running" }).first().waitFor();
    await page.unroute(`**/api/agents/${agent.id}/start`);
    await page.unroute("**/api/agents");
    await page.goBack();
    await page.waitForURL(`${BASE}/agents/${agent.public_id}`);
    await current(agentList, agent.title);
    await page.goForward();
    await current(agentList, "Overview");
    await page.getByRole("complementary", { name: "Agents sidebar" }).getByRole("button", { name: "New agent", exact: true }).click();
    assert.equal(await agentList.locator('[aria-current="page"]').count(), 0);
    await enter("Agents");
    await current(agentList, "Overview");
    await page.getByRole("heading", { name: "Agent fleet" }).waitFor();
    console.log("Agents: overview, search, selection, history and explicit creation.");

    await page.getByRole("button", { name: "Collapse sidebar", exact: true }).click();
    assert.deepEqual(await sections.getByRole("button", { name: "Agents", exact: true }).boundingBox(), railBefore);
    await enter("Workflows");
    await page.getByRole("button", { name: "Expand sidebar", exact: true }).click();
    await current(workflowList, "Overview");
    await workflowList.getByRole("searchbox").fill(workflow.name.toUpperCase());
    assert.equal(await workflowList.locator("li").count(), 1);
    await workflowList.getByRole("searchbox").fill("");
    await row(workflowList, workflow.name).click();
    await page.waitForURL(new RegExp(`/workflows/${workflow.id}(?:/run/[^/]+)?$`));
    await current(workflowList, workflow.name);
    await overview(workflowList).click();
    await page.waitForURL(`${BASE}/workflows`);
    await page.goBack();
    await current(workflowList, workflow.name);
    await page.goForward();
    await current(workflowList, "Overview");
    await page.getByRole("complementary", { name: "Workflows sidebar" }).getByRole("button", { name: "New workflow", exact: true }).click();
    await page.getByRole("heading", { name: "New workflow", exact: true }).waitFor();
    assert.equal(await workflowList.locator('[aria-current="page"]').count(), 0);
    await overview(workflowList).click();
    await page.getByRole("heading", { name: "Workflows", exact: true }).waitFor();
    await row(workflowList, workflow.name).click();
    await enter("Agents");
    await enter("Workflows");
    await current(workflowList, "Overview");
    await page.getByRole("heading", { name: "Workflows", exact: true }).waitFor();
    console.log("Workflows: overview, search, selection, history, collapse and editor reset.");

    await page.goto(`${BASE}/workflows/1/run/latest`);
    await current(workflowList, "Weekly release digest");
    await page.waitForURL(`${BASE}/workflows/1/run/latest`);
    await page.goto(`${BASE}/agents/${agent.public_id}`);
    await current(agentList, agent.title);
    await page.getByRole("button", { name: "Use tab navigation", exact: true }).click();
    await page.getByRole("button", { name: "Workflows", exact: true }).click();
    await current(workflowList, "Overview");
    await page.getByRole("button", { name: "Use rail navigation", exact: true }).click();
    console.log("Deep links and the tab-navigation preference are preserved.");

    // Local responses exercise cross-lane filters and an all-workflow empty state.
    const groupedAgents = agents.map((item) => ({
      ...item,
      status: item.workflow_count > 0 ? "needs_approval" : "blocked",
      schedule: { enabled: true },
    }));
    await page.route("**/api/agents", (route) => route.fulfill({ json: groupedAgents }));
    await page.route("**/api/agents/inbox", (route) => route.fulfill({
      json: [
        { kind: "blocked", agent_id: standalone[0].id, title: standalone[0].title, detail: "Standalone question" },
        { kind: "needs_approval", agent_id: agent.id, title: agent.title, detail: "Workflow permission" },
      ],
    }));
    await page.goto(`${BASE}/agents`);
    await main.getByRole("button", { name: `Approve ${agent.title}`, exact: true }).waitFor();
    await workflowToggle(main).uncheck();
    await main.getByRole("button", { name: `Approve ${agent.title}`, exact: true }).waitFor({ state: "detached" });
    await main.getByRole("button", { name: `Answer ${standalone[0].title}`, exact: true }).waitFor();
    await main.getByRole("button", { name: new RegExp(`^${standalone.length}\\s*Need you$`) }).click();
    assert.equal(await membership(main, "Standalone agents").locator("li").count(), standalone.length);
    await main.getByRole("button", { name: new RegExp(`^${standalone.length}\\s*Scheduled$`) }).click();
    await main.getByRole("textbox", { name: "Filter agents by name" }).fill(standalone[0].title.toUpperCase());
    assert.equal(await membership(main, "Standalone agents").locator("li").count(), 1);
    await main.getByRole("textbox", { name: "Filter agents by name" }).fill("");
    await workflowToggle(main).check();
    await membership(main, "Workflow agents").waitFor();
    assert.equal(await membership(main, "Workflow agents").locator("li").count(), agents.length - standalone.length);
    await page.unroute("**/api/agents/inbox");
    await page.unroute("**/api/agents");
    await page.route("**/api/agents", (route) => route.fulfill({
      json: agents.map((item) => ({ ...item, workflow_count: 2 })),
    }));
    await page.goto(`${BASE}/agents`);
    await membership(agentList, "Workflow agents").waitFor();
    await workflowToggle(agentList).uncheck();
    await agentList.getByText("No standalone agents.", { exact: false }).waitFor();
    await main.getByText("No agents match", { exact: false }).waitFor();
    await main.getByRole("button", { name: "Clear filters", exact: true }).click();
    assert.equal(await membership(main, "Workflow agents").locator("li").count(), agents.length);
    await page.unroute("**/api/agents");
    await page.route("**/api/agents", (route) => route.fulfill({
      json: agents.map((item) => ({ ...item, workflow_count: 0 })),
    }));
    await page.goto(`${BASE}/agents`);
    await membership(main, "Standalone agents").waitFor();
    assert.equal(await membership(main, "Standalone agents").locator("li").count(), agents.length);
    assert.equal(await membership(main, "Workflow agents").count(), 0);
    await page.unroute("**/api/agents");
    console.log("Membership filters compose with urgency/schedule/name, scope inbox, and handle all/none workflow fleets.");

    // Keep the data responses local: exercise running progress without executing a pipeline.
    let polls = 0;
    await page.route("**/api/workflows", async (route) => {
      polls += 1;
      if (polls > 1) await new Promise((resolve) => setTimeout(resolve, 2200));
      const running = structuredClone(workflows);
      running[0].status = "running";
      running[0].run_progress = {
        status: "running", total: 4, done: polls > 1 ? 2 : 1, current_position: null,
      };
      await route.fulfill({ json: running });
    });
    await page.goto(`${BASE}/workflows`);
    await row(workflowList, workflow.name).filter({ hasText: "25%" }).waitFor();
    await row(workflowList, workflow.name).click();
    await row(workflowList, workflow.name).filter({ hasText: "50%" }).waitFor();
    await overview(workflowList).click();
    await page.locator("main").getByRole("progressbar", { name: `${workflow.name} progress` }).waitFor();
    assert.equal(await page.locator("main").getByRole("progressbar", { name: `${workflow.name} progress` }).getAttribute("aria-valuenow"), "50");
    await page.unroute("**/api/workflows");
    console.log("Workflow list and cards share live progress, including while a detail is open.");

    // Retry and empty states must not render the topic tree or a creation form.
    let fail = true;
    await page.route("**/api/workflows", (route) => route.fulfill(
      fail ? { status: 503, json: { detail: "Demo unavailable" } } : { json: [] },
    ));
    await page.goto(`${BASE}/workflows`);
    await workflowList.getByRole("alert").waitFor();
    fail = false;
    await workflowList.getByRole("button", { name: "Retry", exact: true }).click();
    await page.getByRole("heading", { name: "No workflows yet", exact: true }).waitFor();
    await current(workflowList, "Overview");
    await page.unroute("**/api/workflows");
    await page.route("**/api/agents", (route) => route.fulfill({ json: [] }));
    await page.goto(`${BASE}/agents`);
    await page.getByRole("heading", { name: "Agent fleet" }).waitFor();
    await current(agentList, "Overview");
    await agentList.getByText("No agents yet.", { exact: false }).waitFor();
    await page.unroute("**/api/agents");
    console.log("Errors are retryable and empty sections keep their overview.");

    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`${BASE}/agents`);
    await page.getByRole("heading", { name: "Agent fleet" }).waitFor();
    await page.getByRole("button", { name: "Open navigation", exact: true }).click();
    await workflowToggle(agentList).uncheck();
    await membership(agentList, "Workflow agents").waitFor({ state: "detached" });
    await workflowToggle(agentList).check();
    await membership(agentList, "Workflow agents").waitFor();
    await row(agentList, agent.title).click();
    await page.waitForURL(`${BASE}/agents/${agent.public_id}`);
    assert.equal(await page.locator("[inert]").count(), 1);
    await page.getByRole("button", { name: "Open navigation", exact: true }).click();
    await enter("Workflows");
    await page.getByRole("button", { name: "Open navigation", exact: true }).click();
    await row(workflowList, workflow.name).click();
    await current(workflowList, workflow.name);
    assert.equal(await page.locator("[inert]").count(), 1);
    await page.getByRole("button", { name: "Open navigation", exact: true }).click();
    await overview(workflowList).click();
    assert.equal(await page.locator("[inert]").count(), 1);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    console.log("Mobile list/overview selections close the drawer without page overflow.");
    assert.deepEqual(errors, [], "Browser runtime errors");
    await context.close();
  } finally {
    await browser.close();
  }
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
