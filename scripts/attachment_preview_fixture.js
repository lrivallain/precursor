// Synthetic browser fixtures shared by attachment smoke tests and screenshots.
const IMAGE = `<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="1000" viewBox="0 0 1600 1000">
<rect width="1600" height="1000" fill="#f1f5f9"/>
<text x="120" y="160" font-family="sans-serif" font-size="54" font-weight="bold" fill="#0f172a">A closer look at your workflow</text>
<text x="120" y="220" font-family="sans-serif" font-size="28" fill="#475569">Preview, zoom, and keep the conversation in view.</text>
<path d="M470 520H650M950 520H1130" stroke="#64748b" stroke-width="8"/>
<g fill="#fff" stroke="#cbd5e1" stroke-width="3">
<rect x="120" y="360" width="350" height="320" rx="32"/>
<rect x="650" y="360" width="300" height="320" rx="32"/>
<rect x="1130" y="360" width="350" height="320" rx="32"/>
</g>
<g font-family="sans-serif" font-size="34" text-anchor="middle" fill="#0f172a">
<text x="295" y="510">Attach</text><text x="800" y="510">Inspect</text><text x="1305" y="510">Continue</text>
</g>
<g font-family="sans-serif" font-size="24" text-anchor="middle" fill="#475569">
<text x="295" y="565">Images and documents</text><text x="800" y="565">Zoom into details</text><text x="1305" y="565">Stay in your chat</text>
</g>
<rect x="120" y="790" width="1360" height="90" rx="20" fill="#dbeafe"/>
<text x="160" y="846" font-family="sans-serif" font-size="26" fill="#1e40af">Original files are always one click away.</text>
</svg>`;

const attachments = [
  { id: 99001, mime: "image/svg+xml", original_filename: "workflow-overview.svg" },
  { id: 99002, mime: "text/html", original_filename: "example.html" },
  { id: 99003, mime: "application/pdf", original_filename: "reference.pdf" },
  { id: 99004, mime: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", original_filename: "report.docx" },
  { id: 99005, mime: "image/png", original_filename: "missing.png" },
  { id: 99006, mime: "text/plain", original_filename: "unavailable.txt" },
  { id: 99007, mime: "text/html", original_filename: "not-really.pdf" },
].map((attachment) => ({ ...attachment, size: 1000, url: `/api/attachments/${attachment.id}`, created_at: "2026-01-01T10:00:00Z" }));

async function mockAttachmentPreviews(page) {
  await page.route(/\/api\/topics\/\d+\/messages(?:\?.*)?$/, async (route) => {
    const response = await route.fetch();
    const messages = await response.json();
    const first = messages.find((message) => message.role === "user");
    if (!first) throw new Error("The demo topic needs a user message.");
    first.attachments = attachments;
    await route.fulfill({ response, json: messages });
  });
  await page.route(/\/api\/attachments\/9900\d$/, async (route) => {
    const id = Number(route.request().url().split("/").pop());
    if (id === 99001) return route.fulfill({ contentType: "image/svg+xml", body: IMAGE });
    if (id === 99002 || id === 99007) return route.fulfill({ contentType: "text/html", body: '<script>window.attachmentScriptRan = true</script>\n<h1>Rendered as text, never executed.</h1>' });
    if (id === 99005 || id === 99006) return route.fulfill({ status: 404, json: { detail: "Attachment no longer exists." } });
    return route.fulfill({ contentType: "application/pdf", body: "%PDF-1.4\n%%EOF" });
  });
}

module.exports = { IMAGE, attachments, mockAttachmentPreviews };
