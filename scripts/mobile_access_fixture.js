// Mocked remote-access responses for the Mobile access screenshot: an active
// tunnel, one paired phone and a pairing QR code. The QR carries a placeholder
// link (not a real tunnel, not a real code): a screenshot must never hold one.
const TUNNEL_URL = "https://demo-8899.euw.devtunnels.ms";
const QR_SVG = `<svg xmlns="http://www.w3.org/2000/svg" width="205" height="205" class="segno"><g transform="scale(5)"><path fill="#fff" d="M0 0h41v41h-41z"/><path class="qrline" stroke="#000" d="M2 2.5h7m2 0h1m6 0h4m2 0h2m1 0h3m2 0h7m-37 1h1m5 0h1m2 0h1m1 0h1m2 0h1m1 0h3m1 0h1m2 0h2m2 0h2m1 0h1m5 0h1m-37 1h1m1 0h3m1 0h1m7 0h4m4 0h2m2 0h1m3 0h1m1 0h3m1 0h1m-37 1h1m1 0h3m1 0h1m3 0h3m1 0h2m2 0h2m2 0h1m1 0h1m2 0h2m1 0h1m1 0h3m1 0h1m-37 1h1m1 0h3m1 0h1m3 0h1m1 0h3m1 0h2m3 0h3m1 0h1m1 0h1m2 0h1m1 0h3m1 0h1m-37 1h1m5 0h1m1 0h5m1 0h3m1 0h3m1 0h1m1 0h2m1 0h1m2 0h1m5 0h1m-37 1h7m1 0h1m1 0h1m1 0h1m1 0h1m1 0h1m1 0h1m1 0h1m1 0h1m1 0h1m1 0h1m1 0h1m1 0h7m-27 1h1m3 0h1m2 0h1m1 0h6m1 0h3m-29 1h1m2 0h1m1 0h2m1 0h2m2 0h5m1 0h2m1 0h1m1 0h7m1 0h1m-32 1h2m2 0h1m2 0h1m1 0h2m2 0h1m1 0h3m2 0h2m6 0h1m1 0h2m4 0h1m-34 1h2m1 0h1m1 0h2m1 0h1m3 0h1m1 0h3m2 0h1m2 0h2m1 0h4m3 0h2m-37 1h1m1 0h2m3 0h1m2 0h1m1 0h1m1 0h1m1 0h1m1 0h2m1 0h1m3 0h1m1 0h2m2 0h2m-32 1h2m3 0h2m1 0h3m1 0h6m1 0h2m1 0h5m1 0h2m4 0h2m-35 1h1m1 0h2m2 0h1m1 0h1m1 0h3m4 0h1m4 0h2m2 0h4m2 0h1m1 0h1m-36 1h2m1 0h6m1 0h2m1 0h1m1 0h2m2 0h2m1 0h2m1 0h1m1 0h1m2 0h2m1 0h1m1 0h1m-36 1h2m2 0h1m1 0h4m2 0h2m2 0h2m2 0h7m2 0h1m1 0h1m3 0h1m-37 1h1m2 0h1m2 0h1m2 0h2m6 0h1m1 0h4m4 0h4m2 0h3m-36 1h1m2 0h1m1 0h1m1 0h1m1 0h3m2 0h2m1 0h1m3 0h4m1 0h1m3 0h1m1 0h1m2 0h2m-36 1h3m1 0h4m2 0h2m3 0h2m2 0h1m3 0h2m3 0h8m-33 1h2m1 0h1m2 0h3m1 0h6m1 0h1m1 0h2m2 0h1m4 0h4m-36 1h1m1 0h2m2 0h2m1 0h3m1 0h2m5 0h2m2 0h3m3 0h1m1 0h1m2 0h1m-36 1h1m2 0h2m2 0h1m1 0h1m4 0h1m1 0h2m2 0h2m2 0h1m4 0h3m1 0h1m1 0h2m-36 1h1m1 0h1m1 0h2m3 0h1m3 0h1m1 0h5m1 0h1m1 0h1m3 0h1m1 0h1m4 0h2m-35 1h3m2 0h2m2 0h1m2 0h1m1 0h1m1 0h2m1 0h1m1 0h3m1 0h4m4 0h1m-36 1h1m1 0h2m1 0h2m1 0h2m2 0h2m1 0h3m1 0h1m1 0h10m2 0h1m1 0h1m-34 1h2m1 0h1m1 0h2m1 0h1m4 0h3m1 0h2m1 0h1m1 0h1m4 0h3m3 0h2m-37 1h1m1 0h1m2 0h2m2 0h1m4 0h1m2 0h2m1 0h2m1 0h2m1 0h1m1 0h1m2 0h1m1 0h1m2 0h1m-36 1h2m7 0h4m1 0h7m2 0h5m2 0h1m3 0h2m-37 1h2m2 0h1m1 0h2m2 0h1m1 0h1m1 0h1m2 0h1m2 0h5m3 0h7m1 0h1m-29 1h2m2 0h3m2 0h2m4 0h1m3 0h2m3 0h2m2 0h1m-37 1h7m3 0h2m5 0h1m9 0h2m1 0h1m1 0h2m1 0h2m-37 1h1m5 0h1m1 0h1m1 0h4m1 0h4m1 0h2m2 0h1m1 0h3m3 0h3m-35 1h1m1 0h3m1 0h1m2 0h2m1 0h1m2 0h2m4 0h1m1 0h4m1 0h6m2 0h1m-37 1h1m1 0h3m1 0h1m1 0h5m3 0h2m6 0h2m2 0h1m2 0h3m-34 1h1m1 0h3m1 0h1m4 0h1m2 0h1m3 0h2m1 0h2m2 0h1m1 0h2m2 0h2m2 0h2m-37 1h1m5 0h1m2 0h1m1 0h1m2 0h3m2 0h3m1 0h3m1 0h5m-32 1h7m1 0h1m2 0h2m1 0h1m1 0h2m2 0h2m1 0h1m1 0h1m1 0h3m5 0h2"/></g></svg>`;

async function mockMobileAccess(page) {
  const expires = new Date(Date.now() + 25 * 60_000).toISOString();
  await page.route("**/api/remote/tunnel", (route) =>
    route.fulfill({ json: { state: "active", url: TUNNEL_URL, error: null } }),
  );
  await page.route("**/api/remote/access", (route) =>
    route.fulfill({
      json: {
        enabled: true,
        pairing_expires_at: expires,
        sessions: [
          {
            id: "demo-phone",
            label: "iPhone · Safari",
            created_at: "2026-11-20T08:12:00Z",
            last_used_at: "2026-11-27T07:45:00Z",
          },
        ],
      },
    }),
  );
  await page.route("**/api/remote/pairing", (route) =>
    route.fulfill({
      json: {
        path: "/api/remote/pair?code=demo-only-not-a-real-code",
        url: `${TUNNEL_URL}/api/remote/pair?code=demo-only-not-a-real-code`,
        expires_at: expires,
        qr_svg: QR_SVG,
      },
    }),
  );
}

module.exports = { mockMobileAccess };
