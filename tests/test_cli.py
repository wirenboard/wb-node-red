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


# --- day-2 lifecycle verbs (issue #5) ---------------------------------------
# status/logs/restart/update already exist in cli.py; these pin their behaviour.


def test_status_queries_the_systemd_instance(paths):
    runner = FakeRunner()
    helper = Helper(runner, paths)

    helper.status("node-red")

    assert runner.issued("systemctl", "status",
                         "wb-docker-app@node-red.service")


def test_restart_restarts_the_systemd_instance(paths):
    runner = FakeRunner()
    helper = Helper(runner, paths)

    helper.restart("node-red")

    assert runner.issued("systemctl", "restart",
                         "wb-docker-app@node-red.service")


def test_logs_tails_the_instance_journal(paths):
    runner = FakeRunner()
    helper = Helper(runner, paths)

    helper.logs("node-red")

    # journalctl scoped to the instance unit, bounded tail.
    assert runner.issued("journalctl", "-u",
                         "wb-docker-app@node-red.service", "-n", "200")


def test_update_pulls_the_new_image_and_recreates_the_container(paths):
    runner = FakeRunner()
    helper = Helper(runner, paths)

    helper.update("node-red")

    # explicit upgrade: pull the bumped tag then up -d to recreate.
    assert runner.issued("docker", "compose", "pull")
    assert runner.issued("docker", "compose", "up", "-d")


def test_parser_dispatches_each_lifecycle_verb_with_an_app_argument():
    for verb in ("status", "logs", "restart", "update"):
        args = build_parser().parse_args([verb, "node-red"])
        assert args.command == verb
        assert args.app == "node-red"


def test_parser_dispatches_list_without_an_app_argument():
    args = build_parser().parse_args(["list"])
    assert args.command == "list"


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


# --- wb-diag-collect integration (issue #5) ---------------------------------
#
# The released wb-diag-collect reads ONLY its single main config (no conf.d
# merge), so the shipped drop-in is inert on a current controller. The helper
# closes the gap by registering its collector `command` into that main config
# at install and removing it at uninstall. These tests pin that merge — pure,
# idempotent, and surgical (it touches only our entry).

from wb_docker_app import diag  # noqa: E402


def _yaml_or_skip():
    return pytest.importorskip("yaml")


_MAIN_CONF = """\
timeout: 10
journald_logs:
  names:
    - wb-*.service
commands:
  - filename: ps_aux
    command: ps aux
files:
  - /etc/group
"""


def test_register_adds_the_collector_command_to_main_config(tmp_path):
    yaml = _yaml_or_skip()
    conf = tmp_path / "wb-diag-collect.conf"
    conf.write_text(_MAIN_CONF)

    assert diag.register(conf) is True

    data = yaml.safe_load(conf.read_text())
    ours = [c for c in data["commands"] if c["filename"] == diag.COLLECTOR_FILENAME]
    assert len(ours) == 1
    assert ours[0]["command"] == diag.COLLECTOR_CMD
    # Pre-existing entries and other keys are preserved untouched.
    assert {"filename": "ps_aux", "command": "ps aux"} in data["commands"]
    assert data["timeout"] == 10
    assert data["files"] == ["/etc/group"]


def test_register_is_idempotent(tmp_path):
    _yaml_or_skip()
    conf = tmp_path / "wb-diag-collect.conf"
    conf.write_text(_MAIN_CONF)

    assert diag.register(conf) is True
    after_first = conf.read_text()
    # Second run is a no-op: returns False and does not duplicate our entry.
    assert diag.register(conf) is False
    assert conf.read_text() == after_first


def test_register_is_a_noop_when_diag_collect_is_not_installed(tmp_path):
    # No config file => wb-diag-collect absent => nothing to integrate with.
    missing = tmp_path / "absent.conf"
    assert diag.register(missing) is False
    assert not missing.exists()


def test_deregister_removes_only_our_entry(tmp_path):
    yaml = _yaml_or_skip()
    conf = tmp_path / "wb-diag-collect.conf"
    conf.write_text(_MAIN_CONF)
    diag.register(conf)

    assert diag.deregister(conf) is True

    data = yaml.safe_load(conf.read_text())
    assert all(c["filename"] != diag.COLLECTOR_FILENAME for c in data["commands"])
    # The pre-existing command survives.
    assert {"filename": "ps_aux", "command": "ps aux"} in data["commands"]
    # Deregistering again is a no-op.
    assert diag.deregister(conf) is False


def test_register_then_deregister_round_trips(tmp_path):
    yaml = _yaml_or_skip()
    conf = tmp_path / "wb-diag-collect.conf"
    conf.write_text(_MAIN_CONF)
    original = yaml.safe_load(conf.read_text())

    diag.register(conf)
    diag.deregister(conf)

    assert yaml.safe_load(conf.read_text()) == original


def test_cli_register_diag_invokes_the_merge(paths, monkeypatch):
    # The `register-diag` verb (run from postinst) delegates to diag.register.
    called = {}
    monkeypatch.setattr(diag, "register", lambda: called.setdefault("reg", True))
    Helper(FakeRunner(), paths).register_diag()
    assert called.get("reg") is True


def test_cli_deregister_diag_invokes_the_merge(paths, monkeypatch):
    called = {}
    monkeypatch.setattr(diag, "deregister", lambda: called.setdefault("dereg", True))
    Helper(FakeRunner(), paths).deregister_diag()
    assert called.get("dereg") is True


def test_parser_dispatches_register_and_deregister_diag():
    assert build_parser().parse_args(["register-diag"]).command == "register-diag"
    assert (
        build_parser().parse_args(["deregister-diag"]).command == "deregister-diag"
    )
