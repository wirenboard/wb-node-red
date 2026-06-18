# 0004 — MQTT boot-timing via ip_nonlocal_bind, not After=docker.service

**Status:** Accepted (cold-boot validated on aat3d5fw)

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

**Provisioning is lazy, not at helper install.** The helper ships only the
mechanism (the `provision-mqtt` verb + the idempotent provisioner); it does NOT
touch mosquitto or the sysctl when the helper package itself is installed.
Instead, the postinst of the first *bridge* service that needs the broker (e.g.
wb-node-red) calls `provision-mqtt`. The provisioner is an idempotent singleton —
it creates the `wb` network only when absent and restarts mosquitto only when its
config drifts — so several bridge services can each call it without re-restarting
the broker. A controller running only host-networking services (which reach
mosquitto on localhost directly) never grows a gateway listener it does not use,
and there is nothing to "turn off". This is the opt-in/lazy default: the feature
isn't present until a service that needs it asks.

## Considered and rejected

- **`After=docker.service` on mosquitto** — orders the whole broker behind Docker
  on every boot. mosquitto is base infrastructure (drivers, wb-rules, homeui all
  depend on it); gating all controller MQTT behind a heavy, optional subsystem is
  an unacceptable blast radius. (This is what the first implementation shipped.)
- **bind `0.0.0.0:<port>` + firewall** — no ordering dependency, but the broker's
  safety then rests on a firewall rule; a lost/flushed rule exposes an anonymous
  listener to the LAN. Kept only as a fallback.

## Consequences

- **Validated on `aat3d5fw` (cold boot):** mosquitto under nonlocal_bind binds
  the gateway listener at early boot and serves it once Docker brings up the
  `wb` network. PASS.
- Needs team sign-off that the helper may set this sysctl and edit mosquitto
  config — or, cleaner long-term, ship both in the base WB mosquitto config so the
  helper never touches the broker at install (open team decision).
