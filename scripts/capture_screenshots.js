// Capture the documentation screenshots against a seeded demo instance.
//
//   node scripts/capture_screenshots.js [scene ...]
//
// Full loop, from the repo root:
//
//   rm -rf .demo/demo.db* .demo/data .demo/skills
//   PRECURSOR_DATABASE_URL="sqlite+aiosqlite:///$PWD/.demo/demo.db" \
//   PRECURSOR_DATA_DIR="$PWD/.demo/data" \
//   PRECURSOR_SKILLS_DIR="$PWD/.demo/skills" \
//     uv run python scripts/seed_demo.py
//   ./scripts/demo_server.sh &          # serves the demo on :8899 as "Guest"
//   node scripts/capture_screenshots.js
//
// Every shot is written twice — `foo.png` (light) and `foo-dark.png` (dark) —
// into website/public/screenshots/ at deviceScaleFactor 2, matching the
// convention the <Screenshot> component expects (see website/features/AGENTS.md).
//
// The target must be the DEMO instance, whose persona resolves to "Guest / Not
// connected" because its server runs with `gh` off PATH. If :8899 is occupied,
// launch with DEMO_PORT=<port> and capture with DEMO_BASE=http://127.0.0.1:<port>.

let chromium;
try {
  ({ chromium } = require("playwright"));
} catch {
  console.error(
    "Playwright is not installed. From the repo root:\n" +
      "  mkdir -p .demo && cd .demo && npm i -D playwright && npx playwright install chromium\n" +
      "then re-run this script from the repo root.",
  );
  process.exit(1);
}
const path = require("path");
const fs = require("fs");
const { BASE, prepareDemoPage, mockAgentRuntime } = require("./demo_browser");

const OUT = path.resolve(__dirname, "..", "website", "public", "screenshots");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Freeze motion so the light and dark variants line up pixel for pixel.
const STABILISE_CSS = `
  *, *::before, *::after {
    animation: none !important;
    transition: none !important;
    caret-color: transparent !important;
  }
`;

async function shot(page, name, clip) {
  fs.mkdirSync(OUT, { recursive: true });
  const file = path.join(OUT, name);
  await page.screenshot({ path: file, clip });
  console.log("  ✓", path.relative(process.cwd(), file));
}

/** Bounding box of a selector, padded and clamped to the viewport. */
async function clipOf(page, selector, pad = 0) {
  const vp = page.viewportSize();
  const box = await page.locator(selector).first().boundingBox();
  if (!box) return undefined;
  const x = Math.max(0, Math.floor(box.x - pad));
  const y = Math.max(0, Math.floor(box.y - pad));
  return {
    x,
    y,
    width: Math.min(vp.width - x, Math.ceil(box.width + pad * 2)),
    height: Math.min(vp.height - y, Math.ceil(box.height + pad * 2)),
  };
}

/**
 * Clip to the app's rendered content, trimming the dead space below the last
 * element — the app scrolls an inner container, so a tall viewport otherwise
 * leaves a large empty band under a short page.
 */
async function clipToContent(page, pad = 20) {
  const vp = page.viewportSize();
  const bottom = await page.evaluate((vh) => {
    const main = document.querySelector("main") || document.body;
    let max = 0;
    main.querySelectorAll("*").forEach((el) => {
      const r = el.getBoundingClientRect();
      // Skip layout containers that simply stretch to the viewport — they say
      // nothing about where the *content* ends.
      if (r.height >= vh * 0.85) return;
      if (r.width > 0 && r.height > 0 && r.bottom > max) max = r.bottom;
    });
    return max;
  }, vp.height);
  return {
    x: 0,
    y: 0,
    width: vp.width,
    height: Math.min(vp.height, Math.ceil(bottom + pad)),
  };
}

/** Open the settings modal on a named tab. */
async function openSettings(page, tab) {
  await page.locator('[data-tooltip="Settings"]').first().click();
  await page.waitForSelector("text=Appearance", { timeout: 10000 });
  await sleep(500);
  const byRole = page.getByRole("button", { name: tab, exact: true }).first();
  if (await byRole.count()) await byRole.click().catch(() => {});
  else await page.locator(`text="${tab}"`).first().click().catch(() => {});
  await sleep(800);
}

// Fixed release data for the Plugins shots: the panel asks PyPI and GitHub for
// the newest releases, and a screenshot must not depend on either answering —
// or on what has shipped since it was taken.
const KANBAN_RELEASES = ["2026.9.2", "2026.9.1", "2026.9.0"];
const KANBAN_WHEEL = (v) =>
  `https://github.com/lrivallain/precursor-kanban/releases/download/v${v}/precursor_kanban-${v}-py3-none-any.whl`;

