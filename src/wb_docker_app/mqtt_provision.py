"""One-time MQTT provisioner.

Run ONCE per system, lazily — by the postinst of the first bridge-service that
needs the broker, NOT at helper install (docs/adr/0004) — composing the pure
renderers (:mod:`wb_docker_app.mqtt`) with a :class:`Runner`: it creates the
dedicated docker network ``wb`` only if absent, writes the mosquitto gateway
listener drop-in, writes the ``ip_nonlocal_bind`` sysctl drop-in and applies
it, and issues a single ``systemctl restart mosquitto``.

The boot-timing crux: mosquitto MUST keep its normal EARLY boot, because the
whole WB stack (drivers, wb-rules, homeui) depends on the broker. Ordering it
``After=docker.service`` would gate all controller MQTT behind Docker on every
boot. Instead we enable ``net.ipv4.ip_nonlocal_bind`` so mosquitto can bind the
not-yet-existent ``wb`` gateway IP at early boot (like a keepalived VIP); the
gateway listener goes live the moment Docker brings up the ``wb`` network, with
no ordering coupling between mosquitto and docker.

The mosquitto conf dir, the sysctl file path and the marker file path are
injected so tests redirect ``/etc/mosquitto/conf.d``, ``/etc/sysctl.d/...`` and
``/var/lib/wb-docker-app/...`` to ``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path

from .mqtt import (
    network_params,
    render_mosquitto_listener,
    render_nonlocal_bind_sysctl,
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
        sysctl_file: Path = Path("/etc/sysctl.d/60-wb-docker-app.conf"),
        marker_file: Path = Path("/var/lib/wb-docker-app/mqtt-provisioned"),
    ):
        self._runner = runner
        self._network = network_params(subnet, gateway)
        self._listener_port = listener_port
        self._mosquitto_conf_dir = mosquitto_conf_dir
        self._sysctl_file = sysctl_file
        self._marker_file = marker_file

    def provision(self) -> None:
        listener = render_mosquitto_listener(
            gateway=self._network.gateway, port=self._listener_port
        )
        sysctl = render_nonlocal_bind_sysctl()
        listener_path = self._mosquitto_conf_dir / "wb.conf"
        # Deterministic concat of everything we'd write: the marker holds this
        # signature ONLY after a successful restart, so a partial run (restart
        # failed after the drop-ins were written) leaves the marker stale/absent
        # and the next run falls through and retries the restart.
        signature = listener + sysctl

        # Idempotency: skip the network create AND the mosquitto restart only
        # when the system is fully provisioned — the network is present, both
        # drop-ins hold the exact text we'd write, AND the marker confirms a
        # past restart succeeded for this exact signature. Otherwise fall
        # through and (re)apply, ending with the restart + marker write.
        inspect = self._runner.run(
            ["docker", "network", "inspect", "wb"], check=False
        )
        network_present = inspect.returncode == 0
        if (
            network_present
            and _has_text(listener_path, listener)
            and _has_text(self._sysctl_file, sysctl)
            and _has_text(self._marker_file, signature)
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

        # Enable ip_nonlocal_bind so mosquitto can bind the wb gateway IP at
        # early boot before docker brings the network up. Write the drop-in and
        # apply it now so the running system honours it immediately.
        self._sysctl_file.write_text(sysctl)
        self._runner.run(["sysctl", "-p", str(self._sysctl_file)])

        listener_path.write_text(listener)

        self._runner.run(["systemctl", "restart", "mosquitto"])

        # Marker LAST: only a successful restart (the line above did not raise)
        # records the signature, so a failed restart never marks the system as
        # provisioned and the next run retries.
        self._marker_file.parent.mkdir(parents=True, exist_ok=True)
        self._marker_file.write_text(signature)


def _has_text(path: Path, text: str) -> bool:
    """True if ``path`` exists and already holds exactly ``text``."""
    return path.exists() and path.read_text() == text
