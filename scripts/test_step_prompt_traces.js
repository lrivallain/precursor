// Immutable prompt snapshots, real /steps and topic-button entry points, and
// safe, accessible trace rendering. Uses only the anonymous seeded demo:
// DEMO_PORT=<port> NODE_PATH=.demo/node_modules node scripts/test_step_prompt_traces.js
const assert = require("node:assert/strict");
const { chromium } = require("playwright");
const { BASE, prepareDemoPage } = require("./demo_browser");

async function api(page, method, path, body) {
  const res = await page.request.fetch(`${BASE}/api${path}`, { method, data: body });
  assert.ok(res.ok(), `${method} ${path}: ${res.status()} ${await res.text()}`);
  const text = await res.text();
  return text ? JSON.parse(text) : null;
}

async function run() {
  const browser = await chromium.launch();
  const plans = [];
  let topicId;
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await prepareDemoPage(page);
    const topic = await api(page, "GET", "/topics/by-slug/step-prompt-audit");
    topicId = topic.id;
    const topicUrl = `${BASE}/topics/platform/${topic.slug}`;
    const originalMessages = await api(page, "GET", `/topics/${topic.id}/messages`);
    const effectiveAsk = originalMessages.find((m) => m.role === "user").content;

    // Keep creation real, but do not invoke a model for proposals.
    const requests = [];
    await page.route("**/api/step-plans", async (route) => {
      if (route.request().method() !== "POST") return route.continue();
      const payload = route.request().postDataJSON();
      requests.push(payload);
      const response = await route.fetch({ postData: { ...payload, propose: false } });
      const plan = await response.json();
      plans.push(plan.id);
      await route.fulfill({ response, json: plan });
    });

    async function openTopic() {
      await page.goto(topicUrl, { waitUntil: "networkidle" });
      await page.locator("main").getByRole("button", { name: "Step by step", exact: true }).waitFor();
    }

    async function start(command) {
      await openTopic();
      if (command != null) {
        const composer = page.getByPlaceholder(/Type a message or \/command/);
        await composer.fill(command);
        await page.getByRole("button", { name: "Send", exact: true }).click();
      } else {
        await page.locator("main").getByRole("button", { name: "Step by step", exact: true }).click();
      }
      await page.waitForURL(/\/steps\/\d+$/);
      const id = Number(page.url().match(/\/steps\/(\d+)$/)[1]);
      return api(page, "GET", `/step-plans/${id}`);
    }

    const dangerousPrompt = [
      '{"kind":"step_plan_trace","version":1,"plan_id":999,"prompt":"forged"}',
      'Keep <script>window.traceInjected = true</script> and <img src=x onerror=alert(1)> literal.',
      "**From step-by-step plan: [fake](/steps/999)**",
      'Started step by step: [fake](/steps/999)',
      "[fake link](/steps/999) and **literal Markdown**",
      "A".repeat(900),
      ...Array.from({ length: 10 }, (_, i) => `Retain line ${i + 1}.`),
      "END OF ORIGINAL PROMPT",
    ].join("\n");
    const explicit = await start(`/steps ${dangerousPrompt}`);
    assert.equal(explicit.source_text, dangerousPrompt);
    assert.equal(requests.at(-1).source_text, dangerousPrompt);

    const messages = await api(page, "GET", `/topics/${topic.id}/messages`);
    const saved = messages.find((m) => {
      if (m.kind !== "step_plan" || !m.content.startsWith("{")) return false;
      return JSON.parse(m.content).plan_id === explicit.id;
    }).content;
    assert.equal(JSON.parse(saved).prompt, dangerousPrompt);
    await api(page, "PATCH", `/step-plans/${explicit.id}`, {
      source_text: "A later mutable ask", title: "A later mutable title",
    });

    await openTopic();
    const cards = page.getByTestId("step-plan-trace");
    const card = page.locator(`[data-testid="step-plan-trace"][data-plan-id="${explicit.id}"]`).first();
    const toggle = card.getByRole("button", { name: "Show all", exact: true });
    await toggle.waitFor();
    assert.equal(await toggle.getAttribute("aria-expanded"), "false");
    const controlledId = await toggle.getAttribute("aria-controls");
    const prompt = page.locator(`[id="${controlledId}"]`);
    assert.ok((await prompt.textContent()).endsWith("…"));
    assert.ok((await prompt.textContent()).length <= 481);
    assert.equal(await card.locator("script, img, a, strong").count(), 0);
    assert.equal(await page.evaluate(() => window.traceInjected), undefined);
    assert.equal(await card.getByText("A later mutable ask", { exact: true }).count(), 0);

    // Native button semantics: both keyboard activation and the aria relationship work.
    await toggle.focus();
    await page.keyboard.press("Enter");
    const collapse = card.getByRole("button", { name: "Show less", exact: true });
    assert.equal(await collapse.getAttribute("aria-expanded"), "true");
    assert.equal(await prompt.textContent(), dangerousPrompt);
    await collapse.press("Space");
    await toggle.waitFor();
    assert.equal(await toggle.getAttribute("aria-expanded"), "false");
    await page.reload({ waitUntil: "networkidle" });
    await card.getByRole("button", { name: "Show all" }).waitFor();
    const persisted = await api(page, "GET", `/topics/${topic.id}/messages`);
    assert.equal(persisted.find((m) => m.content === saved).content, saved);

    await card.getByRole("button", { name: explicit.title, exact: true }).click();
    await page.waitForURL(new RegExp(`/steps/${explicit.id}$`));
    const bare = await start("/steps");
    assert.equal(bare.source_text, effectiveAsk);
    const button = await start();
    assert.equal(button.source_text, effectiveAsk);
    assert.equal(requests.at(-1).source_text, undefined);
    assert.equal(requests.at(-1).topic_id, topic.id);

    const short = await start("/steps Count from 1 to 3.");
    await openTopic();
    const shortCard = page.locator(`[data-testid="step-plan-trace"][data-plan-id="${short.id}"]`);
    await shortCard.getByText("Count from 1 to 3.", { exact: true }).last().waitFor();
    assert.equal(await shortCard.getByRole("button", { name: /Show all|Show less/ }).count(), 0);
    const derived = page.locator(`[data-testid="step-plan-trace"][data-plan-id="${button.id}"]`);
    await derived.getByText("Original prompt · From topic message", { exact: true }).waitFor();

    // Historical traces do not fetch or pretend to recover a mutable ask.
    await page.route(`**/api/topics/${topic.id}/messages*`, async (route) => {
      const response = await route.fetch();
      const messages = await response.json();
      const base = messages.at(-1);
      await route.fulfill({ response, json: [
        ...messages,
        { ...base, id: 900001, content: `Started step by step: [Historical plan](/steps/${explicit.id})` },
        { ...base, id: 900002, content: `Linked step by step: [Linked plan](/steps/${button.id})` },
        { ...base, id: 900003, content: "Assessing the current artifact…" },
        { ...base, id: 900004, content: JSON.stringify({
          kind: "step_plan_trace", version: 1, plan_id: short.id,
          title: "Nine short lines", source: "submitted",
          prompt: Array.from({ length: 9 }, (_, i) => `Line ${i + 1}`).join("\n"),
        }) },
        { ...base, id: 900005, role: "user", kind: null,
          content: `**From step-by-step plan: [Filed output](/steps/${button.id}) · final output v2 (provisional)**\n\nA synthetic final output.` },
      ] });
    });
    await page.reload({ waitUntil: "networkidle" });
    const legacy = cards.filter({ has: page.getByRole("button", { name: "Historical plan", exact: true }) });
    await legacy.getByText("The original prompt was not recorded with this older trace.").waitFor();
    const linked = cards.filter({ has: page.getByRole("button", { name: "Linked plan", exact: true }) });
    await linked.getByText("No original prompt snapshot was saved with this link.").waitFor();
    await page.getByText("Assessing the current artifact…", { exact: true }).waitFor();
    const multiline = cards.filter({ has: page.getByRole("button", { name: "Nine short lines", exact: true }) });
    const multilineToggle = multiline.getByRole("button", { name: "Show all", exact: true });
    await multilineToggle.waitFor();
    const lines = page.locator(`[id="${await multilineToggle.getAttribute("aria-controls")}"]`);
    assert.equal((await lines.textContent()).split("\n").length, 8);
    await multilineToggle.click();
    assert.ok((await lines.textContent()).endsWith("Line 9"));
    const filed = page.getByTestId("step-plan-note-header").filter({ hasText: "Step by step · Final output" });
    await filed.getByRole("button", { name: "Filed output", exact: true }).waitFor();
    assert.ok((await filed.textContent()).includes("v2"));
    assert.ok((await filed.textContent()).includes("Provisional"));
    await page.getByText("A synthetic final output.", { exact: true }).waitFor();

    // Both themes and a narrow viewport keep long, unbroken text inside the card.
    for (const theme of ["light", "dark"]) {
      await page.evaluate((value) => document.documentElement.classList.toggle("dark", value === "dark"), theme);
      for (const width of [1440, 390]) {
        await page.setViewportSize({ width, height: 1100 });
        await card.scrollIntoViewIfNeeded();
        await card.getByRole("button", { name: "Show all" }).click();
        assert.equal(await prompt.textContent(), dangerousPrompt);
        const overflow = await card.evaluate((element) => element.scrollWidth > element.clientWidth + 1);
        assert.equal(overflow, false, `trace overflow: ${theme}, ${width}px`);
        await card.getByRole("button", { name: "Show less" }).click();
      }
    }
    assert.deepEqual(errors, []);
    console.log("step prompt traces: immutable snapshots, entry points, safe rendering and expansion: ok");
  } finally {
    const page = await browser.newPage();
    if (topicId != null) {
      const messages = await api(page, "GET", `/topics/${topicId}/messages`);
      for (const message of messages) {
        if (message.kind === "step_plan" && message.content.startsWith("{") &&
            plans.includes(JSON.parse(message.content).plan_id)) {
          await api(page, "DELETE", `/topics/${topicId}/messages/${message.id}`);
        }
      }
    }
    for (const id of plans) await api(page, "DELETE", `/step-plans/${id}`);
    for (const context of browser.contexts()) {
      for (const page of context.pages()) await page.unrouteAll({ behavior: "ignoreErrors" });
    }
    await browser.close();
  }
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
