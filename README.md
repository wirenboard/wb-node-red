# wb-node-red

One-command `apt install wb-node-red` to run [Node-RED](https://nodered.org/)
**natively** on a [Wiren Board](https://wirenboard.com/) controller — no Docker,
ready to work out of the box.

## What you get

- **Node-RED on the host**, run as a dedicated unprivileged systemd service
  (`wb-node-red.service`). Node.js comes from the distribution (`Depends:
  nodejs`), so its security updates flow from the Wiren Board / Debian
  repository rather than being bundled and owned by this package.
- **The Wiren Board palette** (`node-red-contrib-wirenboard`) and a **default
  flow** already wired to the local MQTT broker (`127.0.0.1:1883`) and the
  device tree (`/devices/#`) — open the editor and you immediately see live
  controller data.
- **Behind the homeui login.** The editor binds to loopback only and is served
  on a **dedicated HTTPS port** that nginx reverse-proxies behind the standard
  Wiren Board login (`auth_request`, **admin only** — Node-RED can run arbitrary
  code). The homeui session cookie is host-scoped (port-agnostic), so it reaches
  the port; the old `/node-red/` URL is kept as a redirect to it. Trade-off: a
  dedicated port is not reachable through the WB Cloud tunnel — local/LAN access.
- **Your data is safe across upgrades.** Flows, credentials and any nodes you
  install live under `/mnt/data/wb-node-red`, the partition that survives a
  firmware reflash. `apt upgrade` swaps the code and restarts; your data stays.

## Why native (not Docker)

Node-RED is a Node.js app, and the EU Cyber Resilience Act makes whoever ships
a product responsible for patching every bundled dependency for years. A fat
container image carries its own copy of Node.js / OpenSSL / system libraries —
each a separate thing to track and patch. Running natively on the controller
lets the **distribution-maintained Node.js** carry that load (its patches are
shared by everything on the system), and it is also the install path Wiren
Board officially recommends for Node-RED on current firmware.

## How it works

| Concern | Where |
|---|---|
| Node-RED + palette code | `/mnt/data/wb-node-red-runtime/node_modules/` (extracted from the `.deb` at install — keeps the ~120 MB tree off the small root; overwritten on upgrade) |
| settings template | `/usr/lib/wb-node-red/settings.js` (overwritten on upgrade) |
| your flows / credentials / nodes | `/mnt/data/wb-node-red/` (seeded if-absent, preserved) |
| service | `wb-node-red.service` → `node red.js`, user `wb-node-red`, editor on `127.0.0.1:1880` (httpAdminRoot `/`) |
| public access | `https://<homeui-host>:21880/` — nginx server block in `conf.d`, admin auth-gate → `127.0.0.1:1880`; old `/node-red/` 302-redirects there |
| MQTT | default flow → `127.0.0.1:1883` |
| homeui menu | "Node-RED" under **Integrations** — a drop-in placed by postinst into homeui's `custom-menu/` (only on homeui ≥ 2.235.4; `isExternal` renders a full-page anchor and `openInNewTab` opens it in a separate reusable tab so homeui stays put) |

The Node-RED runtime and the palette are **vendored into the `.deb` at build
time** (`npm ci --omit=dev --omit=optional` over `vendor/package.json`), so installation
does no npm download and no native compilation on the controller, and the
package stays `Architecture: all`.

## Layout

```
debian/                  # the wb-node-red package (control, rules, maintainer scripts, unit)
vendor/package.json      # pinned node-red + node-red-contrib-wirenboard (built in CI)
config/settings.js       # Node-RED settings template (package-owned)
config/flows.json        # default flow, seeded into the user layer if-absent
nginx/wb-node-red.conf            # :21880 server block (conf.d), admin auth-gate
nginx/wb-node-red-auth-cache.conf # cached /auth/check zone + guard maps (conf.d)
nginx/wb-node-red-redirect.conf   # old /node-red/ -> 302 to the port (homeui block)
apt/                     # unattended-upgrades exclusion
tests/test_packaging.py  # packaging-contract invariants
Jenkinsfile              # buildDebArchAll()
docs/native-node-red_plan.md
```

## Development

```bash
python3 -m pytest tests/          # packaging-contract checks (no build needed)
shellcheck debian/postinst debian/postrm
```

The package builds on jenkins.wirenboard.com (`buildDebArchAll`). The build
runs `npm ci` against `vendor/`, so the build environment needs registry
access (or a committed `vendor/node_modules` if the WB builder is offline — see
the plan). Real validation is an install on a controller: the service starts,
the editor loads behind the admin auth-gate, the default flow shows live
`/devices/#` data, and `apt upgrade`/`remove` behave.
