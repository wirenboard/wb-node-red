"""Behavior of the CLI orchestrator (module I).

The orchestrator is deliberately thin glue over an injected :class:`Runner`.
Services are CURATED and static: each hardcodes its loopback port and ships its
own static nginx drop-in, so the helper neither allocates ports nor renders
nginx at runtime. Its verbs are minimal — ``install``, ``remove``,
``provision-mqtt``. Tests drive it with a fake :class:`Runner` (recording argv,
returning canned results) and a ``Paths`` rooted at ``tmp_path`` — no
docker/systemd/nginx is touched.
"""

import pytest

from wb_docker_app.cli import Helper, Paths, build_parser
from wb_docker_app.runner import CommandResult


class FakeRunner:
    """Records every argv; returns rc 0 except a failing `docker network inspect`."""

    def __init__(self):
        self.calls = []

    def run(self, argv, *, check=True, input=None):
        argv = list(argv)
        self.calls.append(argv)
        returncode = 0
        # On a clean system the `wb` network is absent, so inspect fails — this
        # lets provision_mqtt exercise the create path.
        if tuple(argv[:3]) == ("docker", "network", "inspect"):
            returncode = 1
        return CommandResult(argv=tuple(argv), returncode=returncode, stdout="")

    def issued(self, *needles):
        """True if some recorded call contains all the given substrings."""
        return any(
            all(n in " ".join(c) for n in needles) for c in self.calls
        )


@pytest.fixture
def paths(tmp_path):
    return Paths(
        data_dir=tmp_path / "mnt/data/wb-docker-apps",
        pkg_dir=tmp_path / "usr/lib/wb-docker-app",
        mosquitto_conf_dir=tmp_path / "etc/mosquitto/conf.d",
        sysctl_file=tmp_path / "etc/sysctl.d/60-wb-docker-app.conf",
        mqtt_marker_file=tmp_path / "var/lib/wb-docker-app/mqtt-provisioned",
    )


def test_install_seeds_the_user_layer_and_enables_the_systemd_instance(paths):
    runner = FakeRunner()
    helper = Helper(runner, paths)

    helper.install("node-red")

    # user layer seeded only-if-absent under /mnt/data
    override = paths.data_dir / "node-red" / "docker-compose.override.yml"
    assert override.exists()
    assert (paths.data_dir / "node-red" / "data").is_dir()

    # service enabled+started under the templated systemd unit
    assert runner.issued("systemctl", "enable", "--now",
                         "wb-docker-app@node-red.service")


def test_install_seeds_the_packages_default_flows_when_absent(paths):
    # The package ships its default config under <app>/seed/, mirroring the user
    # layout; install drops it into /mnt/data only-if-absent so Node-RED comes up
    # with a pre-wired broker node out of the box.
    seed_flows = paths.pkg_dir / "node-red" / "seed" / "data" / "flows.json"
    seed_flows.parent.mkdir(parents=True)
    seed_flows.write_text('[{"id": "wb-mqtt-broker"}]\n')

    runner = FakeRunner()
    helper = Helper(runner, paths)

    helper.install("node-red")

    landed = paths.data_dir / "node-red" / "data" / "flows.json"
    assert landed.read_text() == '[{"id": "wb-mqtt-broker"}]\n'


def test_install_does_not_clobber_a_users_flows(paths):
    seed_flows = paths.pkg_dir / "node-red" / "seed" / "data" / "flows.json"
    seed_flows.parent.mkdir(parents=True)
    seed_flows.write_text('[{"id": "wb-mqtt-broker"}]\n')

    user_flows = paths.data_dir / "node-red" / "data" / "flows.json"
    user_flows.parent.mkdir(parents=True)
    user_flows.write_text('[{"id": "user-edit"}]\n')

    runner = FakeRunner()
    helper = Helper(runner, paths)

    helper.install("node-red")

    assert user_flows.read_text() == '[{"id": "user-edit"}]\n'  # user edit survives


def test_install_tests_the_nginx_config_before_reloading_it(paths):
    runner = FakeRunner()
    helper = Helper(runner, paths)

    helper.install("node-red")

    # The static proxy drop-in shipped by the service package must pass
    # `nginx -t` before the reload; a failing test aborts before reload.
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


def test_install_does_not_reseed_user_overrides_on_reinstall(paths):
    runner = FakeRunner()
    helper = Helper(runner, paths)
    helper.install("node-red")

    override = paths.data_dir / "node-red" / "docker-compose.override.yml"
    override.write_text("services:\n  node-red:\n    mem_limit: 256m\n")

    helper.install("node-red")

    assert "mem_limit: 256m" in override.read_text()  # user edit survives


