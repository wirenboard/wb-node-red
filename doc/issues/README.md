# wb-docker-apps — implementation issues

Tracer-bullet vertical slices derived from
[../wb-docker-apps-prd.md](../wb-docker-apps-prd.md) and
[../wb-docker-apps-design.md](../wb-docker-apps-design.md). Each slice cuts
end-to-end through package → helper → system integration (compose / systemd /
nginx / mosquitto), not horizontally through a single module.

All issues carry the `ready-for-agent` label (AFK-ready) unless the HITL note
applies — those need validation on a real controller or external infra.

| # | Title | Type | Blocked by |
|---|-------|------|------------|
| [1](01-walking-skeleton-apt-install.md) | Walking skeleton — `apt install wb-node-red` brings up a container | AFK | — |
| [2](02-persistent-overridable-layout.md) | Persistent & user-overridable layout on `/mnt/data` | AFK | #1 |
| [3](03-web-access-behind-wb-login.md) | Web access behind the WB login | HITL | #1 |
| [4](04-mqtt-connectivity-docker-network.md) | MQTT connectivity via a dedicated docker network | HITL | #1 |
| [5](05-lifecycle-observability-commands.md) | Lifecycle & observability commands | AFK | #1 |
| [6](06-derived-node-red-image.md) | Derived Node-RED image — WB integration out of the box | HITL | #4 (+ WB-registry infra) |
| [7](07-second-service-isolation.md) | Second-service isolation (template proof) | AFK | #1, #4 |

## Dependency graph

```
#1 walking skeleton
 ├─ #2 persistent layout
 ├─ #3 web access behind login   (HITL)
 ├─ #4 MQTT connectivity          (HITL)
 │    ├─ #6 derived image         (HITL, + registry infra)
 │    └─ #7 second-service isolation
 └─ #5 lifecycle & observability
```
