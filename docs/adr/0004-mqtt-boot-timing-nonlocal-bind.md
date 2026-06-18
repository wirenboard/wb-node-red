# 0004 — MQTT boot-timing via ip_nonlocal_bind, not After=docker.service

**Status:** Proposed (pending controller validation, HITL)

## Context

Containers reach the broker via a mosquitto listener bound to the gateway of the
dedicated `wb` docker network (e.g. `172.29.0.1:11883`). That gateway IP does not
exist until Docker has created the `wb` network, but mosquitto starts early at
boot — so the listener can fail to bind on a cold boot.

## Decision

Set `net.ipv4.ip_nonlocal_bind=1` (sysctl drop-in shipped by the helper) and keep
the listener on the gateway address. mosquitto binds the not-yet-existent gateway
IP at its normal early start (the same trick keepalived/HAProxy use for VIPs); the
listener goes live the moment Docker brings up the `wb` network. mosquitto keeps
its normal early boot.

## Considered and rejected

- **`After=docker.service` on mosquitto** — orders the whole broker behind Docker
  on every boot. mosquitto is base infrastructure (drivers, wb-rules, homeui all
  depend on it); gating all controller MQTT behind a heavy, optional subsystem is
  an unacceptable blast radius. (This is what the first implementation shipped.)
- **bind `0.0.0.0:<port>` + firewall** — no ordering dependency, but the broker's
  safety then rests on a firewall rule; a lost/flushed rule exposes an anonymous
  listener to the LAN. Kept only as a fallback.

## Consequences

- Must be validated on a real controller (`aat3d5fw`): that mosquitto under
  nonlocal_bind actually serves the gateway after the network appears, on a cold
  boot.
- Needs team sign-off that the helper may set this sysctl and edit mosquitto
  config — or, cleaner long-term, ship both in the base WB mosquitto config so the
  helper never touches the broker at install (open team decision).
