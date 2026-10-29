// Browser regression for failed autosaves in the step-by-step editors: the
// typed text must survive navigating away and back, the failure must be shown,
// and a retry must save it. Run against scripts/demo_server.sh after seeding
// and building the SPA:
// NODE_PATH=.demo/node_modules node scripts/test_step_field_failures.js
// It creates its own disposable topic and plan through the API.
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

async function run() {
  const browser = await chromium.launch();
  try {
    const context = await browser.newContext({ viewport: { width: 1500, height: 1000 } });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await prepareDemoPage(page);

    const topic = await api(page, "POST", "/topics", { title: "Autosave failure test" });
    const plan = await api(page, "POST", "/step-plans", {
      topic_id: topic.id,
      propose: false,
      source_text: "Autosave test: answer two asks.",
    });
    for (const title of ["Alpha", "Bravo"]) {
      await api(page, "POST", `/step-plans/${plan.id}/steps`, { title });
    }
    await api(page, "POST", `/step-plans/${plan.id}/start`, {});
    const stepOf = async (title) =>
      (await api(page, "GET", `/step-plans/${plan.id}`)).steps.find((s) => s.title === title);

    // every step/plan edit fails until `failing` is switched off
    let failing = true;
    await page.route(/\/api\/steps\/\d+$/, async (route) => {
      if (failing && route.request().method() === "PATCH") {
        await route.fulfill({ status: 500, json: { detail: "disk on fire" } });
      } else await route.continue();
    });

    await page.goto(`${BASE}/steps/${plan.id}`);
    const tree = page.getByRole("complementary", { name: "Plan steps" });
    const step = (title) => tree.getByText(title, { exact: true });
    const rowMarker = (title) =>
      tree.getByRole("button").filter({ hasText: title }).getByRole("img", { name: "Unsaved edits" });
    const instruction = page.getByLabel(/^Instruction/);
    const title = page.getByLabel(/^Title/);
    const notSaved = page.getByRole("alert").filter({ hasText: "Not saved" });

    // the fields sit in a collapsed "Title and instruction" block of a leaf step
    const open = async () => {
      const summary = page.getByText("Title and instruction", { exact: true });
      await summary.waitFor();
      if (!(await summary.evaluate((el) => el.parentElement.open))) await summary.click();
    };
    await step("Alpha").click();
    await open();
    await instruction.fill("  keep this instruction  ");
    await title.fill("Alpha renamed");
    // navigating blurs the field: the save fails, the text must stay
    await step("Bravo").click();
    await rowMarker("Alpha").waitFor();
    assert.equal((await stepOf("Alpha")).instruction, "", "nothing was saved");
    await page.getByText("disk on fire").first().waitFor();

    await step("Alpha").click();
    await open();
    assert.equal(await instruction.inputValue(), "  keep this instruction  ", "the typed instruction is kept");
    assert.equal(await title.inputValue(), "Alpha renamed", "the typed title is kept");
    await notSaved.waitFor();

    // retry succeeds once the failure is gone
    failing = false;
    await notSaved.getByRole("button", { name: "Retry" }).click();
    await notSaved.waitFor({ state: "detached" });
    await rowMarker("Alpha").waitFor({ state: "detached" });
    const saved = await stepOf("Alpha renamed");
    assert.equal(saved.instruction, "keep this instruction");
    await open();
    assert.equal(await instruction.inputValue(), "keep this instruction");

    // a successful blur-save leaves no marker either
    await open();
    await instruction.fill("second edit");
    await step("Bravo").click();
    await rowMarker("Alpha").waitFor({ state: "detached" });
    assert.equal((await stepOf("Alpha renamed")).instruction, "second edit");

    // the plan-level ask in the drafting view keeps its text the same way
    const draftPlan = await api(page, "POST", "/step-plans", {
      topic_id: topic.id,
      propose: false,
      source_text: "Original ask",
    });
    let failAsk = true;
    await page.route(new RegExp(`/api/step-plans/${draftPlan.id}$`), async (route) => {
      if (failAsk && route.request().method() === "PATCH") {
        await route.fulfill({ status: 500, json: { detail: "no space left" } });
      } else await route.continue();
    });
    await page.goto(`${BASE}/steps/${draftPlan.id}`);
    const ask = page.getByLabel("The ask", { exact: true });
    await ask.waitFor();
    await ask.fill("A new ask that must not be lost");
    await page.getByTestId("step-plan-view").click({ position: { x: 5, y: 5 } });
    await notSaved.waitFor();
    // away to the topic and back, in the app (no reload: drafts live in the page)
    await page.locator('[data-tooltip^="Go to the topic"]').click();
    await page.waitForURL((u) => !u.pathname.startsWith("/steps"));
    await page.goBack();
    await page.waitForURL(`${BASE}/steps/${draftPlan.id}`);
    await notSaved.waitFor();
    assert.equal(await page.getByLabel("The ask", { exact: true }).inputValue(), "A new ask that must not be lost");

    assert.deepEqual(errors, []);
    await api(page, "DELETE", `/step-plans/${draftPlan.id}`);
    await api(page, "DELETE", `/step-plans/${plan.id}`);
    console.log("step field autosave failures: ok");
  } finally {
    await browser.close();
  }
}

run().catch((error) => {
  console.error(error);
  process.exit(1);
});
