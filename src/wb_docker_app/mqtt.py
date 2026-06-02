"""MQTT / docker-network config renderer (module D).

Renders the parameters of the dedicated docker network ``wb`` and the mosquitto
drop-in text that binds a listener on that network's gateway (design.md §3.7).
All functions here are pure: they return config *text* / *params* and never
touch docker or systemctl — applying them is the provisioner's job (module H).
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass


class NetworkError(ValueError):
    """Raised when the ``wb`` network params are inconsistent."""


@dataclass(frozen=True)
class WbNetwork:
    """Parameters of the dedicated docker network ``wb`` (design.md §3.7).

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
    # the docker-network gateway only (design.md §3.7); broker ACL/passwords are
    # out of scope per the PRD.
    return f"listener {port} {gateway}\nallow_anonymous true\n"


def render_mosquitto_after_docker_dropin() -> str:
    """Render the systemd drop-in ordering mosquitto after ``docker.service``.

    The gateway listener (see :func:`render_mosquitto_listener`) can only bind
    once docker has brought up the ``wb`` network, so mosquitto must start after
    docker (design.md §3.7 boot-order). ``Wants`` (not ``Requires``) keeps the
    broker startable even if docker is absent — it just won't have the gateway
    listener until docker comes up.
    """
    return (
        "[Unit]\n"
        "After=docker.service\n"
        "Wants=docker.service\n"
    )
