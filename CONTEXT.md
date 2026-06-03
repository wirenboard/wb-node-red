# CONTEXT — wb-docker-apps domain language

The ubiquitous language for this project. Agents (and humans) should use these
exact terms in code, tests, issues, and commits so the vocabulary stays
consistent. The full design and the 15 foundational decisions live in
[doc/wb-docker-apps-design.md](doc/wb-docker-apps-design.md); this file is the
glossary, not a re-explanation.

## What the project is

A way to install containerized services (Node-RED first, Home Assistant and
others later) on **Wiren Board** controllers with one `apt` command, layered on
top of an already-shipped `docker-ce` repack. One shared helper package carries
all the lifecycle logic; each service is a thin `.deb`.

## Core terms

- **helper** / **`wb-docker-app`** — the single shared package (and Python
  package `wb_docker_app`) that owns all lifecycle logic. Installed once,
  automatically, as a dependency; refcounted by apt.
- **service package** / **`wb-<service>`** — a thin per-app `.deb`
  (`wb-node-red`, `wb-echo`, …), `Architecture: all`, carrying only a base
  compose + `wb.*` labels + thin maintainer scripts. `Depends: docker-ce,
  wb-docker-app`.
- **`AppDescriptor`** — the in-memory description of a WB-managed service
  (app slug, title, image, public port, internal port, proxy role). Produced by
  the descriptor reader from a service's compose labels; the single source of
  truth that flows between the helper's cores.
- **`wb.*` labels** — the contract a service package declares in its base
  compose: `wb.app`, `wb.title`, `wb.proxy.port`, `wb.proxy.role`. The helper
  reads these to drive everything (nginx, listing, status).
- **base / override layout** — *base* compose is package-owned under
  `/usr/lib/wb-docker-app/<app>/` (overwritten on upgrade); the *user layer*
  (`docker-compose.override.yml`, `.env`, `data/`) lives under
  `/mnt/data/wb-docker-apps/<app>/` and is **seeded only-if-absent** (user edits
  survive upgrades). Runtime = `docker compose -f base -f override`.
- **port-for-all** — every service is reverse-proxied on its own public port
  (`wb.proxy.port`), never on a subpath. The container binds only to
  `127.0.0.1:<internal>`; the helper allocates the internal loopback port.
- **`auth_request` / `required_user_type`** — the rendered nginx server-block
  reuses the homeui login via `auth_request /auth/check` gated to a role
  (Node-RED → `admin`, because it is RCE-capable). 401 → redirect to the homeui
  login form.
- **`wb` network + gateway listener** — a dedicated docker network (fixed
  subnet/gateway, e.g. `172.29.0.0/24` / `172.29.0.1`); mosquitto gets an extra
  listener bound to the gateway (e.g. `11883`) so containers reach the broker
  without exposing `1883` to the LAN. Provisioned **once**, at helper install.
- **mirror vs derived image** — *mirror* = byte-for-byte retag of a vanilla
  upstream image into the WB registry; *derived* = `FROM upstream` + WB
  integration baked in (Node-RED → WB palette, `settings.js`, default
  `flows.json` pointing at the gateway). Versioned `<upstream>-wbN`.

## The helper's shape (modules)

Pure cores (no system access, unit-tested with deterministic in/out):
- **A descriptor reader** (`descriptor.py`) — compose config → `AppDescriptor`.
- **B nginx renderer** (`nginx.py`) — `AppDescriptor` → server-block.
- **C port allocator** (`ports.py`) — collision-free, idempotent loopback ports.
- **D mqtt/net renderer** (`mqtt.py`) — mosquitto listener + `wb` network params.
- **E seeding** (`seeding.py`) — seed-if-absent user layout.

Thin wrappers (reach the system only through the injected **`Runner`** seam in
`runner.py`; validated on a controller, HITL):
- **F compose-runner** (`compose.py`), **G systemd manager** (`systemd.py`),
  **H mqtt provisioner** (`mqtt_provision.py`), **I CLI/orchestrator**
  (`cli.py`), plus **diag** (`diag.py`, wb-diag-collect integration).

## HITL vs AFK

- **AFK** — implementable and self-verifiable with no human decision.
- **HITL** — needs a real controller (candidate `aat3d5fw`, wb-2602/wb7) or
  external infra (WB registry) to verify. The pure cores are AFK; the wrappers
  and packaging carry HITL verification tails. See
  [doc/wb-docker-apps-design.md §6](doc/wb-docker-apps-design.md).
