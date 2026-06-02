# 2. Persistent & user-overridable layout on `/mnt/data`

Labels: `ready-for-agent`
Type: AFK

## Parent

[doc/wb-docker-apps-prd.md](../wb-docker-apps-prd.md) — PRD: wb-docker-apps.
Architecture and rationale: [doc/wb-docker-apps-design.md](../wb-docker-apps-design.md) §3.6; PRD module **E** (idempotent seeding).

## What to build

Split the compose layout so `apt upgrade` can bump the image and defaults while
user edits and service data survive untouched.

- **base** (`docker-compose.yml` with image tag, ports, labels) lives in
  `/usr/lib/wb-docker-app/<app>/`, owned by the package, freely overwritten on
  upgrade.
- **user layer** (`docker-compose.override.yml`, `.env`, `data/`) lives on
  `/mnt/data/wb-docker-apps/<app>/`, **seeded only-if-absent** from templates,
  never a dpkg conffile (no conffile prompts, upgrade never touches it).
- Runtime invocation becomes `docker compose -f base -f override up -d`.
- Service data (Node-RED flows) is a bind-mount into `data/`.

The seeding logic is a pure, unit-testable core (module E): given a target
root, it creates the directory tree and copies templates only when files are
missing, and is idempotent on repeat.

## Acceptance criteria

- [ ] base compose is installed under `/usr/lib/wb-docker-app/node-red/` and is
      package-owned (replaced on upgrade).
- [ ] On install, `override.yml`/`.env`/`data/` are seeded under
      `/mnt/data/wb-docker-apps/node-red/` only if they don't already exist.
- [ ] Editing the user `override.yml`/`.env` and then reinstalling or upgrading
      `wb-node-red` does **not** clobber those edits.
- [ ] Node-RED flows persist across `apt purge` + reinstall and across upgrade.
- [ ] Unit tests for the seeding core: creates-when-absent, never-overwrites,
      idempotent-on-repeat, against a temp root (no docker/systemd needed).
- [ ] No dpkg conffile prompt appears under non-interactive `apt`.

## Blocked by

- #1 (walking skeleton)
