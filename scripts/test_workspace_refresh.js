// DEMO_PORT=<port> NODE_PATH=.demo/node_modules node scripts/test_workspace_refresh.js
const assert = require("node:assert/strict");
const { chromium } = require("playwright");
const { BASE, prepareDemoPage } = require("./demo_browser");

async function exercise(browser, slug, width, colorScheme) {
  const page = await browser.newPage({ viewport: { width, height: 900 }, colorScheme });
  try {
    await prepareDemoPage(page);
    const workspaces = await (await page.request.get(`${BASE}/api/workspaces`)).json();
    const workspace = workspaces.find((item) => item.slug === slug);
    assert.ok(workspace, `Missing demo workspace: ${slug}`);
    const files = new Map([
      ["README.md", "# Initial disk content\n"],
      ["other.md", "# Another file\n"],
      ["preview.html", "<h1>Initial HTML</h1>"],
      ["picture.png", ""],
      ["reference.pdf", ""],
    ]);
    let reads = 0;
    let writes = 0;
    let fail = false;
    let hold = null;
    let statusReads = 0;
    const prefix = `**/api/workspaces/${workspace.id}`;
    await page.route(`${prefix}/files`, (route) => route.fulfill({
      json: [...files.keys()].map((name) => ({ path: name, name, type: "file" })),
    }));
    await page.route(`${prefix}/file?*`, async (route) => {
      const path = new URL(route.request().url()).searchParams.get("path");
      if (route.request().method() !== "GET") {
        writes++;
        await route.fulfill({ status: 500, json: { detail: "Refresh must never write a file" } });
        return;
      }
      reads++;
      const content = files.get(path);
      if (hold) await hold;
      await route.fulfill(fail
        ? { status: 503, json: { detail: "Disk temporarily unavailable" } }
        : { json: { path, content } });
    });
    await page.route(`${prefix}/git/status`, async (route) => {
      statusReads++;
      await route.continue();
    });
    await page.route(`**/raw/${slug}/picture.png*`, (route) => route.fulfill({
      contentType: "image/png",
      body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=", "base64"),
    }));
    await page.route(`**/raw/${slug}/preview.html*`, (route) => route.fulfill({
      contentType: "text/html", body: files.get("preview.html"),
    }));
    await page.route(`**/raw/${slug}/reference.pdf*`, (route) => route.fulfill({
      contentType: "application/pdf", body: "%PDF-1.4\n%%EOF",
    }));

    await page.goto(`${BASE}/ws/${slug}/README.md`, { waitUntil: "networkidle" });
    const refresh = page.getByRole("button", { name: "Refresh file", exact: true });
    const save = page.getByRole("button", { name: "Save", exact: true });
    const initialUrl = page.url();
    await page.getByRole("heading", { name: "Initial disk content", exact: true }).waitFor();
    assert.equal(await refresh.getAttribute("data-tooltip"), "Refresh file from disk");
    const box = await refresh.boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= width, "Refresh fits the viewport");
    files.set("README.md", "# Updated disk content\n");
    const previousStatusReads = statusReads;
    await refresh.click();
    await page.getByRole("heading", { name: "Updated disk content", exact: true }).waitFor();
    assert.equal(page.url(), initialUrl, "Refresh preserves the route");
    if (workspace.kind !== "local") {
      await page.waitForFunction(() => !document.querySelector('[aria-label="Refresh file"]').disabled);
      assert.ok(statusReads > previousStatusReads, "Refresh updates git status");
    }

    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await page.locator(".monaco-editor .view-line", { hasText: "Updated disk content" }).waitFor();
    await page.locator(".monaco-editor").click({ position: { x: 150, y: 20 } });
    await page.keyboard.press("ControlOrMeta+End");
    await page.keyboard.type("unsaved local draft");
    await page.waitForFunction(() => [...document.querySelectorAll("button")].some((button) =>
      button.textContent.trim() === "Save" && !button.disabled,
    ));
    files.set("README.md", "# Newer disk content\n");
    const beforeCancel = reads;
    await refresh.click();
    const confirmation = page.getByRole("alertdialog");
    await confirmation.getByText("Discard unsaved changes and refresh this file from disk?", { exact: true }).waitFor();
    await confirmation.getByRole("button", { name: "Cancel", exact: true }).click();
    assert.equal(reads, beforeCancel, "Cancel never reads or replaces the file");
    assert.ok(await save.isEnabled(), "Cancel preserves the dirty buffer");
    await page.locator(".monaco-editor .view-line", { hasText: "unsaved local draft" }).waitFor();
    await refresh.click();
    await confirmation.getByRole("button", { name: "Discard changes", exact: true }).click();
    await page.locator(".monaco-editor .view-line", { hasText: "Newer disk content" }).waitFor();
    assert.ok(await save.isDisabled(), "Reload resets the saved baseline and preserves edit mode");
    assert.equal(await page.locator(".monaco-editor .view-line", { hasText: "unsaved local draft" }).count(), 0);

    fail = true;
    await refresh.click();
    await page.getByText("Disk temporarily unavailable", { exact: true }).waitFor();
    await page.locator(".monaco-editor .view-line", { hasText: "Newer disk content" }).waitFor();
    fail = false;
    files.set("README.md", "# Recovered disk content\n");
    await refresh.click();
    await page.locator(".monaco-editor .view-line", { hasText: "Recovered disk content" }).waitFor();
    assert.equal(await page.getByText("Disk temporarily unavailable", { exact: true }).count(), 0);

    let release;
    hold = new Promise((resolve) => { release = resolve; });
    await refresh.click();
    await page.waitForFunction(() => document.querySelector('[aria-label="Refresh file"]').disabled);
    assert.ok(await save.isDisabled(), "Save is disabled during reload");
    release();
    hold = null;
    await page.locator(".monaco-editor .view-line", { hasText: "Recovered disk content" }).waitFor();

    if (width >= 768) {
      hold = new Promise((resolve) => { release = resolve; });
      const request = page.waitForRequest((request) =>
        request.url().includes(`/api/workspaces/${workspace.id}/file?path=README.md`),
      );
      await refresh.click();
      await request;
      hold = null;
      await page.getByText("other.md", { exact: true }).click();
      await page.getByRole("heading", { name: "Another file", exact: true }).waitFor();
      const response = page.waitForResponse((response) =>
        response.url().includes(`/api/workspaces/${workspace.id}/file?path=README.md`),
      );
      release();
      await (await response).finished();
      await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      assert.equal(new URL(page.url()).pathname, `/ws/${slug}/other.md`, "A late refresh cannot reopen the previous file");
      assert.ok(await page.getByRole("heading", { name: "Another file", exact: true }).isVisible());
    }

    for (const [path, selector] of [
      ["picture.png", 'img[alt="picture.png"]'],
      ["reference.pdf", 'iframe[title="reference.pdf"]'],
      ["preview.html", 'iframe[title="preview.html"]'],
    ]) {
      await page.goto(`${BASE}/ws/${slug}/${path}`, { waitUntil: "networkidle" });
      const preview = page.locator(selector);
      await preview.waitFor();
      const before = await preview.getAttribute("src");
      const textReads = reads;
      files.set("preview.html", "<h1>Updated HTML</h1>");
      await refresh.click();
      await page.waitForFunction(({ selector, before }) =>
        document.querySelector(selector)?.getAttribute("src") !== before,
      { selector, before });
      if (path === "preview.html") {
        await page.frameLocator(selector).getByRole("heading", { name: "Updated HTML", exact: true }).waitFor();
      } else {
        assert.equal(reads, textReads, "Binary refresh never reads a file as text");
      }
    }
    assert.equal(writes, 0, "Refresh is read-only");
    console.log(`Workspace refresh passed (${slug}, ${width}px, ${colorScheme})`);
  } finally {
    await page.close();
  }
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  try {
    await exercise(browser, "design-notes", 1440, "light");
    await exercise(browser, "handbook", 390, "dark");
  } finally {
    await browser.close();
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
