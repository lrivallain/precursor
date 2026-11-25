- **Self wakeups.** A model waiting on something outside the conversation (a CI
  run, a deploy, a long job) can now pause a topic, chat or agent and wake itself
  up later with a prompt of its own, instead of polling. A paused conversation
  shows an orange pause icon in the sidebar and a banner with **Wake now** and
  **Cancel**. Agents reach it through the `precursor` MCP server (new `wakeups`
  section).
