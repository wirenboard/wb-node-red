"""Behavior of the MQTT / docker-network config renderer (module D).

These are pure renderers: they emit config *text* and network *parameters* for
the dedicated docker network ``wb``, the mosquitto listener bound to its
gateway, and the ``ip_nonlocal_bind`` sysctl drop-in (design.md §3.7). They do
not touch docker or systemctl — that is the provisioner's job (module H). Tests
assert on the rendered text / params only.
"""

import pytest

from wb_docker_app.mqtt import (
    NetworkError,
    network_params,
    render_mosquitto_listener,
    render_nonlocal_bind_sysctl,
)


def test_listener_dropin_binds_the_given_port_on_the_given_gateway():
    snippet = render_mosquitto_listener(gateway="172.29.0.1", port=11883)

    assert "listener 11883 172.29.0.1" in snippet


def test_listener_allows_anonymous_access_on_its_dedicated_listener():
    snippet = render_mosquitto_listener(gateway="172.29.0.1", port=11883)

    assert "allow_anonymous true" in snippet


def test_network_params_expose_the_subnet_and_gateway():
    net = network_params(subnet="172.29.0.0/24", gateway="172.29.0.1")

    assert net.subnet == "172.29.0.0/24"
    assert net.gateway == "172.29.0.1"


def test_gateway_outside_the_subnet_raises_network_error():
    with pytest.raises(NetworkError, match="172.30.0.1"):
        network_params(subnet="172.29.0.0/24", gateway="172.30.0.1")


def test_nonlocal_bind_sysctl_enables_ip_nonlocal_bind():
    # Mosquitto keeps its EARLY boot (the whole WB stack depends on the broker);
    # ip_nonlocal_bind lets it bind the not-yet-up wb gateway IP at boot, instead
    # of ordering it After=docker.service.
    snippet = render_nonlocal_bind_sysctl()

    assert snippet == "net.ipv4.ip_nonlocal_bind = 1\n"


def test_rendered_outputs_are_stable_across_calls():
    assert render_mosquitto_listener(
        gateway="172.29.0.1", port=11883
    ) == render_mosquitto_listener(gateway="172.29.0.1", port=11883)
    assert render_nonlocal_bind_sysctl() == render_nonlocal_bind_sysctl()
