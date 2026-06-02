# 1. Walking skeleton — `apt install wb-node-red` brings up a container

Labels: `ready-for-agent`
Type: AFK

## Parent

[doc/wb-docker-apps-prd.md](../wb-docker-apps-prd.md) — PRD: wb-docker-apps. Architecture and rationale: [doc/wb-docker-apps-design.md](../wb-docker-apps-design.md) §3.1, §3.2, §3.5, §3.5.1.

## What to build

The thinnest end-to-end path that proves the whole delivery model: a single
`apt install wb-node-red` pulls in the shared helper and a Docker, deploys a
Node-RED container, and starts it under systemd.

Two packages:

- **`wb-docker-app`** (shared helper, installed once as a dependency, refcounted
  by apt): a minimal CLI with `install`/`remove` subcommands, a compose-runner
  that calls `docker compose -f <base> [-f <override>] up -d/down` with a fixed
  project name, and **one** templated systemd unit `wb-docker-app@.service`
  (`%i` → path to the app's compose; `ExecStart=docker compose up -d`;
  `After=docker.service`). Helper is Python 3; maintainer scripts are thin sh
  glue that call the CLI.
- **`wb-node-red`** (`Architecture: all`): `control` with
  `Depends: docker-ce, wb-docker-app`; a base `docker-compose.yml` carrying the
  service definition with `wb.*` labels and `restart: unless-stopped`; thin sh
  `postinst` (`wb-docker-app install node-red`) and `prerm`
  (`wb-docker-app remove node-red`).

For this slice the image may be an upstream/mirror tag and the container may be
reached directly on its port (nginx proxy, auth, MQTT, and persistence polish
come in later slices). Goal is to demonstrate the package → helper → compose →
systemd path works end-to-end and is idempotent.

## Acceptance criteria

- [ ] `apt install wb-node-red` on a controller (already on the `docker-ce`
      repack) auto-installs `wb-docker-app` and brings up a running Node-RED
      container in one command.
- [ ] The container has `restart: unless-stopped`; it comes back after a
      controller reboot and after a manual `docker kill`.
- [ ] `systemctl status wb-docker-app@node-red` shows the service; the unit is
      a single template instance, not a per-app unit file.
- [ ] Node-RED responds on its port from the controller (e.g. `curl`).
- [ ] `apt purge wb-node-red` stops and removes the container/unit cleanly and
      leaves `docker-ce`/`wb-docker-app` able to serve other apps.
- [ ] Re-running install/remove is idempotent (no error on second run).

## Blocked by

- None — can start immediately.
