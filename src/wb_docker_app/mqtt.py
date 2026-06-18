"""MQTT / docker-network config renderer.

Renders the parameters of the dedicated docker network ``wb``, the mosquitto
drop-in text that binds a listener on that network's gateway, and the sysctl
drop-in that lets mosquitto bind that gateway IP at early boot before docker
exists (docs/adr/0004). All functions here are pure: they return config
*text* / *params* and never touch docker or systemctl — applying them is the
provisioner's job.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass


class NetworkError(ValueError):
    """Raised when the ``wb`` network params are inconsistent."""


@dataclass(frozen=True)
class WbNetwork:
    """Parameters of the dedicated docker network ``wb`` (docs/adr/0004).

    Attributes:
        subnet: the fixed CIDR of the network (e.g. ``172.29.0.0/24``).
        gateway: the gateway address mosquitto binds its listener on, and that
            containers reach the broker at (e.g. ``172.29.0.1``).
    """

    subnet: str
    gateway: str


def network_params(subnet: str, gateway: str) -> WbNetwork:
    """Build the ``wb`` network params from a fixed ``subnet`` and ``gateway``.

    Raises:
        NetworkError: if ``gateway`` does not lie within ``subnet``.
    """
    network = ipaddress.ip_network(subnet)
    if ipaddress.ip_address(gateway) not in network:
        raise NetworkError(
            f"gateway {gateway} is not within subnet {subnet}"
        )
    return WbNetwork(subnet=subnet, gateway=gateway)


def render_mosquitto_listener(gateway: str, port: int) -> str:
    """Render a mosquitto drop-in binding ``listener <port> <gateway>``."""
    # ``allow_anonymous`` follows the ``listener`` line so it scopes to *this*
    # listener: anonymous access is acceptable here because the listener binds
    # the docker-network gateway only (docs/adr/0004); broker ACL/passwords are
    # out of scope per the PRD.
    return f"listener {port} {gateway}\nallow_anonymous true\n"


def render_nonlocal_bind_sysctl() -> str:
    """Render the sysctl drop-in enabling ``net.ipv4.ip_nonlocal_bind``.

    Mosquitto MUST keep its normal EARLY boot: the whole WB stack (drivers,
    wb-rules, homeui) depends on the broker, so gating it behind docker with an
    ``After=docker.service`` ordering would gate all controller MQTT behind
    Docker on every boot — unacceptable. Instead we let mosquitto bind the
    not-yet-existent ``wb`` gateway IP at early boot (the way keepalived binds a
    VIP that isn't up yet) by enabling ``ip_nonlocal_bind`` system-wide. The
    gateway listener (see :func:`render_mosquitto_listener`) then goes live the
    moment Docker brings up the ``wb`` network, with no ordering coupling between
    mosquitto and docker (docs/adr/0004).
    """
    return "net.ipv4.ip_nonlocal_bind = 1\n"
