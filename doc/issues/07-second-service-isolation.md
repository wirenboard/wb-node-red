# 7. Second-service isolation (template proof)

Labels: `ready-for-agent`
Type: AFK

## Parent

[doc/wb-docker-apps-prd.md](../wb-docker-apps-prd.md) — PRD: wb-docker-apps.
Architecture and rationale: [doc/wb-docker-apps-design.md](../wb-docker-apps-design.md) §3.1, §3.2, §3.7, §3.10.

## What to build

Prove the "thin per-app package + shared helper" model: a second service
declared purely by the template (compose + `wb.*` labels) installs alongside
Node-RED without disturbing it, the broker, or the `wb` network.

- Add a second service package (a minimal stub/utility service is fine — it
  need not be Home Assistant) built only from base compose + `wb.*` labels and
  thin sh maintainer scripts, reusing the existing helper with **no copy-pasted
  lifecycle logic**.
- Installing it must reuse the already-provisioned `wb` network and mosquitto
  listener — no second mosquitto restart, no network re-create.
- nginx server-block and internal-port allocation for the second service must
  not collide with Node-RED's.

## Acceptance criteria

- [ ] A second service installs via `apt install` reusing `wb-docker-app` with
      no duplicated lifecycle logic in its package.
- [ ] Installing the second service does **not** restart mosquitto and does
      **not** re-create or modify the `wb` network.
- [ ] Both services run concurrently, each on its own public port behind the
      WB login, with distinct internal loopback ports (no collision).
- [ ] Removing one service (`apt purge`) leaves the other running and the
      broker/network intact.
- [ ] The new package is demonstrably "by template" — adding it required only
      compose + labels + thin glue, no changes to helper logic.

## Blocked by

- #1 (walking skeleton)
- #4 (MQTT connectivity — the shared network/listener whose non-disturbance is
  under test)
