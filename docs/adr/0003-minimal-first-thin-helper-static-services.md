# 0003 — Minimal-first: thin helper + static services

**Status:** Accepted (supersedes the implicit "fat helper" shape and ADR 0002)

## Context

The first implementation built a generic, dynamic helper: it read compose labels
(`descriptor`), allocated loopback ports at runtime (`ports`), generated nginx
server-blocks (`nginx`), and spliced the foreign `wb-diag-collect.conf` (`diag`).
That machinery is where almost all open review findings and unvalidated HITL items
clustered. The number of docker services WB will actually support is unknown — we
want many someday, but it may not happen.

## Decision

Optimise for a **small, curated set of services now**, and accept a rewrite later
if the catalog grows. Concretely:

- **Cut** the dynamic cores/wrappers: `descriptor`, `ports`, `nginx`, `diag`, and
  the CLI sugar verbs (status/logs/list/restart/update).
- **Keep a thin helper**: the `wb` network + mosquitto gateway listener
  (provisioned once), the shared `wb-docker-app@.service` systemd template, and
  the seed-if-absent of the user layer. The `docker compose up -d` / `down` lives
  in the systemd template (no separate Python compose runner). Verbs:
  `provision-mqtt`, `install <app>`, `remove <app>`, `reload-proxy`.
- Each **service is static**: it ships its own fixed internal port and its own
  pre-written nginx drop-in, instead of the helper computing them at runtime.
- **Monorepo now**: helper + services stay in one repo under `packaging/<pkg>/`;
  the repo-per-package split (WB convention) is deferred to WB Jenkins onboarding
  if/when it earns its keep. (This is why there is no per-package ADR.)

## Consequences

- Almost all open findings disappear with `diag`; HITL shrinks to the irreducible
  (auth_request on a foreign server-block; mosquitto + nonlocal_bind;
  container→broker connectivity).
- The cost is duplication across service packages (each authors its own nginx
  drop-in and picks its own port). Tolerable at a handful of services; growing
  duplication is the **explicit trigger** to re-introduce a generic helper and
  revisit this decision.
