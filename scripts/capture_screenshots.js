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
// The target must be the DEMO instance on :8899, whose persona resolves to
// "Guest / Not connected" because its server runs with `gh` off PATH.

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

const BASE = process.env.DEMO_BASE || "http://127.0.0.1:8899";
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

// --------------------------------------------------------------------------
// Scenes. `viewport` is per scene because these surfaces have very different
// natural heights; the clip trims whatever is left over.
// --------------------------------------------------------------------------
const scenes = {
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

  // Settings → Plugins: the installed packages and what each contributes.
  plugins: {
    viewport: { width: 1440, height: 1000 },
    async go(page) {
      await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
      await openSettings(page, "Plugins");
      return clipOf(page, "div.fixed.inset-0 > div, [role=dialog]", 0);
    },
  },

  // The same panel, framed on the bundled catalogue — the "Available" list you
  // install from. Its content depends on what the demo environment already has:
  // an entry disappears from Available once its package is installed, so run
  // this one against an instance *without* the catalogued plugins. The shot
  // reveals one entry's install command, since that is the state a reader
  // without the in-app installer enabled will actually meet.
  "plugins-catalog": {
    viewport: { width: 1440, height: 1000 },
    async go(page) {
      await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
      await openSettings(page, "Plugins");
      const command = page.getByRole("button", { name: "Install command" }).first();
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
