# 5. Lifecycle & observability commands

Labels: `ready-for-agent`
Type: AFK

## Parent

[doc/wb-docker-apps-prd.md](../wb-docker-apps-prd.md) — PRD: wb-docker-apps.
Architecture and rationale: [doc/wb-docker-apps-design.md](../wb-docker-apps-design.md) §3.5.1, §3.11; PRD modules **G** (systemd instance manager), **I** (CLI/orchestrator).

## What to build

Round out day-2 operations so a service behaves like any native WB service and
upgrades happen only on purpose.

- User-facing CLI verbs on the helper (thin glue over modules A–H):
  `status`, `logs`, `restart`, `update`, `list`. `list`/`status` introspect
  running containers via `wb.*` labels (self-describing), so they need no extra
  state.
- **Explicit upgrade**: `apt upgrade wb-node-red` ships a new `.deb` with a
  bumped image tag in base → postinst runs `compose pull` + `up -d`, recreating
  the container. These packages are **excluded from unattended-upgrades** so
  automations are never restarted unexpectedly.
- Document downgrade/rollback (`apt downgrade`, works while the image is still
  in cache/registry) and a "back up `/mnt/data/wb-docker-apps/<app>/data`
  before a major upgrade" warning.
- Integrate with `wb-diag-collect` so service status/logs land in the
  diagnostic archive.

## Acceptance criteria

- [ ] `wb-docker-app list` shows installed docker services from labels;
      `status`/`logs`/`restart`/`update` operate on a named app.
- [ ] `apt upgrade wb-node-red` with a bumped tag pulls the new image and
      recreates the container; an unrelated `apt upgrade` / unattended run does
      **not** touch it.
- [ ] `systemctl status/restart wb-docker-app@node-red` works (module G).
- [ ] Service status and logs appear in the `wb-diag-collect` archive.
- [ ] Downgrade path and pre-major-upgrade backup are documented.

## Blocked by

- #1 (walking skeleton)
