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
        inspect = self._runner.run(
            ["docker", "network", "inspect", "wb"], check=False
        )
        if inspect.returncode != 0:
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

        listener = render_mosquitto_listener(
            gateway=self._network.gateway, port=self._listener_port
        )
        (self._mosquitto_conf_dir / "wb.conf").write_text(listener)

        (self._mosquitto_dropin_dir / "after-docker.conf").write_text(
            render_mosquitto_after_docker_dropin()
        )

        self._runner.run(["systemctl", "restart", "mosquitto"])
