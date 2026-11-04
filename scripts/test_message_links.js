// Run against the anonymous seeded demo after building the SPA:
// DEMO_PORT=8899 NODE_PATH=.demo/node_modules node scripts/test_message_links.js
const assert = require("node:assert/strict");
const { randomUUID } = require("node:crypto");
const { chromium } = require("playwright");
const { BASE, prepareDemoPage } = require("./demo_browser");

async function focused(page, id) {
  const message = page.locator(`[data-message-link="${id}"]`);
  await message.waitFor();
  await page.waitForFunction((target) => {
    const element = document.querySelector(`[data-message-link="${target}"]`);
    if (!element || document.activeElement !== element) return false;
    const rect = element.getBoundingClientRect();
    return rect.top >= 0 && rect.bottom <= innerHeight;
  }, id);
  return message;
}

async function run() {
  const browser = await chromium.launch();
  try {
    const context = await browser.newContext({
      viewport: { width: 1440, height: 1000 },
      permissions: ["clipboard-read", "clipboard-write"],
    });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await prepareDemoPage(page);
    const topics = await (await page.request.get(`${BASE}/api/topics`)).json();
    const chats = await (await page.request.get(`${BASE}/api/chats`)).json();
    const agents = await (await page.request.get(`${BASE}/api/agents`)).json();
    assert.ok(topics.length && chats.length && agents.length, "Seed the demo first.");
    const topic = topics.find((item) => item.slug === "onboarding-checklist") ?? topics[0];
    const chat = chats[0];

    for (const [kind, container] of [["topics", topic], ["chats", chat]]) {
      const messages = Array.from({ length: 242 }, (_, i) => ({
        id: 10000 + i,
        public_id: randomUUID(),
        topic_id: kind === "topics" ? container.id : null,
        chat_id: kind === "chats" ? container.id : null,
        role: i % 2 ? "assistant" : "user",
        content: `Link regression message ${i + 1}`,
        kind: null,
        tool_calls: null,
        created_at: new Date(2026, 0, 1, 0, i).toISOString(),
        attachments: [],
        suggestions: [],
      }));
      const path = `/api/${kind}/${container.id}/messages`;
      let earlierReads = 0;
      let failFirst = false;
      let failEarlier = false;
      await page.route(`**${path}**`, async (route) => {
        const url = new URL(route.request().url());
        if (url.pathname === `${path}/turns`) return route.fulfill({ json: [] });
        if (url.pathname !== path || route.request().method() !== "GET") return route.fallback();
        const before = Number(url.searchParams.get("before_id") || Infinity);
        if (before !== Infinity) earlierReads++;
        if ((before === Infinity && failFirst) || (before !== Infinity && failEarlier)) {
          return route.fulfill({ status: 503, json: { detail: "Regression network failure" } });
        }
        const limit = Number(url.searchParams.get("limit") || messages.length);
        return route.fulfill({ json: messages.filter((m) => m.id < before).slice(-limit) });
      });

      const target = messages[3];
      const link = `${BASE}/${kind}/id/${container.public_id}#message=${target.public_id}`;
      await page.goto(link);
      const message = await focused(page, target.public_id);
      assert.ok(earlierReads > 0, "An old target must load earlier pages.");
      assert.ok(page.url().endsWith(`#message=${target.public_id}`), "Canonicalization retains the target.");
      await message.hover();
      await message.getByRole("button", { name: "Copy link to message", exact: true }).click();
      assert.equal(await page.evaluate(() => navigator.clipboard.readText()), link);

      // Same-discussion fragment changes, Back/Forward, and a cold reload.
      const second = messages[13];
      await page.evaluate((id) => { location.hash = `message=${id}`; }, second.public_id);
      await focused(page, second.public_id);
      await page.goBack();
      await focused(page, target.public_id);
      await page.goForward();
      await focused(page, second.public_id);
      await page.reload();
      await focused(page, second.public_id);
      if (kind === "topics") {
        await page.goto(`${BASE}/t/${container.public_id}#message=${target.public_id}`);
        await focused(page, target.public_id);
      }

      // Search highlighting must neither discard the fragment nor steal focus.
      await page.goto(`${link.replace("#", "?q=regression#")}`);
      await focused(page, target.public_id);
      assert.equal(new URL(page.url()).searchParams.get("q"), "regression");
      await page.evaluate(() => { location.hash = `message=${crypto.randomUUID()}`; });
      await page.getByRole("status").filter({ hasText: "This message is no longer available." }).waitFor();

      // Readable conversation URLs still work without a message target.
      await page.goto(link);
      await focused(page, target.public_id);
      await page.goto(`${BASE}/${kind}/${encodeURIComponent(container.slug)}`);
      await page.locator(`[data-message-link="${messages.at(-1).public_id}"]`).waitFor();
      assert.equal(new URL(page.url()).hash, "");
      failFirst = true;
      await page.goto(link);
      await page.getByRole("status").filter({ hasText: "Could not load this discussion:" }).waitFor();
      failFirst = false;
      failEarlier = true;
      await page.reload();
      await page.getByRole("status").filter({ hasText: "Could not open this message:" }).waitFor();
      failEarlier = false;
      await page.reload();
      await focused(page, target.public_id);
      console.log(`${kind}: exact old reply, canonical copy, reload, search and history.`);
      await page.unroute(`**${path}**`);
    }

    // Older runs, hidden assistant chatter and folded work must all be reachable.
    const agent = agents.find((item) => item.title === "Digest writer") ?? agents[0];
    const events = [];
    for (let i = 0; i < 80; i++) {
      for (const [kind, text] of [
        ["user", `Prompt ${i}`],
        ["assistant", `Intermediate reply ${i}`],
        ["assistant", `Final reply ${i}`],
        ["idle", null],
      ]) {
        events.push({
          kind, text, link_id: randomUUID(), at: new Date(2026, 0, 1, 0, i).toISOString(),
          tool_name: null, tool_status: null, request_id: null, data: null,
          agent_run_id: i < 40 ? 101 : 102, event_id: null,
        });
      }
    }
    await page.route(`**/api/agents/${agent.id}/events**`, (route) =>
      route.fulfill({ json: {
        events, pending: [], cursor: events.length, epoch: "link-test", reset: true,
        rewindable_run_ids: [],
      } }));
    await page.route(`**/api/agents/${agent.id}/runs**`, (route) =>
      route.fulfill({ json: [102, 101].map((id) => ({
        id, agent_id: agent.id, trigger: "manual", status: "completed",
        total_input_tokens: 0, total_output_tokens: 0,
      })) }));
    await page.route(`**/api/agents/${agent.id}/artifacts`, (route) =>
      route.fulfill({ json: [{
        id: 9001, agent_id: agent.id, agent_run_id: 102, key: null,
        kind: "markdown", title: "Link regression result", content: "Published test result.",
        created_at: events.at(-1).at, updated_at: events.at(-1).at,
      }] }));
    await page.addInitScript(() => localStorage.setItem("precursor:agent-show-prefs", JSON.stringify({
      system: false, tool: false, thinking: false, assistant: false, lifecycle: false, fold: true,
    })));
    const event = events[5];
    const agentLink = `${BASE}/agents/${agent.public_id}#event=${event.link_id}`;
    await page.goto(agentLink);
    const eventNode = await focused(page, event.link_id);
    await eventNode.hover();
    await eventNode.getByRole("button", { name: "Copy link to message", exact: true }).click();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), agentLink);
    await page.reload();
    await focused(page, event.link_id);
    await page.locator('button[data-tooltip="Fold these steps"]').click();
    assert.equal(new URL(page.url()).hash, "");
    await page.waitForFunction((id) => !document.querySelector(`[data-message-link="${id}"]`), event.link_id);
    await page.goto(`${agentLink.replace("#", "?q=regression#")}&keep=1`);
    await focused(page, event.link_id);
    const resultTab = page.getByRole("tab", { name: /^Result/ });
    await resultTab.click();
    await page.waitForFunction(() => document.getElementById("agent-pane-result")?.getAttribute("aria-selected") === "true");
    assert.equal(new URL(page.url()).searchParams.get("q"), "regression");
    assert.equal(new URL(page.url()).hash, "#keep=1");
    await page.goto(agentLink);
    await focused(page, event.link_id);
    await page.getByRole("button", { name: "Show usage and workflow filters" }).click();
    const latestRun = page.locator('button[data-tooltip^="Run #102 "]');
    await latestRun.click();
    await page.waitForFunction(() => {
      const button = document.querySelector('button[data-tooltip^="Run #102 "]');
      return button?.getAttribute("aria-pressed") === "true";
    });
    assert.equal(new URL(page.url()).hash, "");
    await page.goto(agentLink);
    await focused(page, event.link_id);
    await page.evaluate(() => { location.hash = `event=${crypto.randomUUID()}`; });
    await page.getByRole("status").filter({ hasText: "This message is no longer available." }).waitFor();
    assert.deepEqual(errors, [], "No browser errors.");
    console.log("agents: stable event copy, older runs, folded replies, reload and unavailable targets.");
    await context.close();
  } finally {
    await browser.close();
  }
}

run().catch((error) => { console.error(error); process.exitCode = 1; });
