<script setup lang="ts">
import DefaultTheme from "vitepress/theme";
import { withBase } from "vitepress";

const { Layout } = DefaultTheme;

// A single showcase screenshot rendered right under the hero. Both light and
// dark variants are emitted; CSS shows the one matching the active theme so it
// always mirrors the page's light/dark mode.
const light = withBase("/screenshots/home.png");
const dark = withBase("/screenshots/home-dark.png");
</script>

<template>
  <Layout>
    <template #home-hero-after>
      <div class="pc-hero-showcase">
        <div class="frame">
          <img class="light-only" :src="light" alt="Precursor home page" />
          <img class="dark-only" :src="dark" alt="Precursor home page (dark)" />
        </div>
        <p class="caption">A single home for every thread of work.</p>
      </div>
    </template>
  </Layout>
</template>
