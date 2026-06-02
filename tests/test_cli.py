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
        if "config" in argv:
            stdout = json.dumps(COMPOSE_CONFIG)
        return CommandResult(argv=tuple(argv), returncode=0, stdout=stdout)

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


def test_install_tests_the_nginx_config_before_reloading_it(paths):
    runner = FakeRunner()
    helper = Helper(runner, paths)

    helper.install("node-red")

    # A bad server-block must never reach a live reload: `nginx -t` gates the
    # reload, and a failing test aborts before `systemctl reload nginx`.
    test_idx = runner.calls.index(["nginx", "-t"])
    reload_idx = runner.calls.index(["systemctl", "reload", "nginx"])
    assert test_idx < reload_idx


def test_install_aborts_the_reload_when_the_nginx_config_test_fails(paths):
    class BadConfigRunner(FakeRunner):
        def run(self, argv, *, check=True, input=None):
            result = super().run(argv, check=check, input=input)
            if list(argv) == ["nginx", "-t"]:
                from wb_docker_app.runner import CommandError, CommandResult

                raise CommandError(
                    CommandResult(argv=tuple(argv), returncode=1,
                                  stderr="nginx: configuration file test failed")
                )
            return result

    runner = BadConfigRunner()
    helper = Helper(runner, paths)

    with pytest.raises(Exception):
        helper.install("node-red")

    # the reload must not have been issued after a failing config test.
    assert ["systemctl", "reload", "nginx"] not in runner.calls


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
