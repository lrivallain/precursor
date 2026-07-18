<script setup lang="ts">
import { withBase } from "vitepress";

// A framed product screenshot that follows the site theme. `src` is the light
// variant; the dark variant is derived by inserting `-dark` before the
// extension (e.g. topics.png -> topics-dark.png). Both are emitted and CSS shows
// the one matching the active theme. `src` is resolved against the site base so
// it works on GitHub Pages project paths.
const props = defineProps<{
  src: string;
  alt: string;
  caption?: string;
}>();

const light = withBase(props.src);
const dark = withBase(props.src.replace(/\.(png|jpe?g|webp|avif)$/i, "-dark.$1"));
</script>

<template>
  <figure class="pc-shot">
    <img class="light-only" :src="light" :alt="props.alt" loading="lazy" />
    <img class="dark-only" :src="dark" :alt="props.alt" loading="lazy" />
    <figcaption
      v-if="props.caption"
      style="
        padding: 0.5rem 0.9rem;
        font-size: 0.82rem;
        color: var(--vp-c-text-2);
        border-top: 1px solid var(--vp-c-divider);
      "
    >
      {{ props.caption }}
    </figcaption>
  </figure>
</template>