def test_remove_disables_the_unit_without_reloading_nginx_leaving_data_intact(paths):
    runner = FakeRunner()
    helper = Helper(runner, paths)
    helper.install("node-red")
    override = paths.data_dir / "node-red" / "docker-compose.override.yml"
    assert override.exists()
    runner.calls.clear()

    helper.remove("node-red")

    assert runner.issued("systemctl", "disable", "--now",
                         "wb-docker-app@node-red.service")
    # The static drop-in is a dpkg-owned file deleted AFTER this prerm runs, so
    # remove must NOT reload nginx — the reload happens in the postrm via
    # reload-proxy, once the file is gone.
    assert not runner.issued("systemctl", "reload", "nginx")
    assert not runner.issued("nginx", "-t")
    # /mnt/data is left intact on remove.
    assert override.exists()


def test_parser_dispatches_install_with_an_app_argument():
    args = build_parser().parse_args(["install", "node-red"])
    assert args.command == "install"
    assert args.app == "node-red"


def test_parser_dispatches_remove_with_an_app_argument():
    args = build_parser().parse_args(["remove", "node-red"])
    assert args.command == "remove"
    assert args.app == "node-red"


def test_parser_dispatches_provision_mqtt_without_an_app_argument():
    args = build_parser().parse_args(["provision-mqtt"])
    assert args.command == "provision-mqtt"


def test_parser_dispatches_reload_proxy_without_an_app_argument():
    args = build_parser().parse_args(["reload-proxy"])
    assert args.command == "reload-proxy"


def test_reload_proxy_reloads_only_after_a_passing_config_test(paths):
    runner = FakeRunner()
    helper = Helper(runner, paths)

    helper.reload_proxy()

    test_idx = runner.calls.index(["nginx", "-t"])
    reload_idx = runner.calls.index(["systemctl", "reload", "nginx"])
    assert test_idx < reload_idx


def test_reload_proxy_skips_the_reload_and_never_raises_on_a_bad_config(paths):
    # Tolerant on purpose: an unrelated broken nginx config elsewhere must not
    # block package removal. `nginx -t` is run unchecked; on a non-zero result
    # the reload is skipped and nothing raises.
    class BadConfigRunner(FakeRunner):
        def run(self, argv, *, check=True, input=None):
            if list(argv) == ["nginx", "-t"]:
                self.calls.append(list(argv))
                return CommandResult(argv=tuple(argv), returncode=1, stdout="")
            return super().run(argv, check=check, input=input)

    runner = BadConfigRunner()
    helper = Helper(runner, paths)

    helper.reload_proxy()  # must not raise

    assert ["systemctl", "reload", "nginx"] not in runner.calls


def test_parser_rejects_the_dropped_day2_verbs():
    # status/logs/restart/update/list are no longer helper verbs; use systemctl
    # and docker directly. The parser must reject them.
    for verb in ("status", "logs", "restart", "update", "list"):
        with pytest.raises(SystemExit):
            build_parser().parse_args([verb, "node-red"])


def test_provision_mqtt_creates_the_network_and_restarts_mosquitto_once(paths):
    # MQTT connectivity is provisioned by the helper at install time: it creates
    # the `wb` docker network, sets ip_nonlocal_bind and restarts mosquitto
    # exactly once (design.md §3.7).
    runner = FakeRunner()
    helper = Helper(runner, paths)

    helper.provision_mqtt()

    assert runner.issued("docker", "network", "create", "wb")
    restarts = [
        c for c in runner.calls if c == ["systemctl", "restart", "mosquitto"]
    ]
    assert restarts == [["systemctl", "restart", "mosquitto"]]
    # listener drop-in + sysctl drop-in written and the sysctl applied
    assert (paths.mosquitto_conf_dir / "wb.conf").exists()
    assert paths.sysctl_file.exists()
    assert runner.issued("sysctl", "-p", str(paths.sysctl_file))


def test_provision_mqtt_writes_no_after_docker_dropin(paths):
    # The boot-timing crux: mosquitto keeps its early boot; nothing the helper
    # writes may order it After=docker.service.
    runner = FakeRunner()
    helper = Helper(runner, paths)

    helper.provision_mqtt()

    assert "After=docker.service" not in (
        paths.mosquitto_conf_dir / "wb.conf"
    ).read_text()
    assert "After=docker.service" not in paths.sysctl_file.read_text()


def test_a_service_install_never_touches_mosquitto(paths):
    # Provisioning is the helper's job, run once at helper install — NOT per
    # service. Installing a service must never restart mosquitto.
    runner = FakeRunner()
    helper = Helper(runner, paths)

    helper.install("node-red")

    assert not runner.issued("systemctl", "restart", "mosquitto")
    assert not runner.issued("docker", "network", "create")
