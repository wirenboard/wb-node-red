# 4. MQTT connectivity via a dedicated docker network

Labels: `ready-for-agent`
Type: HITL

## Parent

[doc/wb-docker-apps-prd.md](../wb-docker-apps-prd.md) — PRD: wb-docker-apps.
Architecture and rationale: [doc/wb-docker-apps-design.md](../wb-docker-apps-design.md) §3.7; PRD modules **D** (MQTT/network config render), **H** (MQTT provisioner).

## What to build

Give containers a deterministic, non-LAN-exposed path to the mosquitto broker,
solving the documented "containers can't see the broker" pain without opening
`1883` to `0.0.0.0`.

At **helper install time, once per system** (not per service):

- Create docker network `wb` with a fixed subnet/gateway (e.g. `172.29.0.0/24`,
  gateway `172.29.0.1`), idempotently (module H).
- Render and install a mosquitto drop-in adding a listener on the gateway
  (e.g. `listener 11883 172.29.0.1`) (module D, pure render).
- Add an `After=docker.service` drop-in for mosquitto so the docker network is
  up before the broker binds to the gateway.
- Perform a **single** mosquitto `restart` (SIGHUP/reload does not open new
  listeners). Installing further services must not restart mosquitto again.

Services attach to network `wb` and reach the broker at `172.29.0.1:11883`.

## Acceptance criteria

- [ ] After installing the helper, `docker network ls` shows `wb` with the
      fixed subnet/gateway; re-running provisioning is idempotent.
- [ ] mosquitto has a listener on the gateway; broker is reachable from a
      container on network `wb` at `172.29.0.1:11883`, and `1883` is **not**
      newly exposed on `0.0.0.0`.
- [ ] mosquitto is restarted exactly once (at helper install); installing a
      second service does not restart it.
- [ ] The listener survives a controller reboot (boot order: docker network up
      before broker binds).
- [ ] Unit tests for the MQTT/network render (module D): listener text and
      network params from given subnet/gateway, stable output.

## Blocked by

- #1 (walking skeleton)

> **HITL:** requires controller validation (design.md §6 item 2 & §5): confirm
> reload-vs-restart behavior and gateway-bind on boot; pick a `wb` subnet that
> does not collide with subnets already used on WB controllers; approve the
> mosquitto config modification by the helper (PRD Further Notes #1, #3).
