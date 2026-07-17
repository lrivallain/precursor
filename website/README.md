# Precursor website

The public showcase + documentation site for Precursor, built with
[VitePress](https://vitepress.dev) and published to **GitHub Pages** on every
push to `main` via [`.github/workflows/pages.yml`](../.github/workflows/pages.yml).

Live at **https://lrivallain.github.io/precursor/**.

## Develop locally

```bash
cd website
npm install
npm run docs:dev        # live-reload dev server
npm run docs:build      # production build → .vitepress/dist
npm run docs:preview    # preview the production build
```

## Structure

```
website/
├── .vitepress/
│   ├── config.mts        # site config, nav, sidebar
│   └── theme/            # brand tokens + <Screenshot> component
├── index.md              # landing page (hero + feature grid)
├── guide/                # getting started (install, quick start, config)
├── features/             # per-feature guides
├── reference/            # stack, architecture, API & plugin references
├── contributing/         # contribution, workflow, releasing
└── public/screenshots/   # product screenshots (account hidden)
```

## Screenshots

Product screenshots live in `public/screenshots/`. They are captured against a
local instance seeded with **fictional** demo content and with the GitHub
account hidden (no token resolved), so no personal data appears. See the
`<Screenshot>` component in `.vitepress/theme/` for how pages embed them.