// The installed plugin the "plugins" shot shows — a fixture, so neither shot
// depends on what happens to be installed in the environment serving the demo.
const KANBAN_INSTALLED = {
  id: "kanban",
  distribution: "precursor-kanban",
  version: "2026.9.1",
  summary: "GitHub Projects v2 kanban board for Precursor.",
  homepage: "https://github.com/lrivallain/precursor-kanban",
  enabled: true,
  error: null,
  entry: "/api/plugins/kanban/assets/index.js",
  sections: [{ id: "kanban", title: "Kanban" }],
  extensions: [{ id: "kanban", kind: "section", slot: "app.section", title: "Kanban" }],
  settings_pages: [],
  routes: ["/api/github/projects"],
  mcp_servers: [{ name: "kanban.board", title: "Kanban board" }],
  source: {
    kind: "github",
    distribution: "precursor-kanban",
    repository: "lrivallain/precursor-kanban",
    specifier: "",
    pinned: false,
  },
};

async function stubPluginReleases(page, { installed }) {
  const plugins = installed ? [KANBAN_INSTALLED] : [];
  await page.route("**/api/plugins/installed", (route) => route.fulfill({ json: plugins }));
  await page.route("**/api/plugins/catalog", async (route) => {
    const entries = await (await route.fetch()).json();
    await route.fulfill({
      json: entries.map((e) => ({
        ...e,
        installed: installed && e.id === "kanban",
        enabled: installed && e.id === "kanban",
        installed_version: installed && e.id === "kanban" ? KANBAN_INSTALLED.version : null,
      })),
    });
  });
  await page.route("**/api/plugins/versions?*", async (route) => {
    const spec = new URL(route.request().url()).searchParams.get("package") || "";
    const github = spec.includes("github.com");
    const requirement = (v) =>
      github ? `precursor-kanban @ ${KANBAN_WHEEL(v)}` : `precursor-kanban==${v}`;
    await route.fulfill({
      json: {
        source: {
          kind: github ? "github" : "pypi",
          distribution: "precursor-kanban",
          repository: github ? "lrivallain/precursor-kanban" : null,
          specifier: "",
          pinned: false,
        },
        latest: KANBAN_RELEASES[0],
        latest_requirement: github
          ? requirement(KANBAN_RELEASES[0])
          : `precursor-kanban>=${KANBAN_RELEASES[0]}`,
        versions: KANBAN_RELEASES.map((v) => ({
          version: v,
          prerelease: false,
          published_at: null,
          tag: `v${v}`,
          requirement: requirement(v),
        })),
      },
    });
  });
  await page.route("**/api/plugins/updates*", async (route) => {
    await route.fulfill({
      json: plugins.map((p) => ({
          id: p.id,
          distribution: p.distribution,
          installed_version: p.version,
          source: p.source,
          latest: KANBAN_RELEASES[0],
          update_available: p.version !== KANBAN_RELEASES[0],
        upgrade_requirement: `precursor-kanban @ ${KANBAN_WHEEL(KANBAN_RELEASES[0])}`,
        error: null,
      })),
    });
  });
}

