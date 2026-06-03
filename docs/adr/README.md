# Architecture Decision Records

The **15 foundational decisions** (delivery model, registry, mirror/derived,
compose+systemd, base/override layout, port-for-all, auth_request, MQTT network,
updates, naming, repo structure) are recorded and justified in
[doc/wb-docker-apps-design.md §3](../../doc/wb-docker-apps-design.md). Treat that
document as ADR-0000 — do not duplicate it here.

This directory captures decisions taken **during or after implementation** that
the design doc doesn't cover, or that **supersede** a design decision.

## Format

One file per decision: `NNNN-kebab-title.md`, with: **Status** (Proposed /
Accepted / Superseded-by-NNNN), **Context**, **Decision**, **Consequences**.
Keep them short. Number sequentially.

## Index

- [0001 — Helper language and the testability seam](0001-helper-language-and-testability-seam.md) — Accepted
- [0002 — wb-diag-collect integration via in-place splice](0002-wb-diag-collect-integration.md) — Proposed (contentious)
