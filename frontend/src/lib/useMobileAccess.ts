// State and actions of the Mobile access dialog: the tunnel, the pairing code
// shown as a QR code, and the paired devices.
import { useCallback, useEffect, useRef, useState } from "react";
import { remoteApi } from "./remoteAccessApi";
import { needsNewCode, pollDelay, wantsPairingCode } from "./mobileAccess";
import { useBusyAction } from "./useBusyAction";
import type { PairingCode, RemoteSession, TunnelStatus } from "./remoteAccessApi";

export interface MobileAccess {
  tunnel: TunnelStatus | null;
  devices: RemoteSession[];
  code: PairingCode | null;
  busy: boolean;
  error: string | null;
  start: () => void;
  stop: (signOutDevices: boolean) => void;
  newCode: () => void;
  /** Show a QR code to pair one more device (after the first, it is on request). */
  addDevice: () => void;
  cancelAdd: () => void;
  revoke: (id: string) => void;
  rename: (id: string, label: string) => void;
}

export function useMobileAccess(): MobileAccess {
  const [tunnel, setTunnel] = useState<TunnelStatus | null>(null);
  const [devices, setDevices] = useState<RemoteSession[]>([]);
  const [code, setCodeState] = useState<PairingCode | null>(null);
  // The tick reads the current code without re-arming its timer on each change.
  const codeRef = useRef<PairingCode | null>(null);
  const setCode = useCallback((next: PairingCode | null) => {
    codeRef.current = next;
    setCodeState(next);
  }, []);
  const { busy, error, run } = useBusyAction();
  // After the first device, a code is only made when the user asks for one,
  // and put away once that device has paired.
  const addRequested = useRef(false);
  const pairedCount = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    const [t, access] = await Promise.all([remoteApi.tunnel(), remoteApi.access()]);
    const count = access.sessions.length;
    if (pairedCount.current !== null && count > pairedCount.current) {
      addRequested.current = false; // the device it was for has paired
      setCode(null);
    }
    pairedCount.current = count;
    setTunnel(t);
    setDevices(access.sessions);
    return t;
  }, [setCode]);

  // Refresh while the dialog is open (a phone pairing shows up), faster while
  // the tunnel comes up; keep a usable QR code once it is active.
  const state = tunnel?.state;
  useEffect(() => {
    let cancelled = false;
    async function tick() {
      const t = await refresh().catch(() => null);
      if (cancelled || t?.state !== "active") return;
      if (!wantsPairingCode(pairedCount.current ?? 0, addRequested.current)) return;
      if (!needsNewCode(codeRef.current, Date.now())) return;
      const fresh = await remoteApi.pairing().catch(() => null);
      if (!cancelled && fresh) setCode(fresh);
    }
    void tick();
    const timer = window.setInterval(() => void tick(), pollDelay(state ?? "starting"));
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [refresh, setCode, state]);

  return {
    tunnel,
    devices,
    code,
    busy,
    error,
    start: () => void run(async () => setTunnel(await remoteApi.setTunnel(true))),
    stop: (signOutDevices) =>
      void run(async () => {
        setTunnel(await remoteApi.setTunnel(false, signOutDevices));
        setCode(null);
        await refresh();
      }),
    newCode: () => void run(async () => setCode(await remoteApi.pairing())),
    addDevice: () => {
      addRequested.current = true;
      void run(async () => setCode(await remoteApi.pairing()));
    },
    cancelAdd: () => {
      addRequested.current = false;
      setCode(null);
    },
    rename: (id, label) =>
      void run(async () => {
        await remoteApi.rename(id, label);
        await refresh();
      }),
    revoke: (id) =>
      void run(async () => {
        await remoteApi.revoke(id);
        await refresh();
      }),
  };
}
