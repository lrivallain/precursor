// State and actions of the Mobile access dialog: the tunnel, the pairing code
// shown as a QR code, and the paired devices.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { remoteApi } from "./remoteAccessApi";
import { singleFlight } from "./singleFlight";
import { needsNewCode, newlyPaired, pollDelay, wantsPairingCode } from "./mobileAccess";
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
  const knownIds = useRef<Set<string> | null>(null);
  // Bumped whenever the shown code must go away (cancel, stop, device paired):
  // a code request still in flight then lands on a stale generation and is
  // dropped instead of bringing the QR code back.
  const codeGeneration = useRef(0);
  const dropCode = useCallback(() => {
    codeGeneration.current += 1;
    setCode(null);
  }, [setCode]);
  // Single-flight: polling and a click never mint two codes at once (each new
  // code invalidates the previous one, so the older could end up shown).
  const mintCode = useMemo(() => singleFlight(() => remoteApi.pairing()), []);
  const fetchCode = useCallback(async () => {
    const generation = codeGeneration.current;
    const fresh = await mintCode();
    if (generation === codeGeneration.current) setCode(fresh);
  }, [mintCode, setCode]);

  const refresh = useCallback(async () => {
    const [t, access] = await Promise.all([remoteApi.tunnel(), remoteApi.access()]);
    const ids = access.sessions.map((s) => s.id);
    if (newlyPaired(knownIds.current, ids)) {
      addRequested.current = false; // the device it was for has paired
      dropCode();
    }
    knownIds.current = new Set(ids);
    setTunnel(t);
    setDevices(access.sessions);
    return t;
  }, [dropCode]);

  // Refresh while the dialog is open (a phone pairing shows up), faster while
  // the tunnel comes up; keep a usable QR code once it is active.
  const state = tunnel?.state;
  useEffect(() => {
    let cancelled = false;
    async function tick() {
      const t = await refresh().catch(() => null);
      if (cancelled || t?.state !== "active") return;
      if (!wantsPairingCode(knownIds.current?.size ?? 0, addRequested.current)) return;
      if (!needsNewCode(codeRef.current, Date.now())) return;
      await fetchCode().catch(() => undefined);
    }
    void tick();
    const timer = window.setInterval(() => void tick(), pollDelay(state ?? "starting"));
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [fetchCode, refresh, state]);

  return {
    tunnel,
    devices,
    code,
    busy,
    error,
    start: () => void run(async () => setTunnel(await remoteApi.setTunnel(true))),
    stop: (signOutDevices) =>
      void run(async () => {
        addRequested.current = false;
        dropCode();
        setTunnel(await remoteApi.setTunnel(false, signOutDevices));
        await refresh();
      }),
    newCode: () => void run(fetchCode),
    addDevice: () => {
      addRequested.current = true;
      void run(fetchCode);
    },
    cancelAdd: () => {
      addRequested.current = false;
      dropCode();
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
