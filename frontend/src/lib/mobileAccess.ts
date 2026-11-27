// The Mobile access dialog's open flag and the pure rules behind it (issue #521).
import { useEffect, useSyncExternalStore } from "react";
import { remoteApi } from "./remoteAccessApi";
import { Emitter } from "./externalStore";
import type { PairingCode, TunnelStatus } from "./remoteAccessApi";

const dialog = new Emitter();
let dialogOpen = false;

export function openMobileAccess(): void {
  dialogOpen = true;
  dialog.emit();
}

export function closeMobileAccess(): void {
  dialogOpen = false;
  dialog.emit();
}

export function useMobileAccessOpen(): boolean {
  return useSyncExternalStore(dialog.subscribe, () => dialogOpen, () => false);
}

/** How often to refresh while the dialog is open: fast while the tunnel comes up. */
export function pollDelay(state: TunnelStatus["state"]): number {
  return state === "starting" ? 1500 : 5000;
}

/** A pairing code to replace: none yet, or used up within a minute. */
export function needsNewCode(code: PairingCode | null, now: number): boolean {
  if (!code?.url) return true;
  return Date.parse(code.expires_at) - now < 60_000;
}

export function tunnelSummary(status: TunnelStatus | null): string {
  switch (status?.state) {
    case undefined:
      return "Checking…";
    case "missing_cli":
      return "The devtunnel CLI is not installed on this computer.";
    case "signed_out":
      return "Sign in to dev tunnels on this computer first.";
    case "stopped":
      return "Off. Start it to open Precursor from your phone.";
    case "starting":
      return "Starting the tunnel…";
    case "active":
      return "On. Scan the code with your phone's camera.";
    case "error":
      return status.error ?? "The tunnel stopped.";
  }
}

// Whether this page itself was opened through the tunnel, and the loopback
// origin tools use in links to the app's pages. Asked once per load; unknown
// (null) until a request succeeds. A failed request stays unknown and is asked
// again on the next mount, so a hiccup never passes a phone off as local.
const contextChanged = new Emitter();
let pending: Promise<boolean | null> | null = null;
let knownRemote: boolean | null = null;
let knownAppOrigin: string | null = null;

export function loadRemoteContext(): Promise<boolean | null> {
  pending ??= remoteApi.context().then(
    (c) => {
      knownRemote = c.remote;
      knownAppOrigin = c.app_origin || null;
      contextChanged.emit();
      return c.remote;
    },
    () => {
      pending = null;
      return null;
    },
  );
  return pending;
}

/** Whether this page came through the tunnel: null until known. */
export function useRemoteContext(): boolean | null {
  useEffect(() => void loadRemoteContext(), []);
  return useSyncExternalStore(contextChanged.subscribe, () => knownRemote, () => null);
}

/** The app's own loopback origin, once known; re-renders when it arrives. */
export function useAppOrigin(): string | null {
  useEffect(() => void loadRemoteContext(), []);
  return useSyncExternalStore(contextChanged.subscribe, () => knownAppOrigin, () => null);
}

/** Test hook: forget what was learned. */
export function resetRemoteContext(): void {
  pending = null;
  knownRemote = null;
  knownAppOrigin = null;
}

export function remoteContextSnapshot(): { remote: boolean | null; appOrigin: string | null } {
  return { remote: knownRemote, appOrigin: knownAppOrigin };
}

export const remoteContextChanges = contextChanged;
