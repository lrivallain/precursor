import { Smartphone, X } from "lucide-react";
import { closeMobileAccess, tunnelSummary, useMobileAccessOpen } from "../lib/mobileAccess";
import { useMobileAccess, type MobileAccess } from "../lib/useMobileAccess";
import type { PairingCode, RemoteSession } from "../lib/remoteAccessApi";
import { useConfirm } from "./ConfirmDialog";
import { Modal } from "./Modal";

/** Mounted once by the persona menu; shows the dialog while it is open. */
export function MobileAccessHost() {
  const open = useMobileAccessOpen();
  return open ? <MobileAccessModal onClose={closeMobileAccess} /> : null;
}

function MobileAccessModal({ onClose }: { onClose: () => void }) {
  const access = useMobileAccess();
  const confirm = useConfirm();
  const state = access.tunnel?.state;

  async function stop() {
    const signOut =
      access.devices.length > 0 &&
      (await confirm({
        title: "Stop the tunnel",
        message:
          "Also sign out the paired devices? They will need a new QR code next time, and the OpenAI-compatible endpoint gets a new key.",
        confirmLabel: "Sign them out",
        cancelLabel: "Keep them paired",
      }));
    access.stop(signOut);
  }

  return (
    <Modal
      onClose={onClose}
      closeOnEscape
      padded
      labelledBy="mobile-access-title"
      panelClassName="w-full max-w-md max-h-[85vh] overflow-y-auto rounded-2xl border border-border bg-surface p-5 shadow-xl"
    >
      <div className="flex items-center gap-2 mb-3">
        <Smartphone size={18} className="text-muted" />
        <h2 id="mobile-access-title" className="text-base font-semibold flex-1">
          Mobile access
        </h2>
        <button type="button" onClick={onClose} aria-label="Close" data-tooltip="Close">
          <X size={16} />
        </button>
      </div>
      <p className="text-sm text-muted mb-3">{tunnelSummary(access.tunnel)}</p>
      <Setup state={state} />
      {state === "active" && <Pairing code={access.code} onNewCode={access.newCode} />}
      <Devices devices={access.devices} onRevoke={access.revoke} busy={access.busy} />
      {access.error && <p className="text-sm text-red-500 mt-2">{access.error}</p>}
      <TunnelButton access={access} onStop={() => void stop()} />
    </Modal>
  );
}

function Setup({ state }: { state: string | undefined }) {
  if (state === "missing_cli") {
    return (
      <pre className="text-xs bg-bg border border-border rounded p-2 mb-3 whitespace-pre-wrap">
        {"macOS:   brew install --cask devtunnel\nWindows: winget install Microsoft.devtunnel"}
      </pre>
    );
  }
  if (state === "signed_out") {
    return <pre className="text-xs bg-bg border border-border rounded p-2 mb-3">devtunnel user login</pre>;
  }
  return null;
}

function Pairing({ code, onNewCode }: { code: PairingCode | null; onNewCode: () => void }) {
  if (!code?.qr_svg || !code.url) return <p className="text-sm text-muted mb-3">Preparing a code…</p>;
  const expires = new Date(code.expires_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return (
    <div className="flex flex-col items-center gap-2 mb-3">
      <img
        src={`data:image/svg+xml;utf8,${encodeURIComponent(code.qr_svg)}`}
        alt="QR code to pair your phone"
        className="w-48 h-48 rounded bg-white p-1"
      />
      <p className="text-xs text-muted text-center">
        Works once, until {expires}. Your phone asks you to sign in with the Microsoft account that
        owns the tunnel, then opens Precursor.
      </p>
      <button type="button" className="text-xs underline" onClick={onNewCode}>
        New code
      </button>
    </div>
  );
}

function Devices(props: { devices: RemoteSession[]; onRevoke: (id: string) => void; busy: boolean }) {
  if (props.devices.length === 0) return null;
  return (
    <div className="mb-3">
      <h3 className="text-xs uppercase tracking-wide text-muted mb-1">Paired devices</h3>
      <ul className="text-sm divide-y divide-border">
        {props.devices.map((d) => (
          <li key={d.id} className="flex items-center gap-2 py-1">
            <span className="flex-1 truncate" data-tooltip={d.label}>
              {d.label}
            </span>
            <span className="text-xs text-muted">{new Date(d.last_used_at).toLocaleDateString()}</span>
            <button
              type="button"
              className="text-xs underline"
              disabled={props.busy}
              onClick={() => props.onRevoke(d.id)}
            >
              Sign out
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function TunnelButton({ access, onStop }: { access: MobileAccess; onStop: () => void }) {
  const state = access.tunnel?.state;
  if (state === undefined || state === "missing_cli") return null;
  const running = state === "active" || state === "starting";
  return (
    <div className="flex justify-end">
      <button
        type="button"
        className="px-3 py-1.5 rounded-md text-sm border border-border bg-bg hover:bg-border disabled:opacity-50"
        disabled={access.busy}
        onClick={running ? onStop : access.start}
      >
        {running ? "Stop the tunnel" : "Start the tunnel"}
      </button>
    </div>
  );
}
