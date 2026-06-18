# CONTEXT — wb-docker-apps domain language

The ubiquitous language for this project. Use these exact terms in code, tests,
issues, and commits. The full design lives in
[doc/wb-docker-apps-design.md](doc/wb-docker-apps-design.md); the direction
decisions live in [docs/adr/](docs/adr/). This file is the glossary.

## What the project is

A way to install containerized services (Node-RED first, maybe Home Assistant and
a few others later) on **Wiren Board** controllers with one `apt` command, on top
of an already-shipped `docker-ce` repack. Deliberately built **minimal for a
small, curated set of services** (ADR 0003): a *thin* shared helper + *static*
per-service packages. If the catalog ever grows large, the dynamic "framework" is
re-introduced then — not now.

## Core terms

- **helper** / **`wb-docker-app`** — the single thin shared package (Python
  package `wb_docker_app`), installed once as a dependency. Owns only the
  irreducible shared host integration: the `wb` docker network + mosquitto gateway
  listener, the shared systemd template, seed-if-absent of the user layer, and the
  `docker compose` runner. Verbs: `provision-mqtt`, `install <app>`,
  `remove <app>`.
- **service package** / **`wb-<service>`** — a thin per-app `.deb`
  (`wb-node-red`, …), `Architecture: all`. Carries a STATIC base compose (fixed
  ports), a STATIC nginx drop-in, default config, and thin maintainer scripts that
  call the helper. `Depends: docker-ce, wb-docker-app`.
- **static service** — the chosen shape (ADR 0003): a service ships its own fixed
  internal port and its own pre-written nginx drop-in, instead of the helper
  allocating ports and generating nginx at runtime.
- **base / override layout** — *base* compose is package-owned under
  `/usr/lib/wb-docker-app/<app>/` (overwritten on upgrade); the *user layer*
  (`docker-compose.override.yml`, `.env`, `data/`) lives under
  `/mnt/data/wb-docker-apps/<app>/`, **seeded only-if-absent** (user edits survive
  upgrades). Runtime = `docker compose -f base -f override`.
- **port-for-all** — every service is reverse-proxied on its own public port,
  never a subpath; the container binds only `127.0.0.1:<internal>`. The internal
  port is **fixed per service** (e.g. node-red → 21880), not allocated.
- **`auth_request` / `required_user_type`** — a service ships a standalone nginx
  `server{}` (in `sites-available/`, symlinked into `sites-enabled/`) reusing the
  homeui login. `/auth/check` lives in the homeui server block, so the service
  block defines its **own** internal `location = /auth/check` proxying to the
  global `wb-homeui-back` upstream; gates its proxied location on a role
  (Node-RED → `admin`, RCE-capable); and on 401 redirects to the homeui login on
  a port-less `$host` (no same-port loop). Controller-driven correction — see
  ADR 0005.
- **`wb` network + gateway listener** — a dedicated docker network (fixed
  subnet/gateway, e.g. `172.29.0.0/24` / `172.29.0.1`); mosquitto gets an extra
  listener bound to the gateway (e.g. `11883`) so containers reach the broker
  without exposing `1883` to the LAN. Provisioned **once**, at helper install.
- **ip_nonlocal_bind** — the boot-timing mechanism (ADR 0004): the helper sets
  `net.ipv4.ip_nonlocal_bind=1` so mosquitto can bind the gateway listener even
  before Docker has created the `wb` network, keeping mosquitto's normal early
  boot. Replaces the rejected `After=docker.service` ordering.
- **mirror vs derived image** — *mirror* = byte-for-byte retag of a vanilla
  upstream image into the WB registry; *derived* = `FROM upstream` + only what
  needs building baked in. **Node-RED uses MIRROR, not derived** (ADR 0006): its
  palette `node-red-contrib-wirenboard` is pure JS (PoC-verified on armv7l), so
  the image is a byte-for-byte mirror of upstream and the palette is vendored as
  plain files into the `.deb` (npm install at build time). The default
  `flows.json` (broker node → `172.29.0.1:11883` + a `/devices/#` example) is
  also shipped by the `.deb` and **seeded into `/mnt/data/.../<app>/data/`
  only-if-absent** (it lives in the bind-mounted `/data`, so baking it into an
  image would be shadowed). `settings.js` is **not** shipped: Node-RED
  auto-generates a default with the editor open (no in-app auth), which is right
  behind the nginx gate. *derived* (`FROM upstream` + build) stays available for
  a future service that genuinely needs native compilation — none does today.

## The helper's shape (modules, after the minimal-first cut)

Kept:
- **mqtt** (`mqtt.py`) — pure: `wb` network params, mosquitto listener text, the
  `ip_nonlocal_bind` sysctl text.
- **seeding** (`seeding.py`) — pure: seed-if-absent user layout.
- **systemd** (`systemd.py`), **mqtt_provision** (`mqtt_provision.py`), **cli**
  (`cli.py`) — thin wrappers over the injected **`Runner`** seam (`runner.py`);
  validated on a controller (HITL). The `docker compose up -d` / `down` itself is
  issued by the `wb-docker-app@.service` systemd template, not by a Python module.

Cut (ADR 0003): `descriptor` reader, `nginx` generator, `ports` allocator,
`diag` (wb-diag-collect splice), and the CLI sugar verbs. Each service now ships
its own nginx drop-in and fixed port.

## HITL vs AFK

- **AFK** — implementable and self-verifiable, no human/controller needed.
- **HITL** — needs a real controller (`aat3d5fw`, wb-2602/wb7) or WB infra. Open
  HITL: does `auth_request` gate a foreign server-block; does mosquitto under
  `ip_nonlocal_bind` serve the gateway after Docker brings the network up (cold
  boot); container→broker connectivity. Plus a team decision: may the helper touch
  mosquitto config / set the sysctl, or should both ship in the base WB config.
