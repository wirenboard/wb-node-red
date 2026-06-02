# 6. Derived Node-RED image — WB integration out of the box

Labels: `ready-for-agent`
Type: HITL

## Parent

[doc/wb-docker-apps-prd.md](../wb-docker-apps-prd.md) — PRD: wb-docker-apps.
Architecture and rationale: [doc/wb-docker-apps-design.md](../wb-docker-apps-design.md) §3.3, §3.4, §3.12, §3.13.

## What to build

Ship a Node-RED image that works on a WB controller out of the box and offline,
without runtime access to docker.io or registry.npmjs.org.

- **Derived image**: `FROM` the upstream `nodered/node-red` tag, with
  `node-red-contrib-wirenboard` (WB palette), `settings.js`, and a default
  `flows.json` whose broker node points at the `wb` network gateway
  (`172.29.0.1:11883`, from slice #4) baked in. The npm dependency is pulled
  **at build time** into the WB registry, so runtime needs no npm.
- **Mirror** path: byte-for-byte retag of vanilla upstream images for services
  that need no WB integration.
- **image-pipeline** in the service repo: `docker build` (derived) / pull→tag→
  push (mirror) into `registry.wirenboard.com`, separate from the `.deb`
  Jenkins build.
- `wb-node-red` base compose pins the image by digest/tag
  (`registry.wirenboard.com/wb/node-red:<ver>-wbN` or `@sha256:…`); versioning
  follows §3.12 (package version = app version + WB revision; derived tag =
  upstream + `-wbN`).

## Acceptance criteria

- [ ] Derived image builds with the WB palette + `settings.js` + default
      `flows.json` (broker node → `172.29.0.1:11883`) embedded.
- [ ] On a fresh install with no internet beyond WB infra, Node-RED starts, the
      WB nodes are already in the palette, and the broker node connects — no
      Docker Hub / npmjs access at runtime.
- [ ] Mirror retag produces a byte-identical vanilla image in the WB registry.
- [ ] image-pipeline (build/retag + push) lives in the service repo, separate
      from the `.deb` build.
- [ ] `wb-node-red` pins the image by digest/tag with the `-wbN` versioning
      scheme.

## Blocked by

- #4 (MQTT connectivity — supplies the broker gateway address baked into the
  default `flows.json`)

> **HITL / infra prerequisite:** depends on WB-registry and the separate image
> CI-pipeline being stood up (PRD "Out of Scope" + Further Notes #2). Confirm
> the exact mechanism for baking the palette and default `flows.json` against
> the real `nodered/node-red` image (design.md §5).
