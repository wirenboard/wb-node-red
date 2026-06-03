# 0002 — wb-diag-collect integration via in-place config splice

**Status:** Proposed (contentious — see consequences)

## Context

Issue #5 requires WB-managed services to appear in the `wb-diag-collect`
diagnostic archive (per-service `systemctl status` + logs). Inspecting the
released `wb-diag-collect` showed it reads **only** a single monolithic
`/usr/share/wb-diag-collect/wb-diag-collect.conf` — **no `conf.d` merge**. So a
drop-in fragment alone can never satisfy the acceptance criterion.

## Decision

`wb-docker-app` ships a collector script and, from its maintainer scripts:

- **postinst** surgically splices a sentinel-delimited block (registering the
  collector as a `commands` entry) into the foreign `wb-diag-collect.conf`,
  using pure-text splicing (PyYAML drops comments and only PyYAML is a
  dependency); **prerm** removes it.
- An apt **`DPkg::Post-Invoke` hook** re-inserts the block after any apt
  transaction, because a `wb-diag-collect` upgrade would overwrite the file.
- A `conf.d` drop-in is also shipped as the canonical description and the
  forward path for when upstream gains a merge.

`register()`/`deregister()` are idempotent and reversible.

## Consequences

- **We modify a file owned by another package.** This is the project's most
  contentious call and currently owns the only MAJOR open review finding plus
  several minor ones (the text-splice makes assumptions about the upstream
  file's exact YAML layout — flow-style `commands:`, indentation, trailing
  newline). See `doc/.afk-pipeline/wb-docker-apps.audit.md`.
- **Preferred long-term fix:** add `conf.d`/include support to `wb-diag-collect`
  upstream and drop the in-place edit + apt hook entirely. If that lands, this
  ADR should be superseded.
- Until verified on a real controller with `wb-diag-collect` installed, the
  integration is HITL; the YAML-shape tests `skip` unless PyYAML is in the venv.
