<script setup lang="ts">
import { withBase } from "vitepress";

// A framed product screenshot with an optional caption. `src` is resolved
// against the site base so it works on GitHub Pages project paths. If the image
// hasn't been captured yet the <img> simply won't load — the frame + caption
// still communicate what belongs there.
const props = defineProps<{
  src: string;
  alt: string;
  caption?: string;
}>();
</script>

<template>
  <figure class="pc-shot">
    <img :src="withBase(props.src)" :alt="props.alt" loading="lazy" />
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
