<script setup lang="ts">
import type { CatalogPlugin } from "../catalog.data.mts";

defineProps<{ plugins: CatalogPlugin[] }>();
</script>

<template>
  <ul v-if="plugins.length" class="pc-catalog">
    <li v-for="plugin in plugins" :key="plugin.id" class="pc-catalog-card">
      <div class="pc-catalog-head">
        <a :href="plugin.url" class="pc-catalog-title">{{ plugin.title }}</a>
        <span v-if="plugin.recommended" class="pc-catalog-badge">Recommended</span>
      </div>

      <p class="pc-catalog-summary">{{ plugin.summary }}</p>

      <p class="pc-catalog-meta">
        <code>{{ plugin.distribution }}</code>
        <span v-if="plugin.author"> · by {{ plugin.author }}</span>
        <span v-if="plugin.license"> · {{ plugin.license }}</span>
      </p>

      <ul v-if="plugin.contributes.length" class="pc-catalog-tags">
        <li v-for="item in plugin.contributes" :key="item">{{ item }}</li>
      </ul>

      <p class="pc-catalog-links">
        <a :href="plugin.url">Documentation</a>
        <a v-if="plugin.homepage" :href="plugin.homepage" target="_blank" rel="noreferrer">
          Source
        </a>
      </p>
    </li>
  </ul>

  <p v-else class="pc-catalog-empty">
    The catalogue is empty. Yours could be the first —
    <a href="/plugins/submitting">submit a plugin</a>.
  </p>
</template>

<style scoped>
.pc-catalog {
  list-style: none;
  padding: 0;
  margin: 24px 0;
  display: grid;
  gap: 16px;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
}

.pc-catalog-card {
  margin: 0;
  padding: 20px;
  border: 1px solid var(--vp-c-divider);
  border-radius: 12px;
  background-color: var(--vp-c-bg-soft);
  transition: border-color 0.25s;
}

.pc-catalog-card:hover {
  border-color: var(--vp-c-brand-1);
}

.pc-catalog-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.pc-catalog-title {
  font-weight: 600;
  font-size: 16px;
  color: var(--vp-c-text-1);
  text-decoration: none;
}

.pc-catalog-title:hover {
  color: var(--vp-c-brand-1);
}

.pc-catalog-badge {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 6px;
  border-radius: 4px;
  color: var(--vp-c-brand-1);
  background-color: var(--vp-c-brand-soft);
}

.pc-catalog-summary {
  margin: 8px 0 0;
  font-size: 14px;
  line-height: 1.6;
  color: var(--vp-c-text-2);
}

.pc-catalog-meta {
  margin: 8px 0 0;
  font-size: 12px;
  color: var(--vp-c-text-3);
}

.pc-catalog-meta code {
  font-size: 12px;
}

.pc-catalog-tags {
  list-style: none;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 0;
  margin: 12px 0 0;
}

.pc-catalog-tags li {
  margin: 0;
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid var(--vp-c-divider);
  color: var(--vp-c-text-2);
}

.pc-catalog-links {
  margin: 14px 0 0;
  display: flex;
  gap: 16px;
  font-size: 13px;
  font-weight: 500;
}

.pc-catalog-empty {
  color: var(--vp-c-text-2);
}
</style>
