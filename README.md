# wb-node-red

A one-command install — `apt install wb-node-red` — that runs
[Node-RED](https://nodered.org/) on a [Wiren Board](https://wirenboard.com/)
controller as a system service, ready to use out of the box.

## What you get

- **Node-RED as a service**, running as a dedicated unprivileged systemd
  service (`wb-node-red.service`). Node.js comes from the distribution
  (`Depends: nodejs`), so its security updates arrive from the
  Wiren Board / Debian repository.
- **A default flow** already wired to the local MQTT broker
  (`127.0.0.1:1883`) and the device tree (`/devices/#`) — open the editor and
  you immediately see the controller's live data. Working with Wiren Board
  means the stock MQTT nodes and the topic conventions
  (`/devices/<dev>/controls/<ctl>`, writes via `.../on`); the community
  palette `node-red-contrib-wirenboard` can be added through the Palette
  Manager if desired (Internet access required).
- **Behind the homeui login.** The editor listens on loopback only and is
  served on a **dedicated HTTPS port** that nginx proxies behind the standard
  Wiren Board login (`auth_request`, **admin only** — Node-RED can execute
  arbitrary code). The homeui session cookie is bound to the host (not to the
  port), so it reaches the port; the old `/node-red/` URL is kept as a
  redirect to it. Trade-off: the dedicated port is not reachable through the
  WB Cloud tunnel — access is local / LAN only.
  The boundary is deliberately the *network* gate (no in-app `adminAuth`):
  locally on the controller `127.0.0.1:1880` is an unauthenticated admin API,
  and any local shell account can control Node-RED.
- **Data survives upgrades.** Flows, credentials and any installed nodes live
  in `/mnt/data/wb-node-red` — on the partition that survives reflashing.
  `apt upgrade` replaces the code and restarts the service; your data stays.

## Requirements

Browser access to the editor needs two things; without them the package
installs and the service runs, but **the editor stays unreachable** (postinst
skips the nginx gate setup and says so on stderr):

- **`wb-mqtt-homeui` ≥ 2.235.4** — ships the service-gate nginx snippets the
  gate includes, and renders the "Node-RED" menu item.
- **The sslip TLS certificate** (`/etc/ssl/sslip.pem`) — managed by homeui;
  it exists once HTTPS access is enabled in homeui.

If homeui is installed or upgraded *after* this package, the gate and the
menu item are wired up automatically (a dpkg trigger watches the homeui
snippets; the same trigger removes the gate when homeui is removed, so nginx
does not fail on a dangling include). The certificate is the only thing dpkg
cannot see: if it appeared later, run `dpkg-reconfigure wb-node-red`.

One more safeguard: if port 1880 is already taken by something else (e.g. a
manually installed Node-RED — previously the recommended setup), the gate is
skipped with a warning, so a foreign instance is not exposed on `:21880`.

## How it works

| What | Where |
|---|---|
| Node-RED code | `/mnt/data/wb-node-red-runtime/node_modules/` (extracted from the `.deb` at install time — keeps the ~120 MB tree off the tight rootfs; overwritten on upgrade) |
| Settings template | `/usr/lib/wb-node-red/settings.js` (overwritten on upgrade) |
| Your settings overrides | `/mnt/data/wb-node-red/settings-user.js` (optional, survives upgrades; merged on top of the template — the loopback bind and `userDir` stay pinned) |
| Your flows / credentials / nodes | `/mnt/data/wb-node-red/` (seeded if empty; preserved) |
| Service | `wb-node-red.service` → `node red.js`, user `wb-node-red`, editor on `127.0.0.1:1880` (httpAdminRoot `/`) |
| Public access | `https://<homeui-host>:21880/` — an nginx server block in `conf.d`, admin gate → `127.0.0.1:1880`; the old `/node-red/` 302-redirects there |
| MQTT | default flow → `127.0.0.1:1883` |
| homeui menu | "Node-RED" under **Integrations** — a drop-in postinst places into homeui's `custom-menu/` (homeui ≥ 2.235.4 only; `isExternal` renders a proper link, and `openInNewTab` opens it in a separate reusable tab so homeui stays put) |

The Node-RED runtime is **vendored into the `.deb` at build time**
(`npm ci --omit=dev` against `vendor/package.json`), so the installation
downloads nothing from npm and compiles nothing on the controller. The only
platform-specific component — the optional `@node-rs/bcrypt` binding
(Node-RED falls back to pure-JS `bcryptjs`) — is stripped right after the
npm ci step, and the build fails if a single `*.node` binary remains in the
tree; that is what keeps the package `Architecture: all`.
