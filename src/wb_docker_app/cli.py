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
from .mqtt_provision import MqttProvisioner
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
    # MQTT connectivity (design.md §3.7): the helper's own mosquitto gateway
    # listener drop-in and the ``After=docker.service`` systemd drop-in. Both
    # live under /etc so they survive package upgrades and edits are explicit.
    mosquitto_conf_dir: Path = Path("/etc/mosquitto/conf.d")
    mosquitto_dropin_dir: Path = Path(
        "/etc/systemd/system/mosquitto.service.d"
    )


# Dedicated docker network for container<->broker connectivity (design.md §3.7).
# The subnet is fixed and chosen to avoid WB-used ranges; gateway is where
# mosquitto binds its extra listener and where containers reach the broker.
# HITL: the subnet choice and bind-on-boot behaviour need controller validation.
WB_NETWORK_SUBNET = "172.29.0.0/24"
WB_NETWORK_GATEWAY = "172.29.0.1"
WB_MQTT_LISTENER_PORT = 11883


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

    def provision_mqtt(self) -> None:
        """Provision container<->broker connectivity ONCE (design.md §3.7).

        Run at *helper* install (wb-docker-app postinst), not per service: it
        creates the ``wb`` docker network if absent, installs the mosquitto
        gateway listener and the ``After=docker.service`` drop-in, and restarts
        mosquitto a single time. Re-running is idempotent — the network is only
        created when absent and the drop-ins are rewritten with identical text —
        so installing a second service (which does NOT call this) never restarts
        mosquitto again.
        """
        self.paths.mosquitto_conf_dir.mkdir(parents=True, exist_ok=True)
        self.paths.mosquitto_dropin_dir.mkdir(parents=True, exist_ok=True)
        MqttProvisioner(
            self.runner,
            subnet=WB_NETWORK_SUBNET,
            gateway=WB_NETWORK_GATEWAY,
            listener_port=WB_MQTT_LISTENER_PORT,
            mosquitto_conf_dir=self.paths.mosquitto_conf_dir,
            mosquitto_dropin_dir=self.paths.mosquitto_dropin_dir,
        ).provision()

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
        self.runner.run(["systemctl", "reload", "nginx"])

        self.systemd.enable_now(app)

    def remove(self, app: str) -> None:
        self.systemd.disable(app)
        self._compose(app).down()
        block = self.paths.nginx_includes / f"{app}.conf"
        if block.exists():
            block.unlink()
            self.runner.run(["systemctl", "reload", "nginx"])
        self.allocator.release(app)

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
    # System-level, no app argument: run once at helper install (design.md §3.7).
    sub.add_parser("provision-mqtt")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    helper = Helper(SubprocessRunner(), Paths())
    if args.command == "list":
        for app in helper.list_apps():
            print(app)
    elif args.command == "provision-mqtt":
        helper.provision_mqtt()
    else:
        getattr(helper, args.command)(args.app)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
