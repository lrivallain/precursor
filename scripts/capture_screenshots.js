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

// The seeded "Onboarding guide writer": one run whose result was refined twice.
async function gotoRefinedAgent(page) {
  const response = await page.request.get(`${BASE}/api/agents`);
  const agents = await response.json();
  const agent = agents.find((item) => item.title === "Onboarding guide writer");
  if (!agent) throw new Error("Seed the demo Onboarding guide writer before capturing it.");
  await page.goto(`${BASE}/agents/${agent.public_id}`, { waitUntil: "networkidle" });
}

// --------------------------------------------------------------------------
// Scenes. `viewport` is per scene because these surfaces have very different
// natural heights; the clip trims whatever is left over.
// --------------------------------------------------------------------------
// The OpenAI-compatible endpoint as a user sees it once switched on. The key is
// an obvious placeholder: a screenshot must never carry a working credential.
const OPENAI_ENDPOINT_SETTINGS = {
  llm_provider: "github_copilot",
  openai_proxy_enabled: true,
  openai_proxy_url: "http://127.0.0.1:8000/api/openai/v1",
  openai_proxy_key: "sk-precursor-demo-0000000000000000000000000000",
  openai_proxy_available: true,
  openai_proxy_unavailable_reason: null,
};
const OPENAI_ENDPOINT_MODELS = [
  ["claude-sonnet-5", "Claude Sonnet 5", "Anthropic"],
  ["gemini-3.6-flash", "Gemini 3.6 Flash", "Google"],
  ["gpt-5-mini", "GPT-5 mini", "OpenAI"],
  ["gpt-5.5", "GPT-5.5", "OpenAI"],
].map(([id, name, publisher]) => ({ id, name, publisher, summary: "", tags: [] }));

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

  // A reply's collapsed "Thinking" area, opened — just the exchange, so the
  // sidebar and its persona footer stay out of the shot.
  thinking: {
    viewport: { width: 1280, height: 900 },
    async go(page) {
      await page.goto(`${BASE}/topics/search-latency-regression`, { waitUntil: "networkidle" });
      await page.locator("button[aria-expanded]", { hasText: "Thinking" }).first().click();
      await page.mouse.move(0, 0);
      await sleep(400);
      const prompt = await page.getByText("p95 went from 180ms", { exact: false }).first().boundingBox();
      const label = await page.getByText("Assistant", { exact: true }).first().boundingBox();
      const answer = await page.getByText("recover most of it", { exact: false }).first().boundingBox();
      if (!prompt || !label || !answer) throw new Error("Seed the search-latency topic first.");
      const x = Math.floor(label.x - 24);
      const y = Math.floor(prompt.y - 44);
      return {
        x,
        y,
        width: Math.ceil(prompt.x + prompt.width + 36 - x),
        height: Math.ceil(answer.y + answer.height + 44 - y),
      };
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

  // The Summary tab's Generate panel: the source, the templates (built-ins
  // plus one of our own, a `*.summary.yaml` definition file) and the language.
  "live-summary-templates": {
    viewport: { width: 1440, height: 1000 },
    async go(page) {
      await page.goto(`${BASE}/live/weekly-platform-sync`, { waitUntil: "networkidle" });
      await page.getByRole("button", { name: /^Summary/ }).first().click();
      await page.getByRole("button", { name: /Regenerate|Generate/ }).first().click();
      await page.getByRole("radio", { name: /Customer call notes/ }).waitFor();
      await page.mouse.move(0, 0);
      await sleep(400);
      // The section (not the sidebar's persona footer), cut below the panel.
      const clip = await clipOf(page, "[data-live-summary]");
      const panel = await page.getByRole("dialog", { name: "Generate summary" }).boundingBox();
      const bottom = panel ? Math.ceil(panel.y + panel.height + 16) : clip.y + 560;
      return clip && { ...clip, height: Math.min(clip.height, bottom - clip.y) };
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

  // The same file in Monaco. Left unfocused so no blinking cursor differs
  // between the light and dark shots.
  "workspaces-editor": {
    viewport: { width: 1440, height: 1000 },
    async go(page) {
      await page.goto(`${BASE}/ws/design-notes/README.md`, { waitUntil: "networkidle" });
      await page.getByRole("button", { name: "Edit", exact: true }).click();
      await page.locator(".monaco-editor .view-line", { hasText: "Release checklist" }).waitFor();
      await sleep(500);
      return undefined;
    },
  },

  // A workflow definition whose first step names a missing agent file: the
  // check's finding, underlined, with its hover and the list below.
  "workspaces-definitions": {
    viewport: { width: 1440, height: 1000 },
    async go(page) {
      await page.goto(`${BASE}/ws/definitions/workflows/weekly-digest.workflow.yaml`, {
        waitUntil: "networkidle",
      });
      await page.getByLabel("Definition check").getByText("1 error").waitFor();
      await page.locator(".monaco-editor .squiggly-error").first().waitFor();
      await page
        .locator(".monaco-editor .view-line", { hasText: "change-collector" })
        .hover({ position: { x: 220, y: 6 } });
      await page.locator(".monaco-hover", { hasText: "no agent file" }).first().waitFor();
      await sleep(400);
      return undefined;
    },
  },

  // The git workspace's Changes tab, with one file's diff against HEAD. The
  // pane is chosen through the stored preference, as a returning user has it.
  "workspaces-changes": {
    viewport: { width: 1440, height: 1000 },
    async go(page) {
      await page.addInitScript(() =>
        localStorage.setItem("precursor:workspace:leftTab", "changes"),
      );
      await page.goto(`${BASE}/ws/handbook`, { waitUntil: "networkidle" });
      // The diff gets the assistant's width, to show side by side.
      await page.getByRole("button", { name: "Hide assistant" }).click();
      const changes = page.getByLabel("Changes", { exact: true });
      await changes.getByRole("button", { name: /^on-call\.md,/ }).click();
      await page.locator(".monaco-diff-editor .view-line", { hasText: "error budget" }).first().waitFor();
      await page.getByText("3 of 3 to commit").waitFor();
      await changes.getByLabel("Commit message").fill("Document handoffs and incident reviews");
      await page.mouse.move(1200, 900);
      await sleep(600);
      return undefined;
    },
  },

  // The History tab: a commit opened to its files, one of them compared with
  // the commit's parent.
  "workspaces-history": {
    viewport: { width: 1440, height: 1000 },
    async go(page) {
      await page.addInitScript(() =>
        localStorage.setItem("precursor:workspace:leftTab", "history"),
      );
      await page.goto(`${BASE}/ws/handbook`, { waitUntil: "networkidle" });
      await page.getByRole("button", { name: "Hide assistant" }).click();
      const history = page.getByLabel("History", { exact: true });
      await history.getByRole("button", { name: /Page owners sooner/ }).click();
      await history.getByRole("button", { name: /^on-call\.md,/ }).click();
      await page.locator(".monaco-diff-editor .view-line", { hasText: "15 minutes" }).first().waitFor();
      await page.mouse.move(1200, 900);
      await sleep(600);
      return undefined;
    },
  },

  // The git bar's branch picker, open: local branches (one unpublished) and
  // the ones only the remote has.
  "workspaces-branches": {
    viewport: { width: 1440, height: 1000 },
    async go(page) {
      await page.goto(`${BASE}/ws/handbook/README.md`, { waitUntil: "networkidle" });
      await page.getByRole("heading", { name: "Team handbook", exact: true }).waitFor();
      await page.getByRole("button", { name: /Switch or create a branch/ }).click();
      await page.getByRole("dialog", { name: "Branches" }).getByText("On the remote").waitFor();
      await page.mouse.move(1200, 900);
      await sleep(400);
      return undefined;
    },
  },

  // A merge stopped on a conflict: the banner, the Changes tab's conflict,
  // and the file in the editor with Accept lenses above the block. The first
  // (light) run does the Pull and the Merge; the dark one finds it in progress.
  "workspaces-conflict": {
    viewport: { width: 1440, height: 1000 },
    async go(page) {
      await page.addInitScript(() =>
        localStorage.setItem("precursor:workspace:leftTab", "changes"),
      );
      await page.goto(`${BASE}/ws/runbooks`, { waitUntil: "networkidle" });
      await page.getByRole("button", { name: "Hide assistant" }).click();
      const banner = page.getByText("Merge in progress");
      if (!(await banner.isVisible())) {
        await page.getByRole("button", { name: "Pull" }).click();
        await page.getByRole("button", { name: "Merge remote changes" }).click();
        await banner.waitFor();
      }
      const changes = page.getByLabel("Changes", { exact: true });
      await changes.getByRole("button", { name: /^escalation\.md, conflict/ }).click();
      await page.getByRole("button", { name: "Resolve in the editor" }).click();
      await page.locator(".monaco-editor .codelens-decoration", { hasText: "Accept current" }).first().waitFor();
      await page.mouse.move(1200, 900);
      await sleep(600);
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

  // An agent's answer with the thinking that led to it, opened — just the
  // exchange, so the sidebar and its persona footer stay out of the shot.
  "agents-thinking": {
    viewport: { width: 1440, height: 1000 },
    async go(page) {
      const response = await page.request.get(`${BASE}/api/agents`);
      const agents = await response.json();
      const agent = agents.find((item) => item.title === "Digest writer");
      if (!agent) throw new Error("Seed the demo Digest writer before capturing agents.");
      await page.goto(`${BASE}/agents/${agent.public_id}`, { waitUntil: "networkidle" });
      await page.getByText("The draft is ready for review before publishing.").waitFor();
      await page.locator("button[aria-expanded]", { hasText: "Thinking" }).first().click();
      await page.mouse.move(0, 0);
      await sleep(400);
      const node = (text) =>
        page
          .getByText(text)
          .first()
          .locator("xpath=ancestor::div[contains(@class, 'group/node')][1]")
          .boundingBox();
      const prompt = await node("Draft this week's engineering digest");
      const answer = await node("The draft is ready for review before publishing.");
      if (!prompt || !answer) throw new Error("Seed the demo Digest writer first.");
      const x = Math.floor(Math.min(prompt.x, answer.x) - 24);
      const y = Math.floor(prompt.y - 10);
      return {
        x,
        y,
        width: Math.ceil(Math.max(prompt.x + prompt.width, answer.x + answer.width) + 24 - x),
        height: Math.ceil(answer.y + answer.height + 24 - y),
      };
    },
  },

  // A deliverable refined over two follow-ups: the Result tab opens on v3,
  // with the earlier versions on the rail above it.
  "agents-results": {
    viewport: { width: 1440, height: 1000 },
    async go(page) {
      await gotoRefinedAgent(page);
      await page.getByRole("tab", { name: /Result/ }).waitFor();
      await page.getByRole("heading", { name: "First-week checklist", exact: true }).waitFor();
      await page.locator('[data-tooltip^="Guest"][data-tooltip*="GitHub not connected"]').waitFor();
      await sleep(800);
      return undefined;
    },
  },

  // The same agent's Activity tab: each finished turn folded above its answer,
  // and each answer pointing at the result version it published.
  "agents-activity": {
    viewport: { width: 1440, height: 1000 },
    async go(page) {
      await gotoRefinedAgent(page);
      await page.getByRole("tab", { name: /Activity/ }).click();
      await page.getByRole("button", { name: /Worked for/ }).first().waitFor();
      await page.evaluate(() => {
        const panel = document.querySelector("#agent-panel-activity");
        if (panel) panel.scrollTop = panel.scrollHeight;
      });
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

  // ⌘K ranked by Precursor IQ: words matched out of order, title hits first,
  // then the most relevant passages across topics, chats and live sessions.
  "iq-palette": {
    viewport: { width: 1280, height: 860 },
    async go(page) {
      await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
      await page.keyboard.press("ControlOrMeta+k");
      await page.waitForSelector("#command-palette-input");
      await page.fill("#command-palette-input", "retries latency regression");
      await page.locator("text=Ask Precursor").first().waitFor({ timeout: 10000 });
      await sleep(700);
      return clipOf(page, "[role=dialog]", 0);
    },
  },

  // Ask mode: a cited answer grounded on the same index. The demo has no model,
  // so the answer text is a fixture built from the real retrieved sources.
  "iq-ask": {
    viewport: { width: 1280, height: 860 },
    async go(page) {
      await page.route("**/api/iq/ask", async (route) => {
        const { question } = JSON.parse(route.request().postData() || "{}");
        const found = await (
          await page.request.get(
            `${BASE}/api/iq/retrieve?q=${encodeURIComponent(question)}&limit=5`,
          )
        ).json();
        await route.fulfill({
          json: {
            question,
            model: "claude-sonnet-5",
            answer:
              "The latency regression came from **retries amplifying load** when " +
              "the search backend slowed down [^1]. The weekly sync agreed to cap " +
              "retries and add jittered backoff before the next release [^2], and " +
              "the follow-up is tracked in the release notes [^3].",
            citations: found.hits.slice(0, 3),
            sources: found.hits,
          },
        });
      });
      await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
      await page.keyboard.press("ControlOrMeta+k");
      await page.waitForSelector("#command-palette-input");
      await page.fill("#command-palette-input", "why did search latency regress?");
      await page.locator("text=Ask Precursor").first().waitFor({ timeout: 10000 });
      await page.keyboard.press("Tab");
      await page.locator("text=Precursor answer").first().waitFor({ timeout: 10000 });
      await page.getByText("Sources", { exact: true }).waitFor({ timeout: 10000 });
      await sleep(700);
      return clipOf(page, "[role=dialog]", 0);
    },
  },

  // The IQ section: one question box, a cited answer and the sources it rests
  // on. As in Ask mode, the answer is a fixture over the real retrieved hits.
  "iq-section": {
    viewport: { width: 1440, height: 900 },
    async go(page) {
      await page.route("**/api/iq/ask", async (route) => {
        const { question } = JSON.parse(route.request().postData() || "{}");
        const found = await (
          await page.request.get(`${BASE}/api/iq/retrieve?q=${encodeURIComponent(question)}&limit=8`)
        ).json();
        // Cite the passages that actually carry each claim, as a model would.
        const n = (field) => found.hits.find((h) => h.field === field)?.id ?? 1;
        const [decision, cause, owner] = [n("summary"), n("transcript"), n("insight")];
        await route.fulfill({
          json: {
            question,
            model: "claude-sonnet-5",
            answer:
              `The team agreed to **cap retries and add jitter to the backoff** [^${decision}], ` +
              `because retries amplified load whenever the gateway slowed down [^${cause}]. ` +
              "Sam owns the follow-up: a bounded-retry regression test and an update " +
              `to the release checklist [^${owner}].`,
            citations: [decision, cause, owner].map((id) => found.hits.find((h) => h.id === id)),
            sources: found.hits,
          },
        });
      });
      await page.goto(`${BASE}/iq`, { waitUntil: "networkidle" });
      await page.fill('input[aria-label="Question for Precursor IQ"]', "What did we decide about the latency regression?");
      await page.keyboard.press("Enter");
      await page.getByText("Sources", { exact: true }).waitFor({ timeout: 15000 });
      await sleep(600);
      return clipToContent(page);
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

  // Settings → Model: the OpenAI-compatible endpoint, switched on. The Guest
  // demo has no provider to relay to, so the switch, key and catalogue are
  // fixtures — never a real key.
  "openai-endpoint": {
    // Tall enough for the whole Model tab, so the card isn't cut by the footer.
    viewport: { width: 1440, height: 1800 },
    async go(page) {
      await page.route("**/api/settings", async (route) => {
        if (route.request().method() !== "GET") return route.fallback();
        const response = await route.fetch();
        const settings = await response.json();
        await route.fulfill({ response, json: { ...settings, ...OPENAI_ENDPOINT_SETTINGS } });
      });
      await page.route("**/api/llm/models*", (route) =>
        route.fulfill({ json: OPENAI_ENDPOINT_MODELS }),
      );
      await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
      await openSettings(page, "Model");
      const heading = page.getByRole("heading", { name: "OpenAI-compatible endpoint" });
      // The innermost div holding the heading is the card itself.
      const card = page.locator("div", { has: heading }).last();
      await card.evaluate((el) => el.scrollIntoView({ block: "center" }));
      await sleep(400);
      const box = await card.boundingBox();
      if (!box) return undefined;
      const pad = 16;
      return {
        x: Math.max(0, Math.floor(box.x - pad)),
        y: Math.max(0, Math.floor(box.y - pad)),
        width: Math.ceil(box.width + pad * 2),
        height: Math.ceil(box.height + pad * 2),
      };
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

  // A compacted topic: the dimmed turns above the marker, the summary opened,
  // and the stats panel's context bar + Compact button. Clipped right of the
  // sidebar so its persona footer stays out.
  "context-compression": {
    viewport: { width: 1440, height: 1000 },
    async go(page) {
      await page.addInitScript(() => localStorage.setItem("precursor:chat-stats:collapsed", "0"));
      await page.goto(`${BASE}/topics/hosting-options-review`, { waitUntil: "networkidle" });
      await page.getByRole("button", { name: "Show summary" }).click();
      await page.getByText("Next turn (estimate)").waitFor();
      await page.mouse.move(0, 0);
      await sleep(600);
      const main = await page.locator("main").first().boundingBox();
      if (!main) throw new Error("Seed the hosting-options-review topic first.");
      return { x: Math.floor(main.x), y: 0, width: Math.ceil(main.width), height: page.viewportSize().height };
    },
  },

  // The stats panel nudging to compact once the window is 80% full.
  "context-compression-nudge": {
    viewport: { width: 1440, height: 900 },
    async go(page) {
      await page.addInitScript(() => localStorage.setItem("precursor:chat-stats:collapsed", "0"));
      await page.goto(`${BASE}/chats/quarterly-capacity-plan`, { waitUntil: "networkidle" });
      await page.getByText(/Context is \d+% full/).waitFor();
      await page.mouse.move(0, 0);
      await sleep(400);
      const aside = await page.locator("aside", { hasText: "Conversation" }).last().boundingBox();
      if (!aside) throw new Error("Seed the quarterly-capacity-plan chat first.");
      return {
        x: Math.floor(aside.x),
        y: Math.floor(aside.y),
        width: Math.ceil(aside.width),
        height: 420,
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
