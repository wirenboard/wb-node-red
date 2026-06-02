"""CLI / orchestrator (module I).

Intentionally thin glue: composes the pure cores (A descriptor reader, B nginx
render, E seeding) and the thin wrappers (F compose-runner, G systemd manager)
into the user-facing verbs of ``wb-docker-app`` — ``install/remove/status/logs/
restart/update/list`` (design.md §3.10, §4). The system is reached only through
an injected :class:`Runner`, so the whole flow is unit-testable with a fake.

NOTE: the exact mechanism for injecting the allocated loopback port into compose
(here: an ``.env`` ``WB_INTERNAL_PORT`` consumed by the base compose) is an open
item to confirm on a controller — design.md §5.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

from .compose import ComposeRunner
from .descriptor import read_descriptor
from .nginx import render_server_block
from .ports import PortAllocator
from .runner import Runner, SubprocessRunner
from .seeding import seed_app_dir
from .systemd import SystemdInstanceManager


@dataclass
class Paths:
    """Filesystem layout (design.md §3.6). Overridable for tests."""

    base_dir: Path = Path("/usr/lib/wb-docker-app")
    data_dir: Path = Path("/mnt/data/wb-docker-apps")
    nginx_includes: Path = Path("/etc/nginx/includes/default.wb.d")
    port_registry: Path = Path("/var/lib/wb-docker-app/ports.json")


# Default user-layer templates, seeded only-if-absent (module E).
_OVERRIDE_TEMPLATE = (
    "# wb-docker-app: your overrides for this service.\n"
    "# Edits here survive package upgrades.\n"
    "services: {}\n"
)


@dataclass
class Helper:
    """Orchestrates the per-app lifecycle over the cores and wrappers."""

    runner: Runner
    paths: Paths
    allocator: PortAllocator | None = field(default=None)

    def __post_init__(self):
        if self.allocator is None:
            self.paths.port_registry.parent.mkdir(parents=True, exist_ok=True)
            self.allocator = PortAllocator(self.paths.port_registry)
        self.systemd = SystemdInstanceManager(self.runner)

    def _compose(self, app: str) -> ComposeRunner:
        return ComposeRunner(
            base_path=self.paths.base_dir / app / "docker-compose.yml",
            override_path=self.paths.data_dir / app / "docker-compose.override.yml",
            project_name=f"wb-{app}",
            runner=self.runner,
        )

    def install(self, app: str) -> None:
        internal_port = self.allocator.allocate(app)
        seed_app_dir(
            self.paths.data_dir / app,
            files={
                ".env": f"WB_INTERNAL_PORT={internal_port}\n",
                "docker-compose.override.yml": _OVERRIDE_TEMPLATE,
            },
            dirs=["data"],
        )

        compose = self._compose(app)
        compose.pull()
        compose.up()

        descriptor = read_descriptor(json.loads(compose.config()))
        block = render_server_block(descriptor)
        self.paths.nginx_includes.mkdir(parents=True, exist_ok=True)
        (self.paths.nginx_includes / f"{app}.conf").write_text(block)
        self._reload_nginx()

        self.systemd.enable_now(app)

    def remove(self, app: str) -> None:
        self.systemd.disable(app)
        self._compose(app).down()
        block = self.paths.nginx_includes / f"{app}.conf"
        if block.exists():
            block.unlink()
            self._reload_nginx()
        self.allocator.release(app)

    def _reload_nginx(self) -> None:
        """Validate the config, then reload — never reload a broken config.

        ``nginx -t`` exits non-zero on a bad config; the checked run raises
        before the reload so a malformed server-block can't take the whole
        proxy (and thus the WB web UI) down (design.md §3.8, §3.9).
        """
        self.runner.run(["nginx", "-t"])
        self.runner.run(["systemctl", "reload", "nginx"])

    def update(self, app: str) -> None:
        compose = self._compose(app)
        compose.pull()
        compose.up()

    def restart(self, app: str) -> None:
        self.systemd.restart(app)

    def status(self, app: str):
        return self.systemd.status(app)

    def logs(self, app: str):
        return self.runner.run(
            ["journalctl", "-u", f"wb-docker-app@{app}.service", "-n", "200"],
            check=False,
        )

    def list_apps(self) -> list[str]:
        result = self.runner.run(
            ["docker", "ps", "--filter", "label=wb.app",
             "--format", "{{.Label \"wb.app\"}}"],
            check=False,
        )
        return [line for line in result.stdout.splitlines() if line.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wb-docker-app")
    sub = parser.add_subparsers(dest="command", required=True)
    for verb in ("install", "remove", "status", "logs", "restart", "update"):
        p = sub.add_parser(verb)
        p.add_argument("app")
    sub.add_parser("list")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    helper = Helper(SubprocessRunner(), Paths())
    if args.command == "list":
        for app in helper.list_apps():
            print(app)
    else:
        getattr(helper, args.command)(args.app)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
