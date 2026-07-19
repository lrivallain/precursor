import { defineConfig } from "vitepress";
import { withMermaid } from "vitepress-plugin-mermaid";

// Served from a dedicated custom domain (precursor.vuptime.io) at its root, so
// the base is "/". Overridable via DOCS_BASE for a different host/subpath.
const base = process.env.DOCS_BASE || "/";

export default withMermaid(
  defineConfig({
  base,
  lang: "en-US",
  title: "Precursor",
  description:
    "Opinionated approach to work follow-up, built as an AI assistant — topics linked to GitHub issues, live meetings, autonomous agents, MCP, and more, in a single local-first app.",
  cleanUrls: true,
  lastUpdated: true,
  ignoreDeadLinks: true,

  head: [
    ["link", { rel: "icon", type: "image/svg+xml", href: `${base}logo.svg` }],
    ["meta", { name: "theme-color", content: "#0ea5e9" }],
    ["meta", { property: "og:type", content: "website" }],
    ["meta", { property: "og:title", content: "Precursor" }],
    [
      "meta",
      {
        property: "og:description",
        content:
          "Opinionated approach to work follow-up, built as an AI assistant — topics linked to the GitHub issues they belong to, in a single local-first app.",
      },
    ],
  ],

  themeConfig: {
    logo: "/logo.svg",

    nav: [
      { text: "Home", link: "/" },
      { text: "Guide", link: "/guide/introduction" },
      { text: "Features", link: "/features/" },
      { text: "Reference", link: "/reference/architecture" },
      { text: "Contributing", link: "/contributing/" },
      {
        text: "GitHub",
        link: "https://github.com/lrivallain/precursor",
      },
    ],

    sidebar: {
      "/guide/": [
        {
          text: "Getting started",
          items: [
            { text: "Introduction", link: "/guide/introduction" },
            { text: "Installation", link: "/guide/installation" },
            { text: "Quick start", link: "/guide/quick-start" },
            { text: "Configuration", link: "/guide/configuration" },
          ],
        },
        {
          text: "Explore",
          items: [
            { text: "Feature guides", link: "/features/" },
            { text: "Architecture", link: "/reference/architecture" },
            { text: "Contributing", link: "/contributing/" },
          ],
        },
      ],

      "/features/": [
        {
          text: "Feature guides",
          items: [
            { text: "Overview", link: "/features/" },
            { text: "Topics", link: "/features/topics" },
            { text: "Chats", link: "/features/chats" },
            { text: "Live sessions", link: "/features/live-sessions" },
            { text: "Agents", link: "/features/agents" },
            { text: "Workspaces & files", link: "/features/workspaces" },
            { text: "Kanban board", link: "/features/kanban" },
            { text: "Skills & memory", link: "/features/skills-memory" },
            { text: "Scheduler & reminders", link: "/features/scheduler" },
            { text: "MCP (tools both ways)", link: "/features/mcp" },
            { text: "Command runner", link: "/features/command-runner" },
            { text: "Attachments", link: "/features/attachments" },
            { text: "Plugins", link: "/features/plugins" },
          ],
        },
      ],

      "/reference/": [
        {
          text: "Reference",
          items: [
            { text: "Technical stack", link: "/reference/stack" },
            { text: "Architecture", link: "/reference/architecture" },
            { text: "Configuration reference", link: "/reference/configuration" },
            { text: "API reference", link: "/reference/api" },
            { text: "Plugin reference", link: "/reference/plugins" },
          ],
        },
      ],

      "/contributing/": [
        {
          text: "Contributing",
          items: [
            { text: "Contribution guide", link: "/contributing/" },
            { text: "Development workflow", link: "/contributing/workflow" },
            { text: "Releasing", link: "/contributing/releasing" },
          ],
        },
      ],
    },

    socialLinks: [
      { icon: "github", link: "https://github.com/lrivallain/precursor" },
    ],

    editLink: {
      pattern:
        "https://github.com/lrivallain/precursor/edit/main/website/:path",
      text: "Edit this page on GitHub",
    },

    search: { provider: "local" },

    outline: { level: [2, 3] },

    footer: {
      message: "Released under the MIT License.",
      copyright: "Copyright © 2026 Precursor contributors",
    },
  },
  }),
);
