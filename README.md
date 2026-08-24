# wb-node-red

A ready-made [Node-RED](https://nodered.org/) package for
[Wiren Board](https://wirenboard.com/) controllers: `apt install wb-node-red`
and Node-RED runs as a system service behind the controller's standard login,
wired to MQTT out of the box.

## Why this exists

Installing Node-RED by hand (`npm install -g node-red`) does not work well on
a controller: the WB specifics require extra setup, and all of it is baked
into the package.

1. **Code and data on the persistent partition.** The root partition is
   small, the Node-RED npm tree weighs ~120 MB, and user flows must survive
   reflashing. The package keeps both the runtime and the data on
   `/mnt/data`: `apt upgrade` replaces the code and restarts the service;
   your flows / credentials / extra nodes stay.
2. **No Internet, no compilation.** npm would pull hundreds of packages from
   the network and compile native modules on the controller. Here the whole
   `node_modules` tree already sits inside the `.deb` — installation works
   offline and compiles nothing. The community palette
   `node-red-contrib-wirenboard` can be added through the Palette Manager if
   desired (Internet access required).
3. **Security.** Node-RED executes arbitrary code, and no built-in login is
   configured in this package. The editor listens on loopback only and hides
   behind the standard homeui login (admin only); the service runs as the
   unprivileged `wb-node-red` user in a systemd sandbox.
4. **Zero-config.** The default flow is already wired to the local MQTT
   broker (`127.0.0.1:1883`) and the device tree (`/devices/#`) — open the
   editor and you immediately see live data; it includes a ready write
   example via the `.../on` subtopic (turning on the buzzer).

## What kind of package this is and how it is built

This is **not a metapackage** but a regular native `.deb`
(`Architecture: all`) that carries the entire Node-RED runtime inside. The
build (see `debian/rules`):

1. `npm ci` unfolds the dependency tree strictly from the pinned
   `vendor/package-lock.json` (Node-RED 5.0.0) — versions are reproducible,
   the build decides nothing on its own.
2. The only platform-specific binding is stripped from the tree
   (`@node-rs/bcrypt`; Node-RED falls back to pure-JS `bcryptjs`). The build
   fails if a single native `*.node` binary remains — that is what keeps the
   package architecture-independent.
3. The tree is packed into a reproducible tarball, and an SBOM is generated
   next to it — a machine-readable inventory of all vendored dependencies
   (CycloneDX, `usr/share/wb-node-red/sbom.cdx.json`) for vulnerability
   audits and CRA compliance.

Node.js itself is not vendored — it comes from the Wiren Board / Debian
repository (`Depends: nodejs`) along with its security updates.

At install time `postinst` extracts the tarball into
`/mnt/data/wb-node-red-runtime` (the swap is atomic: on a corrupt archive the
old runtime stays and the service restarts on it), seeds the default flow on
first install and declares the homeui gate.

## Editor access

The editor is served on a **dedicated HTTPS port** `21880`, which nginx
proxies behind the standard Wiren Board login (`auth_request`, **admin
only**). The homeui session cookie is bound to the host (not to the port), so
it reaches the port; the old `/node-red/` URL is kept as a redirect.
Trade-off: the dedicated port is not reachable through the WB Cloud tunnel —
access is local / LAN only.

The boundary is deliberately the *network* gate (no in-app `adminAuth`):
locally on the controller `127.0.0.1:1880` is an unauthenticated admin API,
and any local shell account can control Node-RED.

## Requirements

The editor gate is rendered by **homeui** from the declaration
`/usr/share/wb-mqtt-homeui/gates.d/node-red.json` shipped by this package as a
package drop-in (an admin copy of the same name in `/etc/wb-homeui/gates.d/`
overrides it). Up to 1.1.0 the declaration itself was shipped into `/etc`, so
upgrading sets an edited copy aside as `node-red.json.dpkg-bak`, which homeui
does not read — copy it back to `node-red.json` to keep the override. It
needs `wb-mqtt-homeui` with the service-gates mechanism (the exact minimum
version is pinned in `debian/control`). Without it the package installs and
the service runs, but **the editor stays unreachable** until homeui is
updated: the declaration sits inert and is picked up automatically on the
next homeui start — no reinstall or `dpkg-reconfigure` needed.

No TLS certificate needs to be prepared separately: homeui always keeps the
`/etc/ssl/sslip.pem` file (an expired placeholder until the real certificate
is issued — the browser will warn; the warning disappears once the
certificate arrives).

## How it is laid out on the controller

| What | Where |
|---|---|
| Node-RED code | `/mnt/data/wb-node-red-runtime/node_modules/` (extracted from the `.deb` at install time; overwritten on upgrade) |
| Settings template | `/usr/lib/wb-node-red/settings.js` (overwritten on upgrade) |
| Your settings overrides | `/mnt/data/wb-node-red/settings-user.js` (optional, survives upgrades; merged on top of the template — the loopback bind and `userDir` stay pinned) |
| Your flows / credentials / nodes | `/mnt/data/wb-node-red/` (seeded if empty; preserved) |
| Service | `wb-node-red.service` → `node red.js`, user `wb-node-red`, editor on `127.0.0.1:1880` (httpAdminRoot `/`) |
| Gate | `/usr/share/wb-mqtt-homeui/gates.d/node-red.json` — a package drop-in declaration (`internalPort 1880`, `externalPort 21880`, `role admin`, `menu.title`) that homeui renders into an nginx server block on port 21880 |
| Public access | `https://<homeui-host>:21880/` — the homeui admin gate → `127.0.0.1:1880`; the old `/node-red/` 302-redirects to `/open-node-red` |
| MQTT | default flow → `127.0.0.1:1883` |
| homeui menu | "Node-RED" under **Integrations** — homeui generates the item from the gate's `title` (no separate menu drop-in needed) |

## Repository layout

```
vendor/    — package.json + package-lock.json: the Node-RED and dependency pin
config/    — settings.js (settings template), flows.json (default flow),
             gates.d/node-red.json (gate declaration for homeui)
debian/    — packaging: rules (build), postinst/postrm (install/removal),
             the service unit, sysusers
nginx/     — drop-in with the 302 redirect from the old /node-red/
tests/     — pytest suite, run on every build (dh_auto_test):
             packaging invariants + postinst behavior tests
```
