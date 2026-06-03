# wb-docker-apps

One-command (`apt install wb-<service>`) installation of containerized services
(Node-RED first) on Wiren Board controllers, layered on the `docker-ce` repack.
A shared helper package (`wb-docker-app`) owns all lifecycle logic; each service
is a thin `.deb`. See [CONTEXT.md](CONTEXT.md) for the domain language and
[doc/wb-docker-apps-design.md](doc/wb-docker-apps-design.md) for the full design.

## Dev setup

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e . pytest        # or: pip install pytest
python -m pytest -q            # pythonpath=src and testpaths=tests are in pyproject
```

The Debian packaging lives under `packaging/<pkg>/`; building real `.deb`s needs
a Debian host (`dpkg-buildpackage`, fakeroot, debhelper-13, dh-python). The
rootless packaging smoke tests use `dpkg-deb --build` and `shellcheck` and run
under plain `pytest`.

## Layout

- `src/wb_docker_app/` — the helper. Pure cores (`descriptor`, `nginx`, `ports`,
  `mqtt`, `seeding`) + thin wrappers (`compose`, `systemd`, `mqtt_provision`,
  `cli`, `diag`) over the injected `runner.Runner` seam. `models.AppDescriptor`
  is the shared contract.
- `tests/` — pytest. Cores tested with deterministic in/out; wrappers/CLI tested
  with a fake `Runner` + `tmp_path` (no real docker/systemd/nginx touched).
- `packaging/` — Debian packaging for `wb-docker-app`, `wb-node-red`, `wb-echo`.
- `doc/` — design, PRD, handoff, and the tracer-bullet issues (`doc/issues/`).
- `docs/agents/`, `docs/adr/`, `CONTEXT.md` — agent-facing config (below).

## Conventions

- **TDD, vertical slices.** One test → one implementation → repeat. Tests assert
  observable behavior through public interfaces, never implementation detail.
- **System access only through `Runner`.** Wrappers never call `subprocess`
  directly; they take a `Runner` so orchestration is unit-testable with a fake.
  Real `docker`/`systemctl`/`nginx`/`mosquitto` invocation is HITL.
- **Domain language.** Use the terms in [CONTEXT.md](CONTEXT.md) in code, tests,
  issues, and commits.
- **Commits.** Conventional-style (`feat(nginx): …`, `fix(diag): …`), one
  coherent change each. Branch off `master`; don't commit to `master` directly
  for feature work.

## Agent skills

Configuration the engineering skills (`to-issues`, `triage`, `to-prd`, `tdd`,
`diagnose`, `improve-codebase-architecture`, `afk-pipeline`) rely on:

- **Issue tracker** — see [docs/agents/issue-tracker.md](docs/agents/issue-tracker.md).
  Issues are local markdown under `doc/issues/` (no remote tracker configured
  yet; the eventual target is GitHub `wirenboard/wb-docker`).
- **Triage labels** — see [docs/agents/triage-labels.md](docs/agents/triage-labels.md).
  The five canonical roles, recorded as markers inside the markdown issue files.
- **Domain docs** — see [docs/agents/domain.md](docs/agents/domain.md).
  Single-context: [CONTEXT.md](CONTEXT.md) at the root, ADRs in
  [docs/adr/](docs/adr/), foundational decisions in
  [doc/wb-docker-apps-design.md](doc/wb-docker-apps-design.md).