// --------------------------------------------------------------------------
// Scenes. `viewport` is per scene because these surfaces have very different
// natural heights; the clip trims whatever is left over.
// --------------------------------------------------------------------------
const scenes = {
  home: {
    viewport: { width: 1440, height: 1000 },
    async go(page) {
      await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
      return undefined;
    },
  },

  topics: {
    viewport: { width: 1440, height: 1000 },
    async go(page) {
      await page.goto(`${BASE}/topics/onboarding-checklist`, { waitUntil: "networkidle" });
      await page.getByRole("button", { name: "Topic settings", exact: true }).waitFor();
      return undefined;
    },
  },

  chats: {
    viewport: { width: 1440, height: 1000 },
    async go(page) {
      await page.goto(`${BASE}/chats/regex-for-semver-tags`, { waitUntil: "networkidle" });
      await page.getByText("Regex for semver tags", { exact: true }).first().waitFor();
      return undefined;
    },
  },

  live: {
    viewport: { width: 1440, height: 1000 },
    async go(page) {
      await page.goto(`${BASE}/live/weekly-platform-sync`, { waitUntil: "networkidle" });
      await page.getByText("Let's review the latency regression and agree on next steps.", { exact: true }).waitFor();
      return undefined;
    },
  },

  workspaces: {
    viewport: { width: 1440, height: 1000 },
    async go(page) {
      await page.goto(`${BASE}/ws/design-notes/README.md`, { waitUntil: "networkidle" });
      await page.getByRole("heading", { name: "Release checklist", exact: true }).waitFor();
      return undefined;
    },
  },

  "agents-setup": {
    viewport: { width: 1200, height: 800 },
    async go(page) {
      await mockAgentRuntime(page);
      await page.goto(`${BASE}/agents`, { waitUntil: "networkidle" });
      await page.getByRole("button", { name: "Install the Copilot CLI (~90 MB)", exact: true }).waitFor();
      await page.locator('[data-tooltip^="Guest"][data-tooltip*="GitHub not connected"]').waitFor();
      return clipOf(page, "main .max-w-xl", 16);
    },
  },

  "agents-overview": {
    viewport: { width: 1440, height: 1000 },
    async go(page) {
      await page.goto(`${BASE}/agents`, { waitUntil: "networkidle" });
      await page.getByRole("heading", { name: "Agent fleet" }).waitFor();
      await page.locator("main").getByRole("region", { name: "Standalone agents", exact: true }).waitFor();
      await page.locator("main").getByRole("region", { name: "Workflow agents", exact: true }).waitFor();
      await page.locator('[data-tooltip^="Guest"][data-tooltip*="GitHub not connected"]').waitFor();
      return undefined;
    },
  },

  agents: {
    viewport: { width: 1440, height: 1000 },
    async go(page) {
      const response = await page.request.get(`${BASE}/api/agents`);
      const agents = await response.json();
      const agent = agents.find((item) => item.title === "Digest writer");
      if (!agent) throw new Error("Seed the demo Digest writer before capturing agents.");
      await page.goto(`${BASE}/agents/${agent.public_id}`, { waitUntil: "networkidle" });
      await page.getByRole("button", { name: "Run agent", exact: true }).waitFor();
      await page.getByText("The draft is ready for review before publishing.").waitFor();
      await page.locator('[data-tooltip^="Guest"][data-tooltip*="GitHub not connected"]').waitFor();
      await sleep(800);
      return undefined;
    },
  },

  "workflows-overview": {
    viewport: { width: 1440, height: 1000 },
    async go(page) {
      await page.goto(`${BASE}/workflows`, { waitUntil: "networkidle" });
      await page.getByRole("heading", { name: "Workflows", exact: true }).waitFor();
      await page.getByRole("heading", { name: "Weekly release digest", exact: true }).waitFor();
      return undefined;
    },
  },

  // The workflow detail board: the step strip with all four step kinds.
  workflows: {
    viewport: { width: 1440, height: 1750 },
    async go(page) {
      await page.goto(`${BASE}/workflows/1/run/latest`, { waitUntil: "networkidle" });
      await page.waitForSelector("text=Weekly release digest", { timeout: 20000 });
      await sleep(1500);
      // Stop above the run trace — that has its own shot.
      const traceTop = await page.evaluate(() => {
        const b = [...document.querySelectorAll("button")].find((x) =>
          /run trace/i.test(x.innerText || ""),
        );
        return b ? b.getBoundingClientRect().top : null;
      });
      const vp = page.viewportSize();
      return { x: 0, y: 0, width: vp.width, height: Math.ceil(traceTop ?? 600) - 8 };
    },
  },

  // The run trace: one row per attempt, including the gate's FAIL that sent
  // step 2 back for an `attempt 2`, then its PASS.
  "workflow-run-trace": {
    viewport: { width: 1440, height: 1750 },
    async go(page) {
      await page.goto(`${BASE}/workflows/1/run/latest`, { waitUntil: "networkidle" });
      await page.waitForSelector("text=Weekly release digest", { timeout: 20000 });
      await sleep(1500);
      const box = await page.evaluate(() => {
        const b = [...document.querySelectorAll("button")].find((x) =>
          /run trace/i.test(x.innerText || ""),
        );
        const state = [...document.querySelectorAll("button")].find((x) =>
          /pipeline state/i.test(x.innerText || ""),
        );
        return b ? { top: b.getBoundingClientRect().top, bottom: state ? state.getBoundingClientRect().top : null } : null;
      });
      const vp = page.viewportSize();
      const y = Math.max(0, Math.floor(box.top) - 12);
      const height = Math.ceil((box.bottom ?? vp.height) - y) - 4;
      return { x: 60, y, width: vp.width - 60, height };
    },
  },

  // The Workflows gallery — two pipelines, for the import/export page.
  transfer: {
    viewport: { width: 1440, height: 1000 },
    async go(page) {
      await page.goto(`${BASE}/workflows`, { waitUntil: "networkidle" });
      await page.waitForSelector("text=Weekly release digest", { timeout: 20000 });
      await sleep(1200);
      return clipToContent(page);
    },
  },

  // A scheduled topic with its recurrence editor open — the control scheduled
  // topics, agents and workflows all share. The demo schedule carries two rules,
  // so the shot shows a schedule that combines cadences.
  scheduler: {
    viewport: { width: 1440, height: 1240 },
    async go(page) {
      await page.goto(`${BASE}/topics/weekly-engineering-digest`, { waitUntil: "networkidle" });
      await page.waitForSelector("text=Weekly engineering digest", { timeout: 20000 });
      await sleep(1200);
      // The topic's settings panel carries the schedule section.
      const gear = page.locator('[data-tooltip="Topic settings"]').first();
      if (await gear.count()) {
        await gear.click().catch(() => {});
        await sleep(1400);
      }
      // The schedule section sits below the fold of a long settings panel, so
      // bring it into view and frame the panel around it.
      const anchor = page.locator("text=Add another schedule").first();
      if (await anchor.count()) {
        await anchor.scrollIntoViewIfNeeded().catch(() => {});
        await sleep(900);
      }
      return clipOf(page, 'text="Topic settings" >> xpath=ancestor::div[contains(@class,"fixed")]');
    },
  },

  // Settings → Skills, listing the demo SKILL.md fixtures found on disk.
  "skills-memory": {
    viewport: { width: 1440, height: 1000 },
    async go(page) {
      await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
      await openSettings(page, "Skills");
      return clipOf(page, "div.fixed.inset-0 > div, [role=dialog]", 0);
    },
  },

  // Settings → Plugins: the installed packages, what each contributes, and
  // where each upgrades from. The installed plugin and its release lookups are
  // fixtures, so the shot depends neither on the environment nor the network.
  plugins: {
    viewport: { width: 1440, height: 1000 },
    async go(page) {
      await stubPluginReleases(page, { installed: true });
      await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
      await openSettings(page, "Plugins");
      await page.locator("text=2026.9.2 available").first().waitFor({ timeout: 10000 }).catch(() => {});
      await sleep(400);
      return clipOf(page, "div.fixed.inset-0 > div, [role=dialog]", 0);
    },
  },

  // The same panel, framed on the bundled catalogue — the "Available" list you
  // install from. Nothing counts as installed here, whatever the environment
  // has, so the catalogued entry stays in Available. The shot opens the source
  // and version picker on GitHub and reveals the resulting install command,
  // since that is the state a reader without the in-app installer enabled will
  // actually meet.
  "plugins-catalog": {
    viewport: { width: 1440, height: 1000 },
    async go(page) {
      await stubPluginReleases(page, { installed: false });
      await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
      await openSettings(page, "Plugins");
      const card = page.locator("li", { hasText: "Kanban board" }).first();
      const options = card.getByRole("button", { name: /Choose source and version/ });
      if (await options.count()) {
        await options.click();
        await card.getByRole("button", { name: "GitHub", exact: true }).click();
        await page.locator("text=on GitHub").first().waitFor({ timeout: 10000 }).catch(() => {});
      }
      const command = card.getByRole("button", { name: "Install command" });
      if (await command.count()) {
        await command.click();
        await sleep(600);
      }
      return clipOf(page, "div.fixed.inset-0 > div, [role=dialog]", 0);
    },
  },

  // Settings → System, where the command-runner sandbox is configured.
  "command-runner": {
    viewport: { width: 1440, height: 1000 },
    async go(page) {
      await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
      await openSettings(page, "System");
      return clipOf(page, "div.fixed.inset-0 > div, [role=dialog]", 0);
    },
  },

  // Settings → Appearance: theme toggle + the reading-font picker (dyslexia /
  // low-vision friendly options).
  accessibility: {
    viewport: { width: 1440, height: 1000 },
    async go(page) {
      await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
      await openSettings(page, "Appearance");
      return clipOf(page, "div.fixed.inset-0 > div, [role=dialog]", 0);
    },
  },

  // Phone layout: a conversation with the whole screen to itself. `isMobile`
  // makes Chromium report `hover: none` / `pointer: coarse`, which is what
  // reveals the touch affordances, so it can't be faked with a narrow viewport.
  "mobile-chat": {
    viewport: { width: 390, height: 844 },
    context: { isMobile: true, hasTouch: true },
    async go(page) {
      await page.goto(`${BASE}/chats/regex-for-semver-tags`, {
        waitUntil: "networkidle",
      });
      await sleep(1400);
      // Whole-viewport shot: the point is the phone screen itself.
      return undefined;
    },
  },

  // The topic summary panel with a proposal open for per-change review.
  "topic-summary": {
    viewport: { width: 1440, height: 1250 },
    async go(page) {
      await page.addInitScript(() => localStorage.setItem("precursor:topic-summary:height", "340"));
      await page.goto(`${BASE}/topics/release-2026-8`, { waitUntil: "networkidle" });
      const expand = page.getByRole("button", { name: "Expand topic summary", exact: true });
      if (await expand.isVisible()) await expand.click();
      await page.getByRole("button", { name: "Accept change 1", exact: true }).waitFor();
      await page.mouse.move(0, 0);
      await sleep(1200);
      // Clip to the panel itself, which keeps the sidebar persona footer out.
      return clipOf(page, '[data-summary-panel]', 0);
    },
  },

  "topic-summary-editing": {
    viewport: { width: 1440, height: 1250 },
    async go(page) {
      await page.addInitScript(() => localStorage.setItem("precursor:topic-summary:height", "280"));
      await page.goto(`${BASE}/topics/release-2026-8`, { waitUntil: "networkidle" });
      const expand = page.getByRole("button", { name: "Expand topic summary", exact: true });
      if (await expand.isVisible()) await expand.click();
      await page.getByRole("button", { name: "Edit summary", exact: true }).click();
      await page.getByRole("textbox", { name: "Summary markdown" }).waitFor();
      await page.mouse.move(0, 0);
      return clipOf(page, "[data-summary-panel]");
    },
  },

  "topic-summary-collapsed": {
    viewport: { width: 1440, height: 1000 },
    async go(page) {
      // The seeded issue is fictional; don't show an offline GitHub error in the header.
      await page.route(/\/api\/topics\/\d+\/summary(?:\?.*)?$/, (route) => route.fulfill({
        json: {
          repo: "acme/widget-platform",
          issue_number: 482,
          issue_title: "Release preparation",
          issue_state: "open",
          issue_url: null,
          labels: [],
          summary: "QA is running; signing and migration notes are the remaining actions.",
          model: "demo-fixture",
          fetched_at: new Date().toISOString(),
          cached: true,
        },
      }));
      await page.goto(`${BASE}/topics/release-2026-8`, { waitUntil: "networkidle" });
      const collapse = page.getByRole("button", { name: "Collapse topic summary", exact: true });
      if (await collapse.isVisible()) await collapse.click();
      await page.getByRole("button", { name: "Expand topic summary", exact: true }).waitFor();
      await page.mouse.move(0, 0);
      await sleep(400);
      const clip = await clipOf(page, "[data-summary-panel]");
      // Include the topic header and the start of the transcript, not the persona.
      return {
        ...clip,
        y: Math.max(0, clip.y - 56),
        width: page.viewportSize().width - clip.x,
        height: 196,
      };
    },
  },

  // The same screen with the navigation drawer pulled out over it.
  "mobile-drawer": {
    viewport: { width: 390, height: 844 },
    context: { isMobile: true, hasTouch: true },
    async go(page) {
      await page.goto(`${BASE}/chats/regex-for-semver-tags`, {
        waitUntil: "networkidle",
      });
      await sleep(1000);
      await page.getByRole("button", { name: "Open navigation" }).click();
      await sleep(700);
      return undefined;
    },
  },
};

async function run(names) {
  const browser = await chromium.launch();
  try {
    for (const name of names) {
      const scene = scenes[name];
      if (!scene) {
        console.error(`unknown scene: ${name}`);
        continue;
      }
      console.log(`\n${name}`);
      for (const theme of ["light", "dark"]) {
        const ctx = await browser.newContext({
          viewport: scene.viewport,
          deviceScaleFactor: 2,
          colorScheme: theme,
          reducedMotion: "reduce",
          ...(scene.context ?? {}),
        });
        const page = await ctx.newPage();
        await prepareDemoPage(page);
        const clip = await scene.go(page, theme);
        await page.addStyleTag({ content: STABILISE_CSS }).catch(() => {});
        await sleep(200);
        await shot(page, theme === "dark" ? `${name}-dark.png` : `${name}.png`, clip);
        await ctx.close();
      }
    }
  } finally {
    await browser.close();
  }
}

const requested = process.argv.slice(2);
run(requested.length ? requested : Object.keys(scenes)).catch((e) => {
  console.error(e);
  process.exit(1);
});
