// Browser regression for linking a step-by-step plan to a topic: the wizard
// picks a topic, an unlinked plan hides "Send to topic" until one is linked,
// and the edit/preview tabs and version history keep their place around the
// content. Run against scripts/demo_server.sh after seeding and building:
// NODE_PATH=.demo/node_modules node scripts/test_step_topic_link.js
// It creates its own disposable topic and plans through the API.
const assert = require("node:assert/strict");
const { chromium } = require("playwright");
const { BASE, prepareDemoPage } = require("./demo_browser");

async function api(page, method, path, body) {
  const res = await page.request.fetch(`${BASE}/api${path}`, {
    method,
    data: body,
    headers: { "content-type": "application/json" },
  });
  assert.ok(res.ok(), `${method} ${path} -> ${res.status()} ${await res.text()}`);
  const text = await res.text();
  return text ? JSON.parse(text) : null;
}

const top = async (locator) => (await locator.boundingBox()).y;

async function run() {
  const browser = await chromium.launch();
  const plans = [];
  try {
    const context = await browser.newContext({ viewport: { width: 1500, height: 1000 } });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await prepareDemoPage(page);

    const title = `Topic link test ${Date.now()}`;
    const topic = await api(page, "POST", "/topics", { title });

    // wizard: pick a topic, create with no ask text of its own
    await page.goto(`${BASE}/steps`);
    await page.getByRole("button", { name: /New/ }).first().click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: "None" }).click();
    await dialog.getByRole("button", { name: title }).click();
    await dialog.getByPlaceholder(/Paste the customer email/).fill("An ask typed in the wizard");
    await dialog.getByLabel(/Let the model propose/).uncheck();
    await dialog.getByRole("button", { name: "Create plan" }).click();
    await page.waitForURL(/\/steps\/\d+/);
    const linked = Number(page.url().match(/\/steps\/(\d+)/)[1]);
    plans.push(linked);
    assert.equal((await api(page, "GET", `/step-plans/${linked}`)).source_topic_id, topic.id);

    // an unlinked plan: no Send to topic until a topic is linked
    const plan = await api(page, "POST", "/step-plans", { propose: false, source_text: "Unlinked ask" });
    plans.push(plan.id);
    const step = await api(page, "POST", `/step-plans/${plan.id}/steps`, { title: "Only step" });
    await api(page, "POST", `/step-plans/${plan.id}/start`, {});
    await api(page, "PUT", `/steps/${step.id}/artifact`, { content: "The approved artifact" });
    await api(page, "POST", `/steps/${step.id}/status`, { status: "validated" });

    await page.goto(`${BASE}/steps/${plan.id}`);
    const nav = page.getByRole("complementary", { name: "Plan steps" });
    await nav.getByText("Final output", { exact: true }).click();
    const editor = page.getByLabel("Final output (Markdown)");
    await editor.fill("Final text");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await page.getByText("Version 1", { exact: true }).waitFor();
    assert.equal(await page.getByRole("button", { name: "Send to topic" }).count(), 0);

    // the tabs sit right above the content, the history below it
    const tab = page.getByRole("tab", { name: "Edit" });
    const gap = (await top(editor)) - (await top(tab));
    assert.ok(gap > 0 && gap < 80, `tabs hug the content (gap ${gap})`);
    await page.getByRole("button", { name: "History" }).click();
    const history = page.getByRole("region", { name: "Version history" });
    await history.waitFor();
    assert.ok((await top(history)) > (await top(editor)), "history renders below the content");

    // link it afterwards
    await page.getByRole("button", { name: /Link a topic/ }).first().click();
    const link = page.getByRole("dialog");
    await link.getByRole("button", { name: "None" }).click();
    await link.getByRole("button", { name: title }).click();
    await link.getByRole("button", { name: "Link", exact: true }).click();
    await page.getByRole("button", { name: "Send to topic" }).waitFor();
    assert.equal((await api(page, "GET", `/step-plans/${plan.id}`)).source_topic_id, topic.id);
    const messages = await api(page, "GET", `/topics/${topic.id}/messages`);
    assert.ok(messages.some((m) => m.content.includes("Linked step by step")), "the topic keeps a trace");

    assert.deepEqual(errors, []);
    for (const id of plans) await api(page, "DELETE", `/step-plans/${id}`);
    console.log("step topic link: ok");
  } finally {
    await browser.close();
  }
}

run().catch((error) => {
  console.error(error);
  process.exit(1);
});
