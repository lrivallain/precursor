---
title: Phone & tablet layout
---

# Phone & tablet layout

Below **768px** wide, Precursor switches to a single-pane layout: content takes
the full width and navigation moves into a drawer. Above that width nothing
changes.

<Screenshot src="/screenshots/mobile-chat.png" alt="Precursor on a phone-sized screen: a conversation using the full width, with a hamburger button in the header and the composer pinned to the bottom" caption="On a phone the conversation owns the entire width; the sidebar is one tap away behind the hamburger." />

## The navigation drawer

Tap the **hamburger** in the header to slide in the full sidebar (section rail,
collections, search, list, persona footer). Tap outside it, **×**, or press
<kbd>Esc</kbd> to close it.

<Screenshot src="/screenshots/mobile-drawer.png" alt="The navigation drawer open over a conversation on a phone, showing the section rail, a chat search box, the chat list and the persona footer" caption="The drawer is the full sidebar, overlaid rather than docked, with a strip of the conversation still visible to tap back to." />

- Picking an item (topic, chat, session, workspace, home) closes it.
- Switching section keeps it open, except **Agents** and **Workflows**, which
  open their overview.
- On the home launcher the drawer replaces the section rail.

## Panels that can't fit

| Surface | On a phone |
| --- | --- |
| [Workspaces](/features/workspaces) | File tree → file is a list → detail flow, with a back arrow. |
| Workspace discussion | Opens **over** the workspace (below **1024px**, so tablets keep tree and editor side by side). |
| Conversation stats | Hidden. |

Your desktop panel sizes and preferences are kept for when the screen is wide
enough again.

## Touch devices

On devices that can't hover (`(hover: none)`; a touchscreen laptop with a
trackpad is unaffected):

- Hover-only actions (row actions, code-block **Copy**) are always shown.
- Inputs have a 16px font floor, so Safari doesn't zoom on focus.
- The shell uses `100dvh` and safe-area padding, so the composer stays above the
  address bar and the home indicator.

## Open it from your phone

Precursor can open itself to your phone through a **private Microsoft dev
tunnel**, without changing its bind address: it stays on `127.0.0.1`, and
everything still runs on your computer.

<Screenshot src="/screenshots/mobile-access.png" alt="The Mobile access dialog: a drawing of the phone reaching this computer through a private relay, the tunnel on, a QR code to scan with the phone, the code's expiry, a paired iPhone with a Sign out link, and a Stop the tunnel button" caption="Mobile access, from the persona menu. The drawing follows your setup: the missing piece (dev tunnels CLI, sign-in), a tunnel waiting for its first phone, or requests and answers flowing once a phone is paired. Scan the code; paired phones are listed with a way to sign them out." />

1. Install the dev tunnels CLI once (`brew install --cask devtunnel` on macOS,
   `winget install Microsoft.devtunnel` on Windows) and sign in with
   `devtunnel user login`.
2. In the persona menu, choose **Mobile access**, then **Start the tunnel**.
3. Scan the QR code with your phone's camera. Sign in with the account that
   owns the tunnel: Precursor opens, and the phone stays paired.
4. Add it to the home screen.

The tunnel is private: only the account that created it gets past the relay.
On top of that, each phone must be paired with a one-time code (single use,
30 minutes). Paired devices are listed in the dialog and can be signed out
one by one, or all at once when you stop the tunnel. The tunnel keeps its
address across restarts, comes back on when Precursor restarts, and is
restarted by itself when it drops.

From the phone, some features stay on the computer: Mobile access itself, the
plugin installer, MCP over HTTP and the **Open in VS Code** links (they open a
path on the computer). Links that tools write to Precursor's own pages work on
the phone too.

## Installing it to the home screen

**Add to Home Screen** gives a standalone window (web manifest and icons are
included). Your phone must reach the machine running Precursor, which binds to
localhost by default — see [configuration](/guide/configuration) to change the
bind host, and the [security model](/reference/architecture#security-deployment-model)
before you do.

::: warning Small screens are a layout, not a separate app
Same app, same data. Dense surfaces such as the [workflow](/features/workflows)
builder canvas are still more comfortable on a larger screen.
:::
