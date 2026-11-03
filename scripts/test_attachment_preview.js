// DEMO_PORT=<port> NODE_PATH=.demo/node_modules node scripts/test_attachment_preview.js
const assert = require("node:assert/strict");
const { chromium } = require("playwright");
const { BASE, prepareDemoPage } = require("./demo_browser");
const { mockAttachmentPreviews } = require("./attachment_preview_fixture");

async function main() {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const [width, colorScheme] of [[1440, "light"], [390, "dark"]]) {
      const page = await browser.newPage({ viewport: { width, height: 900 }, colorScheme });
      await prepareDemoPage(page);
      await mockAttachmentPreviews(page);
      await page.goto(`${BASE}/topics/release-2026-8`, { waitUntil: "networkidle" });
      const initialUrl = page.url();
      const trigger = page.getByRole("button", { name: "Preview workflow-overview.svg", exact: true });
      await trigger.click();
      let dialog = page.getByRole("dialog", { name: "workflow-overview.svg", exact: true });
      await dialog.locator("img").evaluate((image) => image.decode());
      assert.equal(page.url(), initialUrl);
      assert.equal(page.context().pages().length, 1, "Preview must not open another tab");
      assert.equal(await dialog.getByLabel("Zoom level").textContent(), "100%");
      const fitWidth = await dialog.locator("img").evaluate((image) => image.width);
      await dialog.getByRole("button", { name: "Zoom in", exact: true }).click();
      assert.equal(await dialog.getByLabel("Zoom level").textContent(), "125%");
      assert.ok(await dialog.locator("img").evaluate((image) => image.width) > fitWidth);
      await dialog.getByRole("button", { name: "Fit image to window", exact: true }).click();
      await dialog.getByLabel("Attachment content").focus();
      await page.keyboard.press("+");
      assert.equal(await dialog.getByLabel("Zoom level").textContent(), "125%");
      await page.keyboard.press("0");
      assert.equal(await dialog.getByLabel("Zoom level").textContent(), "100%");
      assert.equal(await dialog.getByLabel("Attachment content").evaluate((element) =>
        element.dispatchEvent(new WheelEvent("wheel", { ctrlKey: true, deltaY: -100, bubbles: true, cancelable: true })),
      ), false, "Zoom gesture suppresses browser zoom");
      await page.waitForFunction(() => document.querySelector("dialog output")?.textContent === "125%");
      for (let i = 0; i < 16; i++) await dialog.getByLabel("Attachment content").press("+");
      assert.equal(await dialog.getByLabel("Zoom level").textContent(), "400%");
      assert.ok(await dialog.getByRole("button", { name: "Zoom in", exact: true }).isDisabled());
      assert.ok(await dialog.getByLabel("Attachment content").evaluate((element) => element.scrollWidth > element.clientWidth));
      for (let i = 0; i < 20; i++) await dialog.getByLabel("Attachment content").press("-");
      assert.equal(await dialog.getByLabel("Zoom level").textContent(), "25%");
      assert.ok(await dialog.getByRole("button", { name: "Zoom out", exact: true }).isDisabled());
      const box = await dialog.boundingBox();
      assert.ok(box.x >= 0 && box.x + box.width <= width + 1, "Viewer fits mobile and desktop");
      for (let i = 0; i < 10; i++) {
        await page.keyboard.press("Tab");
        assert.ok(await dialog.evaluate((element) => element.contains(document.activeElement)), "Focus stays in preview");
      }
      const popupPromise = page.waitForEvent("popup");
      await dialog.getByRole("link", { name: "Open in new tab", exact: true }).click();
      const popup = await popupPromise;
      assert.match(popup.url(), /\/api\/attachments\/99001$/);
      await popup.close();
      await page.keyboard.press("Escape");
      await dialog.waitFor({ state: "detached" });
      assert.ok(await trigger.evaluate((element) => document.activeElement === element), "Closing restores focus");
      assert.equal(await page.evaluate(() => document.body.style.overflow), "");

      await trigger.click();
      dialog = page.getByRole("dialog", { name: "workflow-overview.svg", exact: true });
      assert.equal(await dialog.getByLabel("Zoom level").textContent(), "100%", "Zoom resets when reopened");
      await page.mouse.click(1, 1);
      await dialog.waitFor({ state: "detached" });

      await page.getByRole("button", { name: "Preview example.html", exact: true }).click();
      dialog = page.getByRole("dialog", { name: "example.html", exact: true });
      await dialog.locator("pre").waitFor();
      assert.match(await dialog.locator("pre").textContent(), /<script>/);
      assert.equal(await page.evaluate(() => window.attachmentScriptRan), undefined, "HTML attachments are inert text");
      await dialog.getByRole("button", { name: "Zoom in", exact: true }).click();
      assert.equal(await dialog.locator("pre").evaluate((element) => getComputedStyle(element).fontSize), "17.5px");
      await dialog.getByRole("button", { name: "Reset zoom", exact: true }).click();
      await page.keyboard.press("Escape");

      await page.getByRole("button", { name: "Preview not-really.pdf", exact: true }).click();
      dialog = page.getByRole("dialog", { name: "not-really.pdf", exact: true });
      await dialog.locator("pre").waitFor();
      assert.equal(await dialog.locator("iframe").count(), 0, "A filename must not turn HTML into an active frame");
      assert.equal(await page.evaluate(() => window.attachmentScriptRan), undefined);
      await page.keyboard.press("Escape");

      await page.getByRole("button", { name: "Preview reference.pdf", exact: true }).click();
      dialog = page.getByRole("dialog", { name: "reference.pdf", exact: true });
      assert.equal(await dialog.locator("iframe").getAttribute("src"), "/api/attachments/99003");
      await dialog.getByRole("button", { name: "Close attachment preview", exact: true }).click();

      await page.getByRole("button", { name: "Preview report.docx", exact: true }).click();
      dialog = page.getByRole("dialog", { name: "report.docx", exact: true });
      await dialog.getByText("This file type cannot be previewed in the app.", { exact: true }).waitFor();
      assert.equal(await dialog.getByRole("link").getAttribute("href"), "/api/attachments/99004");
      await page.keyboard.press("Escape");

      for (const [name, message] of [["missing.png", "Could not load image preview."], ["unavailable.txt", "Attachment no longer exists."]]) {
        await page.getByRole("button", { name: `Preview ${name}`, exact: true }).click();
        dialog = page.getByRole("dialog", { name, exact: true });
        await dialog.getByRole("alert").waitFor();
        assert.ok((await dialog.getByRole("alert").textContent()).includes(message));
        await page.keyboard.press("Escape");
      }

      await page.route("**/api/attachments/99002", (route) => route.fulfill({
        contentType: "text/plain", body: "a".repeat(200_001),
      }));
      await page.getByRole("button", { name: "Preview example.html", exact: true }).click();
      dialog = page.getByRole("dialog", { name: "example.html", exact: true });
      await dialog.locator("pre").waitFor();
      assert.equal((await dialog.locator("pre").textContent()).length, 200_000);
      await dialog.getByText(/Preview limited to the first 200,000/).waitFor();
      await page.keyboard.press("Escape");

      // Real uploads exercise the composer and notes URL namespaces, without an LLM call.
      const chatResponse = await page.request.post(`${BASE}/api/chats`, { data: { title: "Attachment preview browser fixture" } });
      assert.ok(chatResponse.ok());
      const chat = await chatResponse.json();
      try {
        await page.goto(`${BASE}/chats/${chat.slug}`, { waitUntil: "networkidle" });
        const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=", "base64");
        await page.locator('input[type="file"]').first().setInputFiles({ name: "pending.png", mimeType: "image/png", buffer: png });
        await page.getByRole("button", { name: "Preview pending.png", exact: true }).click();
        dialog = page.getByRole("dialog", { name: "pending.png", exact: true });
        await dialog.locator("img").evaluate((image) => image.decode());
        await page.keyboard.press("Escape");
        await page.getByRole("button", { name: "Remove pending.png", exact: true }).click();
        const composer = page.getByRole("textbox").last();
        await composer.fill("/notes");
        await page.getByRole("button", { name: "Send", exact: true }).click();
        await page.getByRole("textbox", { name: "Notes body", exact: true }).waitFor();
        await page.locator('input[type="file"]').last().setInputFiles({ name: "notes.png", mimeType: "image/png", buffer: png });
        await page.getByRole("button", { name: "Preview notes.png", exact: true }).click();
        dialog = page.getByRole("dialog", { name: "notes.png", exact: true });
        assert.match(await dialog.locator("img").getAttribute("src"), /^\/api\/notes\/attachments\//);
        await dialog.locator("img").evaluate((image) => image.decode());
        await page.keyboard.press("Escape");
        await page.getByRole("textbox", { name: "Notes body", exact: true }).waitFor();
        await page.getByRole("button", { name: "Preview", exact: true }).click();
        await page.getByRole("button", { name: "Preview notes.png", exact: true }).click();
        await page.getByRole("dialog", { name: "notes.png", exact: true }).getByRole("button", { name: "Close attachment preview", exact: true }).click();
        await page.getByRole("button", { name: "Discard notes", exact: true }).click();
      } finally {
        const removed = await page.request.delete(`${BASE}/api/chats/${chat.id}`);
        assert.ok(removed.ok());
      }
      await page.close();
      console.log(`Attachment preview passed (${width}px, ${colorScheme})`);
    }
  } finally {
    await browser.close();
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
