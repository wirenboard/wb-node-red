# 0001 — Helper language and the testability seam

**Status:** Accepted

## Context

The PRD fixes the helper language as Python 3 + pytest (python3 is present on
WB; maintainer scripts are thin sh that call the CLI). The design (§3) splits
the helper conceptually into pure cores and thin system wrappers but doesn't
prescribe *how* the wrappers stay testable without a controller, where
docker/systemd/nginx/mosquitto don't exist.

## Decision

- Split the helper into **deep, pure cores** (`descriptor`, `nginx`, `ports`,
  `mqtt`, `seeding`) — deterministic input → output, no system access — and
  **thin wrappers** (`compose`, `systemd`, `mqtt_provision`, `cli`, `diag`).
- All system access goes through a single injected **`Runner`** seam
  (`runner.py`: `Runner` protocol, `SubprocessRunner`, `CommandResult`,
  `CommandError`). Wrappers never call `subprocess` directly.
- `models.AppDescriptor` is the shared contract the cores pass between each
  other.
- Tests: cores with deterministic in/out; wrappers and the CLI orchestrator with
  a **fake `Runner` + `tmp_path`**, asserting the commands issued and files
  written — never touching real docker/systemd/nginx.

## Consequences

- The entire `install`/`remove` flow is unit-testable offline; real invocation
  is the only HITL part.
- A fake `Runner` that records argv and returns programmed results is the
  standard test fixture; new wrappers must take a `Runner`.
- "Done" for a core means green pytest; "done" for a wrapper means green pytest
  **plus** a logged controller manual-test item (it isn't truly verified until
  run on hardware).
