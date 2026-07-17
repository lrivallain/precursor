import DefaultTheme from "vitepress/theme";
import { useRoute } from "vitepress";
import { nextTick, onMounted, watch } from "vue";
import mediumZoom from "medium-zoom";
import type { Zoom } from "medium-zoom";
import Layout from "./Layout.vue";
import Screenshot from "./Screenshot.vue";
import "./custom.css";

export default {
  extends: DefaultTheme,
  Layout,
  enhanceApp({ app }) {
    // Global so any markdown page can use <Screenshot src="…" alt="…" /> without
    // a per-page <script setup> import.
    app.component("Screenshot", Screenshot);
  },
  setup() {
    // Click-to-zoom on doc + hero screenshots. Re-attach on every route change
    // because VitePress swaps page content client-side without a full reload.
    const route = useRoute();
    let zoom: Zoom | undefined;
    const attach = () => {
      const selector = ".vp-doc img, .pc-hero-showcase .frame img";
      if (zoom) {
        zoom.detach();
        zoom.attach(selector);
      } else {
        zoom = mediumZoom(selector, { background: "var(--vp-c-bg)", margin: 24 });
      }
    };
    onMounted(() => nextTick(attach));
    watch(
      () => route.path,
      () => nextTick(attach),
    );
  },
};
