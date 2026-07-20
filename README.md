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

At install time the tarball is extracted into
`/mnt/data/wb-node-red-runtime` (the swap is atomic: on a corrupt archive the
old runtime stays and the service restarts on it).

## How it is laid out on the controller

| What | Where |
|---|---|
| Node-RED code | `/mnt/data/wb-node-red-runtime/node_modules/` (extracted from the `.deb` at install time; overwritten on upgrade) |
| Service | `wb-node-red.service` → `node red.js`, user `wb-node-red`, editor on `127.0.0.1:1880` (httpAdminRoot `/`) |

## Repository layout

```
vendor/    — package.json + package-lock.json: the Node-RED and dependency pin
debian/    — packaging: rules (build), the service unit, sysusers
tests/     — pytest suite, run on every build (dh_auto_test):
             packaging invariants
```
