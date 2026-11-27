// Remote access to the UI through a private tunnel (issue #521). Kept out of
// api.ts / types.ts, which are at their size limit. All but `context` are
// local-only: the backend answers 404 to a paired phone.
import { call } from "./apiClient";
import type * as Api from "./generated/apiTypes";

export type RemoteSession = Api.RemoteSessionRead;
export type PairingCode = Api.PairingCodeRead;
export type TunnelStatus = Api.TunnelStatusRead;

export const remoteApi = {
  access: () => call("GET /api/remote/access"),
  tunnel: () => call("GET /api/remote/tunnel"),
  setTunnel: (running: boolean, revokeSessions = false) =>
    call("PUT /api/remote/tunnel", { body: { running, revoke_sessions: revokeSessions } }),
  pairing: () => call("POST /api/remote/pairing"),
  rename: (id: string, label: string) =>
    call("PATCH /api/remote/sessions/{session_id}", { path: { session_id: id }, body: { label } }),
  revoke: (id: string) =>
    call("DELETE /api/remote/sessions/{session_id}", { path: { session_id: id } }),
  context: () => call("GET /api/remote/context"),
};
