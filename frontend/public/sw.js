// Minimal service worker for PWA installability.
//
// Precursor is a thin client over a local FastAPI backend, so there is
// deliberately NO offline caching here: every request falls through to the
// network. This worker exists only to satisfy the browser's "installable"
// requirement (a registered worker with a fetch handler) so the app can be
// added to the home screen / launched in a standalone window.

self.addEventListener("install", () => {
  // Activate this worker immediately instead of waiting for old tabs to close.
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  // Take control of already-open clients as soon as we activate.
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", () => {
  // Intentionally a no-op: not calling event.respondWith() lets the browser
  // perform its normal network fetch. We never cache API/SSE/streaming traffic.
});
