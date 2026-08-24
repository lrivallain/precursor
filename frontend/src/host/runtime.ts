/**
 * The module a plugin's frontend bundle imports from.
 *
 * A plugin ships a *separate* build, loaded at runtime from its Python wheel.
 * If that bundle carried its own React the app would end up with two copies —
 * two dispatchers, two context registries — and every hook a plugin called
 * would throw. So plugin bundles mark `react`, `react-dom`, `react/jsx-runtime`
 * and `@precursor/host` as **external**, and an import map (injected into
 * index.html by the `precursor-plugin-runtime` Vite plugin) points all four at
 * this one module. It re-exports the host's own instances, so there is exactly
 * one React on the page and plugins get the SDK for free.
 *
 * That makes everything exported here **public API**. Adding is safe; removing
 * or changing a signature breaks installed plugins.
 */

import * as React from "react";

// --- React ------------------------------------------------------------------
// The default namespace (`import React from "react"`) plus the named exports a
// plugin actually reaches for. React's own typings use `export =`, so they can't
// be star-re-exported — the list is explicit rather than clever, and adding to it
// is a backwards-compatible change.
export default React;
export {
  Children,
  Fragment,
  Profiler,
  StrictMode,
  Suspense,
  cloneElement,
  createContext,
  createElement,
  createRef,
  forwardRef,
  isValidElement,
  lazy,
  memo,
  startTransition,
  use,
  useCallback,
  useContext,
  useDebugValue,
  useDeferredValue,
  useEffect,
  useId,
  useImperativeHandle,
  useInsertionEffect,
  useLayoutEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
  useSyncExternalStore,
  useTransition,
  version,
} from "react";
export type {
  ComponentType,
  CSSProperties,
  Dispatch,
  FormEvent,
  KeyboardEvent,
  MouseEvent,
  MutableRefObject,
  PropsWithChildren,
  ReactElement,
  ReactNode,
  RefObject,
  SetStateAction,
} from "react";

// `react/jsx-runtime` — what the JSX transform emits. `Fragment` is deliberately
// not re-exported from here: it is the same object React already exports above,
// and a duplicate would make the name ambiguous and silently unavailable.
export { jsx, jsxs } from "react/jsx-runtime";
export { jsxDEV } from "react/jsx-dev-runtime";

// `react-dom` — portals are the realistic need for a plugin rendering a modal
// or a tooltip outside its own subtree.
export { createPortal, flushSync } from "react-dom";

// --- Precursor SDK ----------------------------------------------------------
// The plugin contract itself.
export {
  registerSection,
  registerRenderer,
  getSection,
  resolveSections,
  sectionUnavailableReason,
  pluginsForSlot,
  SECTION_KIND,
  SECTION_SLOT,
} from "../lib/plugins";
export type {
  ExtensionProps,
  SectionEnabledContext,
  SectionHost,
  SectionIconProps,
  SectionPlugin,
} from "../lib/plugins";

// HTTP: `request` carries the client id and error unwrapping, so a plugin's own
// endpoints behave like core's; `api` is the whole core surface, since a plugin
// building on topics or issues shouldn't have to re-describe them.
export { api, apiErrorMessage, request } from "../lib/api";

// Shared chrome, so a plugin's UI looks like the app rather than near it.
export { Modal } from "../components/Modal";
export { Markdown } from "../components/Markdown";
export { EmptyHero } from "../components/EmptyHero";
export { IssueLabelChip, IssueStateBadge } from "../components/IssueTags";
export { RefineTextarea } from "../components/RefineTextarea";
export { useScrollActiveIntoView } from "../lib/useScrollActiveIntoView";
export { useResizableBox } from "../lib/useResizableBox";
export { sectionColor } from "../lib/sections";
export type { SectionColor } from "../lib/sections";

export type {
  IssueComment,
  IssueDetail,
  IssueLabel,
  PluginDescriptor,
  Settings,
} from "../lib/types";

/**
 * Version of this contract. A plugin can assert against it to fail with a clear
 * message instead of a mystery TypeError. Bumped on any breaking change; mirrors
 * `precursor.plugin_api.PLUGIN_API_VERSION` on the backend.
 */
export const HOST_API_VERSION = 1;
