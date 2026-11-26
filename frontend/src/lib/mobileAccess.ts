// The Mobile access dialog's open flag and the pure rules behind it (issue #521).
import { useSyncExternalStore } from "react";
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

// Whether this page itself was opened through the tunnel. Asked once per load.
let remoteContext: Promise<boolean> | null = null;

export function isRemoteContext(): Promise<boolean> {
  remoteContext ??= remoteApi
    .context()
    .then((c) => c.remote)
    .catch(() => false);
  return remoteContext;
}
