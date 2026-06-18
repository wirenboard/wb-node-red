"""CLI / orchestrator.

Intentionally thin glue over an injected :class:`Runner`, so the whole flow is
unit-testable with a fake. WB ships a small set of CURATED, static services:
each one hardcodes its loopback port and ships its own static nginx drop-in, so
the helper no longer reads compose labels, allocates ports, or renders nginx at
runtime. The verbs are therefore minimal (docs/adr/0003 minimal-first):

* ``provision-mqtt`` — system-level, run once at helper install: create the
  ``wb`` network, set ip_nonlocal_bind, install the mosquitto listener, restart
  mosquitto once.
* ``install <app>`` — seed the user layer, enable+start the systemd instance,
  validate and reload nginx (the service package already dropped its static
  proxy config in place).
* ``remove <app>`` — disable+stop the systemd instance. ``/mnt/data`` is left
  intact. The static drop-in is a dpkg-owned file, so the proxy reload happens
  in the service's postrm (via ``reload-proxy``), not here.
* ``reload-proxy`` — tolerant ``nginx -t`` + reload, run from the service's
  postrm after dpkg deletes the static drop-in.

Day-2 operations (status/logs/restart/update/list) are intentionally NOT helper
verbs: use ``systemctl`` and ``docker`` directly.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from .mqtt_provision import MqttProvisioner
from .runner import Runner, SubprocessRunner
from .seeding import seed_app_dir, seed_tree
from .systemd import SystemdInstanceManager


@dataclass
class Paths:
    """Filesystem layout (docs/adr/0003 minimal-first). Overridable for tests."""

    data_dir: Path = Path("/mnt/data/wb-docker-apps")
    # Package-owned, read-only payload root (the .deb installs here). Each
    # service ships its base compose and its ``<app>/seed/`` default-config tree
    # under this prefix.
    pkg_dir: Path = Path("/usr/lib/wb-docker-app")
    # MQTT connectivity (docs/adr/0004 mqtt boot-timing): the helper's own
    # mosquitto gateway listener drop-in and the ip_nonlocal_bind sysctl drop-in.
    # Both live under /etc so they survive package upgrades and edits are
    # explicit.
    mosquitto_conf_dir: Path = Path("/etc/mosquitto/conf.d")
    sysctl_file: Path = Path("/etc/sysctl.d/60-wb-docker-app.conf")
    # Marker written ONLY after a successful mosquitto restart so a failed
    # restart leaves it stale/absent and the next provision retries.
    mqtt_marker_file: Path = Path("/var/lib/wb-docker-app/mqtt-provisioned")


# Dedicated docker network for container<->broker connectivity (docs/adr/0004).
# The subnet is fixed and chosen to avoid WB-used ranges; gateway is where
# mosquitto binds its extra listener and where containers reach the broker.
# HITL: the subnet choice and bind-on-boot behaviour need controller validation.
WB_NETWORK_SUBNET = "172.29.0.0/24"
WB_NETWORK_GATEWAY = "172.29.0.1"
WB_MQTT_LISTENER_PORT = 11883


# Default user-layer override template, seeded only-if-absent.
_OVERRIDE_TEMPLATE = (
    "# wb-docker-app: your overrides for this service.\n"
    "# Edits here survive package upgrades.\n"
    "services: {}\n"
)


@dataclass
class Helper:
    """Orchestrates the per-app lifecycle over the seeding core and wrappers."""

    runner: Runner
    paths: Paths

    def __post_init__(self):
        self.systemd = SystemdInstanceManager(self.runner)

    def provision_mqtt(self) -> None:
        """Provision container<->broker connectivity ONCE (docs/adr/0004).

        Run at *helper* install (wb-docker-app postinst), not per service: it
        creates the ``wb`` docker network if absent, sets ip_nonlocal_bind so
        mosquitto can bind the gateway IP at early boot, installs the mosquitto
        gateway listener, and restarts mosquitto a single time. Re-running is
        idempotent — the network is only created when absent and the drop-ins
        are rewritten only when their text drifts — so installing a service
        (which does NOT call this) never restarts mosquitto again.
        """
        self.paths.mosquitto_conf_dir.mkdir(parents=True, exist_ok=True)
        self.paths.sysctl_file.parent.mkdir(parents=True, exist_ok=True)
        MqttProvisioner(
            self.runner,
            subnet=WB_NETWORK_SUBNET,
            gateway=WB_NETWORK_GATEWAY,
            listener_port=WB_MQTT_LISTENER_PORT,
            mosquitto_conf_dir=self.paths.mosquitto_conf_dir,
            sysctl_file=self.paths.sysctl_file,
            marker_file=self.paths.mqtt_marker_file,
        ).provision()

    def install(self, app: str) -> None:
        """Bring a curated service up (docs/adr/0003 minimal-first).

        Seed the user layer only-if-absent, then drop the package's default
        config (e.g. Node-RED's ``data/flows.json``) from its ``<app>/seed/``
        tree into that layer — also only-if-absent, so a user's edits survive.
        Enable+start the systemd instance, and reload nginx so the static proxy
        drop-in the service package shipped goes live. No port allocation, no
        nginx generation, no descriptor read — the service is static.
        """
        seed_app_dir(
            self.paths.data_dir / app,
            files={"docker-compose.override.yml": _OVERRIDE_TEMPLATE},
            dirs=["data"],
        )
        seed_tree(self.paths.pkg_dir / app / "seed", self.paths.data_dir / app)
        self.systemd.enable_now(app)
        self._reload_nginx()

    def remove(self, app: str) -> None:
        """Tear a curated service down, leaving ``/mnt/data`` intact.

        Disable+stop the systemd instance only. The static proxy drop-in is a
        dpkg-owned file that the package deletes AFTER this prerm runs, so the
        proxy reload that drops the now-stale server-block happens in the
        service's postrm (via ``reload-proxy``), not here. The user layer under
        ``/mnt/data`` is deliberately preserved.
        """
        self.systemd.disable(app)

    def reload_proxy(self) -> None:
        """Tolerant proxy reload, run from a service's postrm.

        Run after dpkg has deleted the service's static nginx drop-in, so the
        reload drops the now-stale server-block. Tolerant on purpose: ``nginx
        -t`` runs unchecked and the reload only follows on a clean config, so an
        unrelated broken nginx config elsewhere can't block package removal.
        Never raises.
        """
        test = self.runner.run(["nginx", "-t"], check=False)
        if test.returncode == 0:
            self.runner.run(["systemctl", "reload", "nginx"])

    def _reload_nginx(self) -> None:
        """Validate the config, then reload — never reload a broken config.

        ``nginx -t`` exits non-zero on a bad config; the checked run raises
        before the reload so a malformed NEW drop-in can't take the whole proxy
        (and thus the WB web UI) down. The 401-redirect intent now lives in the
        static node-red.conf (docs/adr/0003).
        """
        self.runner.run(["nginx", "-t"])
        self.runner.run(["systemctl", "reload", "nginx"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wb-docker-app")
    sub = parser.add_subparsers(dest="command", required=True)
    for verb in ("install", "remove"):
        p = sub.add_parser(verb)
        p.add_argument("app")
    # System-level, no app argument: run once at helper install (docs/adr/0004).
    sub.add_parser("provision-mqtt")
    # No app argument: tolerant nginx reload, run from a service postrm after
    # the static drop-in has been deleted by dpkg (docs/adr/0003).
    sub.add_parser("reload-proxy")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    helper = Helper(SubprocessRunner(), Paths())
    if args.command == "provision-mqtt":
        helper.provision_mqtt()
    elif args.command == "reload-proxy":
        helper.reload_proxy()
    else:
        getattr(helper, args.command)(args.app)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
