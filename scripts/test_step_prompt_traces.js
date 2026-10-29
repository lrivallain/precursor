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
    const effectiveAsk = originalMessages.filter((m) => m.role === "user").at(-1).content;

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
    const shortCard = page.locator(`[data-testid="step-plan-trace"][data-plan-id="${short.id}"]`).first();
    await shortCard.getByText("Count from 1 to 3.", { exact: true }).last().waitFor();
    await shortCard.getByText("Request sent to Step by step", { exact: true }).waitFor();
    assert.equal(await shortCard.getByRole("button", { name: /Show all|Show less/ }).count(), 0);
    const derived = page.locator(`[data-testid="step-plan-trace"][data-plan-id="${button.id}"]`);
    await derived.getByText("Original prompt · From topic message", { exact: true }).waitFor();

    const filedBody = `# Filed output\n\nA **synthetic** final output.\n\n- Checked sequence\n- Preserved Markdown\n\n${"RESULT".repeat(120)}`;
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
          content: `**From step-by-step plan: [Filed output](/steps/${button.id}) · final output v2 (provisional)**\n\n${filedBody}` },
        { ...base, id: 900006, role: "user", kind: null,
          content: `**From step-by-step plan: [Earlier output](/steps/${button.id})**\n\nAn older output without a heading or version.` },
        { ...base, id: 900007, content: JSON.stringify({
          kind: "step_plan_trace", version: 1, plan_id: short.id,
          title: '**Literal <img src=x onerror=alert(1)> [title](/steps/999)**' + "TITLE".repeat(60),
          source: "submitted", prompt: "Keep the title literal, too.",
        }) },
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
    const filed = page.getByTestId("step-plan-output").filter({ has: page.getByRole("heading", { name: "Filed output", exact: true }) });
    const metadata = filed.getByTestId("step-plan-note-header");
    const footer = filed.getByTestId("step-plan-note-footer");
    await metadata.getByText("Final output", { exact: true }).waitFor();
    assert.ok((await metadata.textContent()).includes("v2"));
    assert.ok((await metadata.textContent()).includes("Provisional"));
    assert.equal(await metadata.getByRole("button").count(), 0, "plan links belong in the document footer");
    await footer.getByRole("button", { name: "Filed output", exact: true }).waitFor();
    assert.equal(await filed.getByRole("heading").count(), 1, "do not duplicate the document title");
    assert.equal(await filed.locator(".markdown strong").textContent(), "synthetic");
    assert.equal(await filed.locator(".markdown li").count(), 2);
    await page.evaluate(() => {
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: {
          async write(items) {
            window.formattedCopy = {};
            for (const item of items) {
              for (const type of item.types) {
                window.formattedCopy[type] = await (await item.getType(type)).text();
              }
            }
          },
          async writeText(text) {
            window.plainCopy = text;
          },
        },
      });
    });
    await filed.hover();
    const mailCopy = filed.getByRole("button", { name: "Copy formatted", exact: true });
    assert.equal(await mailCopy.locator("svg.lucide-mail").count(), 1);
    await mailCopy.click();
    await page.waitForFunction(() => Boolean(window.formattedCopy?.["text/html"]));
    const copied = await page.evaluate(() => window.formattedCopy);
    assert.equal(copied["text/plain"], filedBody, "plain flavour must not include plan provenance");
    assert.match(copied["text/html"], /<h1 style="[^"]+">Filed output<\/h1>/);
    assert.match(copied["text/html"], /<strong>synthetic<\/strong>/);
    assert.match(copied["text/html"], /<li style="/);
    assert.doesNotMatch(copied["text/html"], /From step-by-step plan|Final output|Provisional|\/steps\/|<button|<svg/);
    await mailCopy.locator("svg.lucide-check").waitFor();
    await page.evaluate(() => Object.defineProperty(window, "ClipboardItem", { configurable: true, value: undefined }));
    await mailCopy.click();
    await page.waitForFunction(() => window.plainCopy != null);
    assert.equal(await page.evaluate(() => window.plainCopy), filedBody, "clipboard fallback must copy the document body too");
    const earlier = page.getByTestId("step-plan-output").filter({ hasText: "An older output without a heading or version." });
    assert.equal(await earlier.getByRole("heading").count(), 0);
    assert.equal(await earlier.getByTestId("step-plan-note-header").textContent(), "Final output");
    const literalTitle = cards.filter({ hasText: "Keep the title literal, too." });
    assert.equal(await literalTitle.locator("img, a, strong").count(), 0);
    assert.ok((await literalTitle.getByRole("button").textContent()).startsWith("**Literal <img"));
    const activity = page.getByText("Assessing the current artifact…", { exact: true }).locator("../..");
    assert.equal(await activity.locator("..").getAttribute("data-testid"), null);
    assert.ok((await activity.getAttribute("class")).includes("bg-accent/[0.06]"));

    // Both themes, zoom-equivalent widths and a phone keep each surface distinct.
    for (const theme of ["light", "dark"]) {
      await page.evaluate((value) => document.documentElement.classList.toggle("dark", value === "dark"), theme);
      for (const width of [1440, 720, 390]) {
        await page.setViewportSize({ width, height: 1100 });
        await card.scrollIntoViewIfNeeded();
        await card.getByRole("button", { name: "Show all" }).click();
        assert.equal(await prompt.textContent(), dangerousPrompt);
        for (const receipt of [card, shortCard, legacy, linked, multiline, literalTitle]) {
          assert.equal(await receipt.locator("svg.lucide-list-checks").count(), 1);
          const style = await receipt.evaluate((element) => {
            const css = getComputedStyle(element);
            const probe = element.ownerDocument.createElement("span");
            probe.style.color = "color-mix(in oklab, var(--section-accent) 40%, var(--border))";
            element.append(probe);
            const featureBorder = getComputedStyle(probe).color;
            probe.remove();
            return {
              background: css.backgroundColor,
              borders: [css.borderTopWidth, css.borderRightWidth, css.borderBottomWidth, css.borderLeftWidth],
              borderColor: css.borderTopColor, featureBorder,
              overflow: element.scrollWidth > element.clientWidth + 1,
            };
          });
          assert.equal(style.background, "rgba(0, 0, 0, 0)", "receipts must not become blue document cards");
          assert.deepEqual(style.borders, ["1px", "1px", "1px", "1px"], "the feature border must surround the entire receipt");
          assert.equal(style.borderColor, style.featureBorder);
          assert.equal(style.overflow, false, `receipt overflow: ${theme}, ${width}px`);
          const quote = await receipt.getByTestId("step-plan-prompt").evaluate((element) => {
            const css = getComputedStyle(element);
            const probe = element.ownerDocument.createElement("span");
            probe.style.color = "var(--border)";
            element.append(probe);
            const neutral = getComputedStyle(probe).color;
            probe.remove();
            return {
              borders: [css.borderTopWidth, css.borderRightWidth, css.borderBottomWidth, css.borderLeftWidth],
              color: css.borderLeftColor, neutral,
            };
          });
          assert.deepEqual(quote.borders, ["0px", "0px", "0px", "1px"]);
          assert.equal(quote.color, quote.neutral);
        }
        for (const document of [filed, earlier]) {
          assert.equal(await document.getByTestId("step-plan-note-header").locator("svg.lucide-list-checks").count(), 1);
          const style = await document.evaluate((element) => {
            const css = getComputedStyle(element);
            const probe = element.ownerDocument.createElement("span");
            probe.style.color = "color-mix(in oklab, var(--section-accent) 12%, var(--bg))";
            element.append(probe);
            const background = getComputedStyle(probe).color;
            probe.style.color = "var(--border)";
            const border = getComputedStyle(probe).color;
            probe.remove();
            const canvas = element.ownerDocument.createElement("canvas");
            canvas.width = canvas.height = 1;
            const context = canvas.getContext("2d");
            function luminance(color) {
              context.fillStyle = color;
              context.fillRect(0, 0, 1, 1);
              const rgb = [...context.getImageData(0, 0, 1, 1).data].slice(0, 3).map((value) => {
                const channel = value / 255;
                return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
              });
              return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722;
            }
            const surface = luminance(css.backgroundColor);
            const contrast = [".markdown", '[data-testid="step-plan-note-header"]', "footer button"].map((selector) => {
              const foreground = luminance(getComputedStyle(element.querySelector(selector)).color);
              return (Math.max(foreground, surface) + 0.05) / (Math.min(foreground, surface) + 0.05);
            });
            return {
              background: css.backgroundColor, expectedBackground: background,
              border: css.borderTopColor, expectedBorder: border, borderWidth: css.borderTopWidth,
              overflow: element.scrollWidth > element.clientWidth + 1,
              contrast,
            };
          });
          assert.equal(style.background, style.expectedBackground);
          assert.ok((await document.getAttribute("class")).includes("section-steps"), "document tint must use the Step by step section colour");
          assert.equal(style.border, style.expectedBorder);
          assert.equal(style.borderWidth, "1px");
          assert.equal(style.overflow, false, `document overflow: ${theme}, ${width}px`);
          assert.ok(style.contrast.every((ratio) => ratio >= 4.5), `document text contrast: ${theme}, ${width}px: ${style.contrast}`);
          const bounds = await document.locator(".markdown").boundingBox();
          const footerBounds = await document.getByTestId("step-plan-note-footer").boundingBox();
          assert.ok(footerBounds.y >= bounds.y + bounds.height, "plan link must follow the document");
        }
        await card.getByRole("button", { name: "Show less" }).click();
      }
    }
    await footer.getByRole("button", { name: "Filed output", exact: true }).focus();
    await page.keyboard.press("Enter");
    await page.waitForURL(new RegExp(`/steps/${button.id}$`));
    assert.deepEqual(errors, []);
    console.log("step prompt traces: immutable snapshots, receipt/document hierarchy, ListChecks icons, Markdown, formatted copying, versions, links and responsive themes: ok");
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
