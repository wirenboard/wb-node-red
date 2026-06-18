---
status: accepted
---
# 0006 — Node-RED: mirror upstream + vendored pure-JS palette (no derived image)

## Context

The plan (design.md §3.4) made Node-RED a DERIVED image on the assumption that the
WB palette `node-red-contrib-wirenboard` carries native modules (ABI → must be
compiled into the image). Verified: the palette (v3.11.0) and its full dependency
tree (92 packages, incl. `mqtt`, `request`) are **100% pure JavaScript** — no
`binding.gyp`, no `*.node`, no `gypfile`, no native install scripts.

PoC on aat3d5fw (armv7l): vanilla `nodered/node-red:4.0.2` (arm/v7) with the
palette dropped as **plain files** into the userDir `node_modules` loaded cleanly
— all 7 palette node types registered (`enabled: true`), zero load errors, no npm
at runtime.

## Decision

Node-RED ships as **mirror + vendored palette**, NOT a derived image:

- Image = byte-for-byte **mirror** of upstream `nodered/node-red` in the WB
  registry (`pull→tag→push`; no Dockerfile, no image build).
- The palette is `npm install`-ed at **`.deb` build time** (CI) and vendored as
  plain files into the package; delivered to the container at install. No
  npm/internet at runtime → sovereignty/offline preserved.
- Package stays `Architecture: all` (palette is pure JS, arch-independent).

This **supersedes design.md §3.4 "Node-RED → derived"**. No current service needs
a derived image, so the image-pipeline is dropped entirely.

## Consequences

- Maintenance: a Node-RED bump = re-mirror (≈0); a palette bump = re-vendor the
  `.deb` (cheap npm step, no Docker build), and the two are decoupled. Removes the
  §8 "derived image" recurring cost.
- **Delivery (resolved → variant b):** the palette is `npm ci`-ed into the
  `.deb` at build time under `/usr/lib/wb-docker-app/node-red/palette/node_modules/`
  and REFRESHED (overwritten) into the user's `/data/node_modules` on every
  install/upgrade by the helper (`seeding.refresh_tree`). The refresh touches
  only the paths the package ships, so a user-installed palette in the same
  `node_modules` survives. Chosen over variant (a) (`nodesDir` + a shipped
  `settings.js`) because it matches the PoC exactly and keeps `settings.js`
  unshipped (Node-RED's default, editor open behind the nginx gate). *Caveat:* if
  a future palette version drops a transitive dep, the stale file lingers in
  `/data/node_modules` (harmless orphan) — acceptable for now.
- **Mirror tag is pending infra:** the WB-registry mirror of
  `nodered/node-red:4.0.2` must be populated (pull→tag→push) — parked in
  doc/issues/06. Until then the compose pins the intended mirror ref and a PoC
  retags docker.io's vanilla image to it by hand.
- Build-time vendoring reproduced locally: `npm ci` of the pinned manifest yields
  93 pure-JS packages (0 `*.node`/`binding.gyp`, 0 compile hooks); the
  `package-lock.json` is committed, the installed tree is not.
- Notes (track, not blockers): the palette is community-maintained (`andreypopov`)
  and pulls the deprecated `request` (pure JS, works).
