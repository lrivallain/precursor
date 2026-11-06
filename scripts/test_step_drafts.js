// Browser regression for the step-by-step "unsaved work" and "stale final"
// rules. Run against scripts/demo_server.sh after seeding and building the SPA:
// NODE_PATH=.demo/node_modules node scripts/test_step_drafts.js
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

    const topic = await api(page, "POST", "/topics", { title: "Draft guard test" });
    const plan = await api(page, "POST", "/step-plans", {
      topic_id: topic.id,
      propose: false,
      source_text: "Guard test: answer two asks.",
    });
    for (const title of ["Alpha", "Bravo"]) {
      await api(page, "POST", `/step-plans/${plan.id}/steps`, { title });
    }
    await api(page, "POST", `/step-plans/${plan.id}/start`, {});

    await page.goto(`${BASE}/steps/${plan.id}`);
    const tree = page.getByRole("complementary", { name: "Plan steps" });
    const step = (title) => tree.getByText(title, { exact: true });
    const artifact = page.getByLabel("Artifact (Markdown)");
    const finalEditor = page.getByLabel("Final output (Markdown)");
    const rowMarker = (title) =>
      tree.getByRole("button").filter({ hasText: title }).getByRole("img", { name: "Unsaved edits" });
    const finalMarker = page.getByTestId("final-output-row").getByRole("img", { name: "Unsaved edits" });

    // step -> step -> back
    await step("Alpha").click();
    await artifact.fill("alpha unsaved");
    await step("Bravo").click();
    await rowMarker("Alpha").waitFor();
    assert.equal(await artifact.inputValue(), "", "Bravo starts from its own (empty) artifact");
    await step("Alpha").click();
    assert.equal(await artifact.inputValue(), "alpha unsaved", "step -> step -> back keeps the draft");

    // whitespace typed into an empty artifact is an edit, not nothing
    await step("Bravo").click();
    await artifact.fill("   ");
    await rowMarker("Bravo").waitFor();
    await step("Alpha").click();
    await step("Bravo").click();
    assert.equal(await artifact.inputValue(), "   ", "whitespace-only input is kept");
    await artifact.fill("");
    await rowMarker("Bravo").waitFor({ state: "detached" });

    // step -> final -> step, and final -> step -> final
    await page.getByTestId("final-output-row").click();
    await finalEditor.fill("final unsaved");
    await finalMarker.waitFor();
    await step("Alpha").click();
    assert.equal(await artifact.inputValue(), "alpha unsaved", "step -> final -> step keeps the step draft");
    await finalMarker.waitFor();
    await page.getByTestId("final-output-row").click();
    assert.equal(await finalEditor.inputValue(), "final unsaved", "final -> step -> final keeps the final draft");

    // another section and back, without a reload
    await page.locator('[data-tooltip^="Go to the topic"]').click();
    await page.waitForURL((u) => !u.pathname.startsWith("/steps"));
    await page.goBack();
    await page.waitForURL(`${BASE}/steps/${plan.id}`);
    await step("Alpha").click();
    assert.equal(await artifact.inputValue(), "alpha unsaved", "leaving the section and returning keeps drafts");
    await page.getByTestId("final-output-row").click();
    assert.equal(await finalEditor.inputValue(), "final unsaved");

    // saving clears the marker and the draft
    await step("Alpha").click();
    await artifact.fill("alpha saved");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await rowMarker("Alpha").waitFor({ state: "detached" });
    await step("Bravo").click();
    await step("Alpha").click();
    assert.equal(await artifact.inputValue(), "alpha saved");

    // stale final: reviewed final -> change an approved step -> reapprove
    const detail = await api(page, "GET", `/step-plans/${plan.id}`);
    const [alpha, bravo] = ["Alpha", "Bravo"].map((t) => detail.steps.find((s) => s.title === t));
    await api(page, "PUT", `/steps/${bravo.id}/artifact`, { content: "bravo text" });
    for (const s of [alpha, bravo]) await api(page, "POST", `/steps/${s.id}/status`, { status: "validated" });
    await api(page, "PUT", `/step-plans/${plan.id}/final`, { content: "A reviewed final." });
    await api(page, "PUT", `/steps/${alpha.id}/artifact`, { content: "alpha revised" });
    await api(page, "POST", `/steps/${alpha.id}/status`, { status: "validated" });
    assert.equal((await api(page, "GET", `/step-plans/${plan.id}`)).final_stale, true);

    await page.goto(`${BASE}/steps/${plan.id}`);
    await page.getByTestId("final-output-row").click();
    const send = page.getByRole("button", { name: "Send to topic", exact: true });
    assert.equal(await send.isDisabled(), true, "a stale final cannot be sent");
    const rest = await page.request.post(`${BASE}/api/step-plans/${plan.id}/send-to-topic`, { data: {} });
    assert.equal(rest.status(), 409);
    const review = page.getByRole("button", { name: "Mark as reviewed", exact: true });
    await review.click();
    await page.getByRole("button", { name: "Save", exact: true }).waitFor();
    await page.waitForFunction(
      () => !![...document.querySelectorAll("button")].find((b) => b.textContent?.trim() === "Send to topic" && !b.disabled),
    );

    // the assessment says what it read
    const scope = async () => {
      await page.getByTestId("assessment-panel").getByRole("button").first().click();
      return page.getByTestId("assessment-scope").innerText();
    };
    // the demo's mock model cannot return an assessment, so inject one; the
    // prompts and signatures are covered by tests/test_step_plans.py
    const injected = (assessment_scope, observations) => async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      body.assessment = { score: 70, summary: "s", covered: [], evidence: [], assumptions: [], gaps: [], contradictions: [], scope: assessment_scope };
      if (observations) body.assessment.observations = observations;
      body.assessment_stale = false;
      await route.fulfill({ response, json: body });
    };
    await page.route(`**/api/step-plans/${plan.id}`, injected("final", ["A non-blocking review note."]));
    await page.reload();
    await page.getByTestId("final-output-row").click();
    assert.match(await scope(), /final output as written/);
    await page.getByTestId("assessment-panel").getByText("Observations", { exact: true }).waitFor();
    await page.getByTestId("assessment-panel").getByText("A non-blocking review note.", { exact: true }).waitFor();
    await page.unroute(`**/api/step-plans/${plan.id}`);
    await page.route(`**/api/step-plans/${plan.id}`, injected("steps"));
    await page.reload();
    await page.getByTestId("final-output-row").click();
    assert.match(await scope(), /steps only/);

    // A delegated discussion displays its inherited policy, keeps an explicit
    // override on amendment, and follows the updated policy again on Default.
    const policyTopic = await api(page, "POST", "/topics", {
      title: "Delegated policy test", approval_policy: "manual",
    });
    const policyPlan = await api(page, "POST", "/step-plans", {
      topic_id: policyTopic.id, propose: false,
      source_text: "Prepare the first part.", delegation_policy: "manual",
    });
    await api(page, "POST", `/step-plans/${policyPlan.id}/steps`, { title: "Policy step" });
    await api(page, "POST", `/step-plans/${policyPlan.id}/start`, {});
    await page.goto(`${BASE}/steps/${policyPlan.id}`);
    await page.getByRole("complementary", { name: "Plan steps" }).getByText("Policy step", { exact: true }).click();
    await page.getByRole("tab", { name: "Discussion" }).click();
    const shield = page.getByRole("button", { name: /^Tool approval policy/ });
    await shield.waitFor();
    await shield.click();
    const inherited = page.getByRole("option", { name: /^Default — Manual/ });
    await inherited.waitFor();
    assert.match(await inherited.textContent(), /Follows the delegated plan's autonomy/);
    await page.getByRole("option", { name: /^Autonomous/ }).click();

    await api(page, "PATCH", `/topics/${policyTopic.id}`, { approval_policy: "balanced" });
    const currentPolicyPlan = await api(page, "GET", `/step-plans/${policyPlan.id}`);
    await api(page, "POST", `/step-plans/${policyPlan.id}/amend`, {
      topic_id: policyTopic.id, expected_state_token: currentPolicyPlan.state_token,
      request: "Add the second part.", steps: [{ title: "Second part", instruction: "Prepare it." }],
    });
    await shield.click();
    const updatedDefault = page.getByRole("option", { name: /^Default — Balanced/ });
    await updatedDefault.waitFor();
    assert.equal(await page.getByRole("option", { name: /^Autonomous/ }).getAttribute("aria-selected"), "true");
    await updatedDefault.click();
    await shield.click();
    assert.equal(await page.getByRole("option", { name: /^Default — Balanced/ }).getAttribute("aria-selected"), "true");
    await page.keyboard.press("Escape");

    await api(page, "DELETE", `/step-plans/${policyPlan.id}`);
    await api(page, "DELETE", `/topics/${policyTopic.id}`);
    assert.deepEqual(errors, []);
    await api(page, "DELETE", `/step-plans/${plan.id}`);
    await api(page, "DELETE", `/topics/${topic.id}`);
    console.log("step drafts + stale final: ok");
  } finally {
    await browser.close();
  }
}

run().catch((error) => {
  console.error(error);
  process.exit(1);
});
