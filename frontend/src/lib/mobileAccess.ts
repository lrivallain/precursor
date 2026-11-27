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

export type TunnelSceneMode = "missing" | "off" | "starting" | "waiting" | "live" | "error";

export interface TunnelScene {
  mode: TunnelSceneMode;
  phone: string;
  relay: string;
  computer: string;
}

/**
 * What the tunnel drawing shows for the current setup: the missing piece
 * (no CLI, not signed in), a tunnel ready for its first phone, or a working
 * tunnel with paired phones.
 */
export function tunnelScene(
  state: TunnelStatus["state"] | undefined,
  pairedDevices: number,
): TunnelScene {
  const base = { phone: "Your phone", relay: "Private relay", computer: "This computer" };
  switch (state) {
    case "missing_cli":
      return { ...base, mode: "missing", relay: "devtunnel not installed" };
    case "signed_out":
      return { ...base, mode: "missing", relay: "Not signed in" };
    case "error":
      return { ...base, mode: "error", relay: "Tunnel stopped" };
    case "starting":
      return { ...base, mode: "starting", relay: "Opening…" };
    case "active":
      return pairedDevices > 0
        ? {
            ...base,
            mode: "live",
            phone: pairedDevices === 1 ? "1 paired phone" : `${pairedDevices} paired phones`,
          }
        : { ...base, mode: "waiting", phone: "Scan to pair" };
    default:
      return { ...base, mode: "off", relay: "Tunnel off" };
  }
}

export interface SetupCommand {
  os: string;
  command: string;
}

const INSTALL_COMMANDS: (SetupCommand & { match: RegExp })[] = [
  { os: "macOS", command: "brew install --cask devtunnel", match: /Mac/i },
  { os: "Windows", command: "winget install Microsoft.devtunnel", match: /Win/i },
  { os: "Linux", command: "curl -sL https://aka.ms/DevTunnelCliInstall | bash", match: /Linux|X11/i },
];

/** How to install the dev tunnels CLI, this computer's OS first. */
export function installCommands(userAgent: string): SetupCommand[] {
  const mine = INSTALL_COMMANDS.findIndex(({ match }) => match.test(userAgent));
  const ordered = mine > 0 ? [INSTALL_COMMANDS[mine], ...INSTALL_COMMANDS.filter((_, i) => i !== mine)] : INSTALL_COMMANDS;
  return ordered.map(({ os, command }) => ({ os, command }));
}

export const SIGN_IN_COMMAND: SetupCommand = { os: "Any OS", command: "devtunnel user login" };
