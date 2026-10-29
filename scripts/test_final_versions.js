// Browser regression for the versions of a plan's final output: every saved
// text is a version, restoring an older one records nothing new, and the
// version filed into the topic carries a "posted" marker. Run against
// scripts/demo_server.sh after seeding and building the SPA:
// NODE_PATH=.demo/node_modules node scripts/test_final_versions.js
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

    const topic = await api(page, "POST", "/topics", { title: "Final versions test" });
    const plan = await api(page, "POST", "/step-plans", {
      topic_id: topic.id,
      propose: false,
      source_text: "Final versions test: answer one ask.",
    });
    const step = await api(page, "POST", `/step-plans/${plan.id}/steps`, { title: "Only step" });
    await api(page, "POST", `/step-plans/${plan.id}/start`, {});
    await api(page, "PUT", `/steps/${step.id}/artifact`, { content: "The approved artifact" });
    await api(page, "POST", `/steps/${step.id}/status`, { status: "validated" });
    const read = () => api(page, "GET", `/step-plans/${plan.id}`);
    const versions = () => api(page, "GET", `/step-plans/${plan.id}/final/versions`);

    await page.goto(`${BASE}/steps/${plan.id}`);
    await page
      .getByRole("complementary", { name: "Plan steps" })
      .getByText("Final output", { exact: true })
      .click();

    const editor = page.getByLabel("Final output (Markdown)");
    const save = page.getByRole("button", { name: "Save", exact: true });
    const history = page.getByRole("button", { name: "History" });

    // no version before there is a text
    assert.equal(await history.count(), 0);
    await page.getByRole("tab", { name: "Edit" }).click();
    await editor.fill("First answer");
    await save.click();
    await page.getByText("Version 1", { exact: true }).waitFor();
    await page.getByRole("tab", { name: "Edit" }).click();
    await editor.fill("Second answer");
    await save.click();
    // live: the new version shows with no reload
    await page.getByText("Version 2", { exact: true }).waitFor();
    assert.equal((await versions()).length, 2);

    // the final output is the same panel as a step artifact: after a reload it
    // opens in Edit, resizable, with the rewrite helper
    await page.reload();
    await page
      .getByRole("complementary", { name: "Plan steps" })
      .getByText("Final output", { exact: true })
      .click();
    await editor.waitFor();
    assert.equal(await page.getByRole("tab", { name: "Edit" }).getAttribute("aria-selected"), "true");
    assert.equal(await editor.evaluate((el) => getComputedStyle(el).resize), "vertical");
    await page.getByRole("button", { name: "Refine with AI" }).waitFor();

    await history.click();
    const row = (n) => page.getByRole("region", { name: "Version history" }).getByRole("listitem").filter({ hasText: `v${n}` });
    await row(2).getByText("current").waitFor();
    await row(1).getByRole("button", { name: "Restore" }).click();
    await page.getByText("Version 1", { exact: true }).waitFor();
    await row(1).getByText("current").waitFor();
    assert.equal((await versions()).length, 2, "restoring recorded no new version");
    assert.equal((await read()).final_output, "First answer");

    // file version 1 in the topic: it carries the posted marker
    await page.getByRole("button", { name: "Send to topic" }).click();
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await page.getByText(/Sent to .* as version 1/).waitFor();
    await row(1).getByText("posted").waitFor();
    const filed = await read();
    assert.equal(filed.final_posted_version, 1);
    const messages = await api(page, "GET", `/topics/${topic.id}/messages`);
    const note = messages.find((m) => m.content.includes("final output v1"));
    assert.ok(note, "the topic note names the version");

    // a newer version leaves the marker on the version that was filed
    await page.getByRole("tab", { name: "Edit" }).click();
    await editor.fill("Third answer");
    await save.click();
    await page.getByText("Version 3", { exact: true }).waitFor();
    await page.getByText("v1 posted").waitFor();
    assert.equal((await read()).final_posted_version, 1);

    assert.deepEqual(errors, []);
    await api(page, "DELETE", `/step-plans/${plan.id}`);
    console.log("final output versions: ok");
  } finally {
    await browser.close();
  }
}

run().catch((error) => {
  console.error(error);
  process.exit(1);
});
