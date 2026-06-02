"""One-time MQTT provisioner (module H).

Run ONCE per system at helper install (design.md §3.7), composing module D's
pure renderers (:mod:`wb_docker_app.mqtt`) with a :class:`Runner`: it creates
the dedicated docker network ``wb`` only if absent, writes the mosquitto
gateway listener drop-in, writes the ``After=docker.service`` systemd drop-in
for mosquitto, and issues a single ``systemctl restart mosquitto``. The two
drop-in dirs are injected so tests redirect ``/etc/mosquitto/conf.d`` and the
systemd drop-in dir to ``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path

from .mqtt import (
    network_params,
    render_mosquitto_after_docker_dropin,
    render_mosquitto_listener,
)
from .runner import Runner


class MqttProvisioner:
    """Provisions the ``wb`` network and mosquitto gateway listener, once."""

    def __init__(
        self,
        runner: Runner,
        *,
        subnet: str,
        gateway: str,
        listener_port: int,
        mosquitto_conf_dir: Path,
        mosquitto_dropin_dir: Path,
    ):
        self._runner = runner
        self._network = network_params(subnet, gateway)
        self._listener_port = listener_port
        self._mosquitto_conf_dir = mosquitto_conf_dir
        self._mosquitto_dropin_dir = mosquitto_dropin_dir

    def provision(self) -> None:
        listener = render_mosquitto_listener(
            gateway=self._network.gateway, port=self._listener_port
        )
        after_docker = render_mosquitto_after_docker_dropin()
        listener_path = self._mosquitto_conf_dir / "wb.conf"
        after_docker_path = self._mosquitto_dropin_dir / "after-docker.conf"

        # Idempotency: if the network is already present and both drop-ins are
        # in place with the exact text we'd write, nothing changed — skip the
        # network create AND the mosquitto restart. This makes a re-run (helper
        # upgrade, or any repeat invocation) a no-op rather than a needless
        # broker restart; mosquitto is restarted exactly once, at first install.
        inspect = self._runner.run(
            ["docker", "network", "inspect", "wb"], check=False
        )
        network_present = inspect.returncode == 0
        if (
            network_present
            and _has_text(listener_path, listener)
            and _has_text(after_docker_path, after_docker)
        ):
            return

        if not network_present:
            self._runner.run(
                [
                    "docker",
                    "network",
                    "create",
                    "--subnet",
                    self._network.subnet,
                    "--gateway",
                    self._network.gateway,
                    "wb",
                ]
            )

        listener_path.write_text(listener)
        after_docker_path.write_text(after_docker)

        self._runner.run(["systemctl", "restart", "mosquitto"])


def _has_text(path: Path, text: str) -> bool:
    """True if ``path`` exists and already holds exactly ``text``."""
    return path.exists() and path.read_text() == text
