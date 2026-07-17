import DefaultTheme from "vitepress/theme";
import Screenshot from "./Screenshot.vue";
import "./custom.css";

export default {
  extends: DefaultTheme,
  enhanceApp({ app }) {
    // Global so any markdown page can use <Screenshot src="…" alt="…" /> without
    // a per-page <script setup> import.
    app.component("Screenshot", Screenshot);
  },
};
