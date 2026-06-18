"""CLI / orchestrator.

Intentionally thin glue over an injected :class:`Runner`, so the whole flow is
unit-testable with a fake. WB ships a small set of CURATED, static services:
each one hardcodes its loopback port and ships its own static nginx drop-in, so
the helper no longer reads compose labels, allocates ports, or renders nginx at
runtime. The verbs are therefore minimal (docs/adr/0003 minimal-first):

* ``provision-mqtt`` — system-level, run lazily from the postinst of the first
  bridge-service that needs the broker (NOT at helper install): create the
  ``wb`` network, set ip_nonlocal_bind, install the mosquitto listener, restart
  mosquitto once. Idempotent, so several bridge-services can each call it.
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
from .seeding import refresh_tree, seed_app_dir, seed_tree
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
    # nginx site dirs. A service ships its server block in sites-available; the
    # helper enables it with an ABSOLUTE-target symlink in sites-enabled. On WB
    # sites-enabled is bind-mounted from /mnt/data, so a relative target would
    # resolve outside /etc and break nginx -t (controller-validated).
    nginx_sites_available: Path = Path("/etc/nginx/sites-available")
    nginx_sites_enabled: Path = Path("/etc/nginx/sites-enabled")


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
        """Provision container<->broker connectivity, lazily (docs/adr/0004).

        Run from the postinst of the first BRIDGE service that needs the broker
        (e.g. wb-node-red), NOT at helper install: it creates the ``wb`` docker
        network if absent, sets ip_nonlocal_bind so mosquitto can bind the
        gateway IP at early boot, installs the mosquitto gateway listener, and
        restarts mosquitto a single time. Idempotent and a singleton — the
        network is only created when absent and the drop-ins are rewritten only
        when their text drifts — so several bridge services can each call it
        without re-restarting the broker, and host-networking services that
        never call it leave mosquitto untouched.
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

    def install(self, app: str, data_uid: int | None = None) -> None:
        """Bring a curated service up (docs/adr/0003 minimal-first).

        Seed the user layer only-if-absent, then drop the package's default
        config (e.g. Node-RED's ``data/flows.json``) from its ``<app>/seed/``
        tree into that layer — also only-if-absent, so a user's edits survive.
        Next refresh any package-OWNED code the service vendors (e.g. Node-RED's
        palette under ``<app>/palette/``) into the user layer, OVERWRITING our
        own subtree so an upgrade ships fresh code while leaving user-installed
        files (e.g. a hand-added palette) alone (docs/adr/0006). If the service's
        container runs as a non-root user, ``data_uid`` chowns the seeded data
        dir to it (Node-RED runs as uid 1000 and cannot write a root-owned
        ``/data`` — controller-validated). Then enable+start the systemd
        instance, enable the nginx site (absolute-target symlink), and reload
        nginx. No port allocation, no nginx generation — the service is static.
        """
        seed_app_dir(
            self.paths.data_dir / app,
            files={"docker-compose.override.yml": _OVERRIDE_TEMPLATE},
            dirs=["data"],
        )
        seed_tree(self.paths.pkg_dir / app / "seed", self.paths.data_dir / app)
        # Vendored palette (package code) -> the user's /data/node_modules. The
        # palette dir mirrors that layout (``palette/node_modules/...``), so it
        # lands at ``<app>/data/node_modules/...``. A service with no palette
        # ships no such dir and this is a no-op.
        refresh_tree(
            self.paths.pkg_dir / app / "palette",
            self.paths.data_dir / app / "data",
        )
        if data_uid is not None:
            data = self.paths.data_dir / app / "data"
            self.runner.run(
                ["chown", "-R", f"{data_uid}:{data_uid}", str(data)]
            )
        # Enable for boot, then RESTART so the unit re-runs `docker compose up
        # -d` against the current base compose. On a first install this just
        # starts the container; on an UPGRADE it recreates it so a new image tag
        # actually takes effect. `enable --now` alone would leave the already-
        # running container on the OLD image until a reboot (release-flow gap).
        self.systemd.enable(app)
        self.systemd.restart(app)
        self._enable_nginx_site(app)
        self._reload_nginx()

    def remove(self, app: str) -> None:
        """Tear a curated service down, leaving ``/mnt/data`` intact.

        Disable+stop the systemd instance and remove the nginx site symlink the
        helper created. The static server block itself is a dpkg-owned file the
        package deletes AFTER this prerm runs, so the proxy reload that drops the
        now-stale block happens in the service's postrm (via ``reload-proxy``),
        not here. The user layer under ``/mnt/data`` is deliberately preserved.
        """
        self.systemd.disable(app)
        self._disable_nginx_site(app)

    def _enable_nginx_site(self, app: str) -> None:
        """Symlink the service's shipped server block into sites-enabled.

        The service drops its block in ``sites-available/<app>.conf``; we enable
        it with an **absolute-target** symlink. A relative target
        (``../sites-available/…``) breaks on WB, where ``sites-enabled`` is
        bind-mounted from ``/mnt/data`` and the relative path resolves outside
        ``/etc`` — ``nginx -t`` then fails and install aborts (controller bug).
        A service that ships no block (e.g. a host-mode service) is a no-op.
        """
        conf = self.paths.nginx_sites_available / f"{app}.conf"
        if not conf.exists():
            return
        link = self.paths.nginx_sites_enabled / f"{app}.conf"
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(conf)  # conf is absolute -> absolute-target symlink

    def _disable_nginx_site(self, app: str) -> None:
        """Remove the sites-enabled symlink the helper created (idempotent)."""
        (self.paths.nginx_sites_enabled / f"{app}.conf").unlink(missing_ok=True)

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
    p_install = sub.add_parser("install")
    p_install.add_argument("app")
    # Chown the seeded data dir to this uid for a non-root container user
    # (Node-RED -> 1000). Omitted by services whose container runs as root.
    p_install.add_argument("--data-uid", type=int, default=None)
    sub.add_parser("remove").add_argument("app")
    # System-level, no app argument: run lazily from the first broker-needing
    # service's postinst, not at helper install (docs/adr/0004).
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
    elif args.command == "install":
        helper.install(args.app, data_uid=args.data_uid)
    else:
        helper.remove(args.app)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
