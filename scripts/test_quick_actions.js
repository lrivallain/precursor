// Run against the anonymous seeded demo after building the SPA:
// DEMO_PORT=8943 NODE_PATH=.demo/node_modules node scripts/test_quick_actions.js
const assert = require("node:assert/strict");
const { chromium } = require("playwright");
const { BASE, prepareDemoPage } = require("./demo_browser");

const STORAGE_KEY = "precursor.quickActions.position";

async function run() {
  const browser = await chromium.launch();
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await prepareDemoPage(page);
    await page.goto(BASE, { waitUntil: "networkidle" });
    const trigger = page.getByRole("button", { name: "Quick actions", exact: true });
    const menu = page.getByRole("menu", { name: "Create new", exact: true });
    const panel = page.locator(".quick-actions-popover");
    const root = page.locator(".quick-actions");
    await trigger.waitFor();
    assert.equal(await menu.isVisible(), false);
    await trigger.hover();
    await menu.waitFor();
    assert.equal(await menu.getByRole("menuitem").count(), 7);
    assert.equal(await panel.getAttribute("data-side"), "top");
    await menu.getByRole("menuitem", { name: "New chat", exact: true }).hover();
    assert.equal(await menu.isVisible(), true, "The pointer can cross the gap to the menu");
    const chatAction = menu.getByRole("menuitem", { name: "New chat", exact: true });
    const tip = page.locator('.quick-actions-tip[data-open="true"]');
    await page.waitForFunction(() => {
      const label = document.querySelector('.quick-actions-tip[data-open="true"]');
      return label && label.getAnimations({ subtree: true }).length === 0;
    });
    assert.equal(await tip.textContent(), "New chat");
    assert.ok((await tip.boundingBox()).width < 170, "Short labels do not reserve an oversized empty pill");
    assert.ok((await chatAction.boundingBox()).width > 52, "Hover magnifies the icon");
    const sizes = await menu.getByRole("menuitem").evaluateAll((buttons) =>
      buttons.map((button) => button.getBoundingClientRect().width));
    assert.ok(sizes.at(-2) > 40 && sizes.at(-2) < sizes.at(-1), "Neighbours magnify less, like the macOS Dock");
    assert.equal(sizes[0], 40, "Distant tiles keep their resting size");
    const edges = await menu.getByRole("menuitem").evaluateAll((buttons) =>
      buttons.map((button) => Math.round(button.getBoundingClientRect().right)));
    assert.ok(edges.every((edge) => edge === edges[0]), "Tiles stay aligned to the screen edge and grow inward");
    const chatBox = await chatAction.boundingBox();
    const tipBox = await tip.boundingBox();
    assert.ok(Math.abs(tipBox.x + tipBox.width - (chatBox.x + chatBox.width)) <= 1 && tipBox.x < chatBox.x,
      "The label pill grows out of the tile, away from the edge");
    assert.equal(await chatAction.getAttribute("title"), null);
    await page.mouse.move(400, 200);
    await menu.waitFor({ state: "hidden" });

    await trigger.focus();
    await page.keyboard.press("ArrowDown");
    await menu.getByRole("menuitem", { name: "New chat", exact: true }).waitFor();
    assert.equal(await page.evaluate(() => document.activeElement.getAttribute("aria-label")), "New workspace");
    await page.keyboard.press("End");
    assert.equal(await page.evaluate(() => document.activeElement.getAttribute("aria-label")), "New chat");
    await page.keyboard.press("ArrowDown");
    assert.equal(await page.evaluate(() => document.activeElement.getAttribute("aria-label")), "New workspace");
    await page.keyboard.press("ArrowUp");
    assert.equal(await page.evaluate(() => document.activeElement.getAttribute("aria-label")), "New chat");
    await page.keyboard.press("Escape");
    assert.equal(await trigger.evaluate((element) => element === document.activeElement), true);
    assert.equal(await trigger.getAttribute("aria-expanded"), "false");

    // A drag must neither open the menu nor invoke a creation action.
    const before = await trigger.boundingBox();
    await page.mouse.move(before.x + 26, before.y + 26);
    await page.mouse.down();
    await page.mouse.move(50, 50, { steps: 12 });
    await page.mouse.up();
    assert.equal(await trigger.getAttribute("aria-expanded"), "false");
    const moved = await root.boundingBox();
    assert.ok(moved.x < 60 && moved.y < 60);
    assert.equal(await page.locator("html").evaluate((element) => element.classList.contains("resizing")), false);
    const stored = await page.evaluate((key) => JSON.parse(localStorage.getItem(key)), STORAGE_KEY);
    assert.ok(stored.right > 1300 && stored.bottom > 900);
    await page.reload({ waitUntil: "networkidle" });
    await trigger.waitFor();
    assert.deepEqual(await root.boundingBox(), moved, "The dragged position survives reload");
    await page.mouse.move(400, 200);
    await trigger.hover();
    await menu.waitFor();
    assert.equal(await panel.getAttribute("data-side"), "bottom");
    const leftMenu = await menu.boundingBox();
    assert.ok(leftMenu.x >= 10 && leftMenu.y > moved.y, "Near the top-left corner the dock opens downward");
    assert.equal(await menu.getByRole("menuitem").first().getAttribute("aria-label"), "New chat",
      "New chat stays next to the launcher when the dock opens downward");
    await page.mouse.move(400, 200);
    await menu.waitFor({ state: "hidden" });

    await trigger.focus();
    await page.keyboard.press("Alt+Home");
    await page.keyboard.press("Alt+ArrowLeft");
    assert.equal(await root.evaluate((element) => element.style.right), "48px");
    await page.keyboard.press("Alt+Shift+ArrowUp");
    assert.equal(await root.evaluate((element) => element.style.bottom), "32px");
    await page.keyboard.press("Alt+Home");
    console.log("Hover, keyboard navigation, drag, persistence, placement and keyboard positioning passed.");

    // Every shortcut opens the existing creation surface, not a server-side item.
    const targets = [
      ["New chat", /\/chats$/, 'textarea[data-composer]'],
      ["New topic", /\/topics(?:\/[^/]+)*$/, 'main input'],
      ["New agent", /\/agents$/, 'main textarea'],
      ["New live session", /\/live$/, 'main input'],
      ["New plan", /\/steps$/, '[role="dialog"]'],
      ["New workflow", /\/workflows$/, 'main input'],
      ["New workspace", /\/ws$/, 'h2:has-text("New workspace")'],
    ];
    const mutations = [];
    await page.route("**/api/**", (route) => {
      if (["POST", "PUT", "DELETE", "PATCH"].includes(route.request().method())) {
        mutations.push(route.request().url());
        return route.fulfill({ status: 500, json: { detail: "Creation shortcuts must not write data" } });
      }
      return route.fallback();
    });
    for (const [label, url, surface] of targets) {
      await trigger.focus();
      await page.keyboard.press("ArrowDown");
      const action = menu.getByRole("menuitem", { name: label, exact: true });
      if (label === "New chat") {
        await action.hover();
        await page.locator('.quick-actions-tip[data-open="true"] .quick-actions-label').click();
      } else {
        await action.click();
      }
      await page.waitForURL(url);
      await page.locator(surface).first().waitFor();
      assert.equal(await trigger.getAttribute("aria-expanded"), "false");
      if (await page.getByRole("dialog").count()) await page.keyboard.press("Escape");
      if (label === "New workspace") await page.getByRole("button", { name: "Cancel", exact: true }).click();
    }
    assert.deepEqual(mutations, [], "Selecting a shortcut only opens the creation UI");
    console.log("All seven shortcuts open their creation flows without creating empty items.");

    await page.emulateMedia({ reducedMotion: "reduce", colorScheme: "dark" });
    await trigger.focus();
    await page.keyboard.press("ArrowDown");
    assert.equal(await panel.evaluate((element) => getComputedStyle(element).transitionDuration), "0s");
    await page.mouse.click(400, 200);
    await menu.waitFor({ state: "hidden" });

    await page.setViewportSize({ width: 360, height: 320 });
    await trigger.focus();
    await page.keyboard.press("Alt+Home");
    await page.keyboard.press("Alt+ArrowUp");
    await page.keyboard.press("Alt+ArrowUp");
    await page.keyboard.press("Alt+ArrowUp");
    await page.keyboard.press("Alt+ArrowUp");
    await page.keyboard.press("ArrowDown");
    const shortMenu = await menu.boundingBox();
    assert.ok(shortMenu.x >= 16 && shortMenu.y >= 16);
    assert.ok(shortMenu.x + shortMenu.width <= 344 && shortMenu.y + shortMenu.height <= 304);
    assert.ok(await menu.evaluate((element) => element.scrollHeight > element.clientHeight), "Short viewports scroll");
    await page.keyboard.press("End");
    assert.ok(await menu.getByRole("menuitem", { name: "New workspace", exact: true }).isVisible());
    await page.keyboard.press("Escape");
    await page.setViewportSize({ width: 220, height: 240 });
    const resized = await trigger.boundingBox();
    assert.ok(resized.x >= 16 && resized.x + resized.width <= 204);
    assert.ok(resized.y >= 16 && resized.y + resized.height <= 224);
    console.log("Reduced motion, outside dismissal, short-screen scrolling and resize clamping passed.");

    const touchContext = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    const touchPage = await touchContext.newPage();
    await prepareDemoPage(touchPage);
    await touchPage.goto(BASE, { waitUntil: "networkidle" });
    const touchTrigger = touchPage.getByRole("button", { name: "Quick actions", exact: true });
    await touchTrigger.tap();
    const touchMenu = touchPage.getByRole("menu", { name: "Create new", exact: true });
    await touchMenu.waitFor();
    await touchTrigger.tap();
    await touchMenu.waitFor({ state: "hidden" });
    await touchTrigger.tap();
    await touchMenu.getByRole("menuitem", { name: "New chat", exact: true }).tap();
    await touchPage.waitForURL(/\/chats$/);
    await touchPage.locator("textarea[data-composer]").waitFor();
    await touchPage.unrouteAll({ behavior: "ignoreErrors" });
    await touchContext.close();

    const settings = await (await page.request.get(`${BASE}/api/settings`)).json();
    await page.route("**/api/settings", (route) => route.fulfill({
      json: { ...settings, agents_enabled: false, live_enabled: false },
    }));
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(BASE, { waitUntil: "networkidle" });
    await trigger.focus();
    await page.keyboard.press("ArrowDown");
    await menu.waitFor();
    assert.deepEqual(await menu.getByRole("menuitem").evaluateAll((buttons) => buttons.map((button) => button.getAttribute("aria-label"))),
      ["New workspace", "New plan", "New topic", "New chat"]);

    await page.unroute("**/api/settings");
    await page.route("**/api/settings", (route) => route.fulfill({ json: { ...settings, show_quick_actions: false } }));
    await page.goto(BASE, { waitUntil: "networkidle" });
    await page.locator("main").first().waitFor();
    assert.equal(await trigger.count(), 0, "The Appearance setting hides the launcher");
    assert.deepEqual(errors, []);
    console.log("Touch toggling/selection, feature availability and the hide setting passed; no browser errors.");
    await page.unrouteAll({ behavior: "ignoreErrors" });
    await context.close();
  } finally {
    await Promise.all(browser.contexts().flatMap((context) =>
      context.pages().map((page) => page.unrouteAll({ behavior: "ignoreErrors" }))));
    await browser.close();
  }
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
