/**
 * Frontend plugins bundled into the core SPA.
 *
 * Importing this module for its side effects registers the frontend half of any
 * in-tree plugin. It is currently **empty on purpose**: plugins now ship their
 * own built bundle inside their Python wheel, which the SPA imports at runtime
 * (see `lib/pluginLoader.ts`), so nothing has to be baked into core's build.
 *
 * The hook stays because bundling remains the right answer for a plugin that
 * lives in this repository and has no separate release cadence — drop a folder
 * here whose entry file calls `registerSection` (or `registerRenderer`) at
 * module scope, and import it below. Whether it renders is still the backend's
 * call: no descriptor from `/api/plugins`, no UI.
 */

export {};
