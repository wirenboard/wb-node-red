"""Behavior of the CLI orchestrator (module I).

The orchestrator is deliberately thin glue: it composes the pure cores (A, B, E)
and the thin wrappers (F, G) and writes the nginx server-block. Tests drive it
with a fake :class:`Runner` (recording argv, returning canned results) and a
``Paths`` rooted at ``tmp_path`` — no docker/systemd/nginx is touched.
"""

import json

import pytest

from wb_docker_app.cli import Helper, Paths, build_parser
from wb_docker_app.runner import CommandResult


# Canonical `docker compose config --format json` for one WB service. The
# published loopback port here is the runtime source of truth for nginx.
COMPOSE_CONFIG = {
    "services": {
        "node-red": {
            "image": "registry.wirenboard.com/wb/node-red:4.0.2-wb1",
            "ports": [
                {"host_ip": "127.0.0.1", "target": 1880,
                 "published": "21880", "protocol": "tcp"}
            ],
            "labels": {
                "wb.app": "node-red",
                "wb.title": "Node-RED",
                "wb.proxy.port": "1880",
                "wb.proxy.role": "admin",
            },
        }
    }
}


class FakeRunner:
    """Records every argv; returns canned compose-config JSON, else rc 0."""

    def __init__(self):
        self.calls = []

    def run(self, argv, *, check=True, input=None):
        argv = list(argv)
        self.calls.append(argv)
        stdout = ""
        returncode = 0
        if "config" in argv:
            stdout = json.dumps(COMPOSE_CONFIG)
        # On a clean system the `wb` network is absent, so inspect fails — this
        # lets provision_mqtt exercise the create path.
        if tuple(argv[:3]) == ("docker", "network", "inspect"):
            returncode = 1
        return CommandResult(argv=tuple(argv), returncode=returncode, stdout=stdout)

    def issued(self, *needles):
        """True if some recorded call contains all the given substrings."""
        return any(
            all(n in " ".join(c) for n in needles) for c in self.calls
        )


@pytest.fixture
def paths(tmp_path):
    return Paths(
        base_dir=tmp_path / "usr/lib/wb-docker-app",
        data_dir=tmp_path / "mnt/data/wb-docker-apps",
        nginx_includes=tmp_path / "etc/nginx/includes/default.wb.d",
        port_registry=tmp_path / "var/lib/wb-docker-app/ports.json",
        mosquitto_conf_dir=tmp_path / "etc/mosquitto/conf.d",
        mosquitto_dropin_dir=tmp_path / "etc/systemd/system/mosquitto.service.d",
    )


def test_install_writes_a_gated_nginx_block_and_brings_the_service_up(paths):
    runner = FakeRunner()
    helper = Helper(runner, paths)

    helper.install("node-red")

    # nginx server-block written, gated to the descriptor's role, proxying to
    # the runtime-published loopback port.
    block = (paths.nginx_includes / "node-red.conf").read_text()
    assert "auth_request" in block
    assert 'required_user_type "admin"' in block
    assert "127.0.0.1:21880" in block

    # service brought up and enabled under the templated systemd unit.
    assert runner.issued("docker", "compose", "up", "-d")
    assert runner.issued("systemctl", "enable", "--now",
                         "wb-docker-app@node-red.service")


def test_remove_disables_the_unit_and_deletes_the_nginx_block(paths):
    runner = FakeRunner()
    helper = Helper(runner, paths)
    helper.install("node-red")
    assert (paths.nginx_includes / "node-red.conf").exists()

    helper.remove("node-red")

    assert not (paths.nginx_includes / "node-red.conf").exists()
    assert runner.issued("systemctl", "disable", "--now",
                         "wb-docker-app@node-red.service")
    assert runner.issued("docker", "compose", "down")


def test_reinstall_keeps_the_same_port_and_preserves_user_overrides(paths):
    runner = FakeRunner()
    helper = Helper(runner, paths)
    helper.install("node-red")

    env = paths.data_dir / "node-red" / ".env"
    first_port = env.read_text()
    override = paths.data_dir / "node-red" / "docker-compose.override.yml"
    override.write_text("services:\n  node-red:\n    mem_limit: 256m\n")

    helper.install("node-red")

    assert env.read_text() == first_port  # allocator idempotent, .env not reseeded
    assert "mem_limit: 256m" in override.read_text()  # user edit survives


def test_list_returns_app_slugs_from_running_container_labels():
    from wb_docker_app.runner import CommandResult

    class LabelRunner(FakeRunner):
        def run(self, argv, *, check=True, input=None):
            self.calls.append(list(argv))
            return CommandResult(argv=tuple(argv), returncode=0,
                                 stdout="node-red\nhome-assistant\n")

    runner = LabelRunner()
    helper = Helper.__new__(Helper)  # list_apps needs no allocator/paths
    helper.runner = runner
    assert helper.list_apps() == ["node-red", "home-assistant"]


def test_parser_dispatches_install_with_an_app_argument():
    args = build_parser().parse_args(["install", "node-red"])
    assert args.command == "install"
    assert args.app == "node-red"


def test_parser_dispatches_provision_mqtt_without_an_app_argument():
    args = build_parser().parse_args(["provision-mqtt"])
    assert args.command == "provision-mqtt"


def test_provision_mqtt_creates_the_network_and_restarts_mosquitto_once(paths):
    # MQTT connectivity is provisioned by the helper at install time: it creates
    # the `wb` docker network and restarts mosquitto exactly once (design.md §3.7).
    runner = FakeRunner()
    helper = Helper(runner, paths)

    helper.provision_mqtt()

    assert runner.issued("docker", "network", "create", "wb")
    restarts = [
        c for c in runner.calls if c == ["systemctl", "restart", "mosquitto"]
    ]
    assert restarts == [["systemctl", "restart", "mosquitto"]]
    # drop-ins written under the configured /etc dirs
    assert (paths.mosquitto_conf_dir / "wb.conf").exists()
    assert (paths.mosquitto_dropin_dir / "after-docker.conf").exists()


def test_a_service_install_never_touches_mosquitto(paths):
    # Provisioning is the helper's job, run once at helper install — NOT per
    # service. Installing a (second) service must never restart mosquitto.
    runner = FakeRunner()
    helper = Helper(runner, paths)

    helper.install("node-red")

    assert not runner.issued("systemctl", "restart", "mosquitto")
    assert not runner.issued("docker", "network", "create")
