"""Behavior of the one-time MQTT provisioner (module H).

Run ONCE per system at helper install, the provisioner (design.md §3.7):
creates the docker network ``wb`` only if absent, writes the mosquitto gateway
listener drop-in, writes the ``After=docker.service`` systemd drop-in, and does
a single ``restart mosquitto``. A fake :class:`Runner` records every argv and
returns programmed returncodes (so ``docker network inspect`` can simulate
"present" vs "absent"); the two drop-in dirs are redirected to ``tmp_path`` so
no real ``/etc`` is touched.
"""

import pytest

from wb_docker_app.mqtt import (
    NetworkError,
    render_mosquitto_after_docker_dropin,
    render_mosquitto_listener,
)
from wb_docker_app.mqtt_provision import MqttProvisioner
from wb_docker_app.runner import CommandResult


class FakeRunner:
    """Records argv it runs; returns a programmed returncode per command.

    ``inspect_returncode`` is the code returned for ``docker network inspect``
    (0 = network present, non-zero = absent); everything else returns 0.
    """

    def __init__(self, inspect_returncode: int = 1):
        self.calls: list[tuple[str, ...]] = []
        self._inspect_returncode = inspect_returncode

    def run(self, argv, *, check: bool = True, input: str | None = None):
        self.calls.append(tuple(argv))
        argv = tuple(argv)
        returncode = 0
        if argv[:3] == ("docker", "network", "inspect"):
            returncode = self._inspect_returncode
        return CommandResult(argv=argv, returncode=returncode)


class StatefulFakeRunner:
    """Fake that models ``docker network`` state across calls.

    ``docker network create`` flips ``wb`` to present so a subsequent
    ``docker network inspect`` returns 0, letting one provisioner instance be
    re-run to exercise idempotency end-to-end.
    """

    def __init__(self, network_present: bool = False):
        self.calls: list[tuple[str, ...]] = []
        self._network_present = network_present

    def run(self, argv, *, check: bool = True, input: str | None = None):
        argv = tuple(argv)
        self.calls.append(argv)
        returncode = 0
        if argv[:3] == ("docker", "network", "inspect"):
            returncode = 0 if self._network_present else 1
        if argv[:3] == ("docker", "network", "create"):
            self._network_present = True
        return CommandResult(argv=argv, returncode=returncode)


def _provisioner(runner, tmp_path, **overrides):
    kwargs = dict(
        subnet="172.29.0.0/24",
        gateway="172.29.0.1",
        listener_port=11883,
        mosquitto_conf_dir=tmp_path / "conf.d",
        mosquitto_dropin_dir=tmp_path / "dropin.d",
    )
    kwargs.update(overrides)
    (tmp_path / "conf.d").mkdir(exist_ok=True)
    (tmp_path / "dropin.d").mkdir(exist_ok=True)
    return MqttProvisioner(runner, **kwargs)


def test_creates_the_wb_network_when_absent_with_the_subnet_and_gateway(tmp_path):
    fake = FakeRunner(inspect_returncode=1)

    _provisioner(fake, tmp_path).provision()

    assert (
        "docker",
        "network",
        "create",
        "--subnet",
        "172.29.0.0/24",
        "--gateway",
        "172.29.0.1",
        "wb",
    ) in fake.calls


def test_does_not_create_the_wb_network_when_it_is_already_present(tmp_path):
    fake = FakeRunner(inspect_returncode=0)

    _provisioner(fake, tmp_path).provision()

    creates = [c for c in fake.calls if c[:3] == ("docker", "network", "create")]
    assert creates == []


def test_writes_the_mosquitto_listener_dropin_with_the_rendered_content(tmp_path):
    fake = FakeRunner()

    _provisioner(fake, tmp_path).provision()

    written = list((tmp_path / "conf.d").iterdir())
    assert len(written) == 1
    assert written[0].read_text() == render_mosquitto_listener(
        gateway="172.29.0.1", port=11883
    )


def test_writes_the_after_docker_systemd_dropin_with_the_rendered_content(tmp_path):
    fake = FakeRunner()

    _provisioner(fake, tmp_path).provision()

    written = list((tmp_path / "dropin.d").iterdir())
    assert len(written) == 1
    assert written[0].read_text() == render_mosquitto_after_docker_dropin()


def test_restarts_mosquitto_exactly_once(tmp_path):
    fake = FakeRunner()

    _provisioner(fake, tmp_path).provision()

    restarts = [
        c for c in fake.calls if c == ("systemctl", "restart", "mosquitto")
    ]
    assert restarts == [("systemctl", "restart", "mosquitto")]


def test_gateway_outside_the_subnet_propagates_network_error(tmp_path):
    fake = FakeRunner()

    with pytest.raises(NetworkError):
        _provisioner(fake, tmp_path, gateway="172.30.0.1")


def test_reprovisioning_is_a_noop_and_does_not_restart_mosquitto_again(tmp_path):
    # First run from a clean system: creates the network, writes both drop-ins
    # and restarts mosquitto exactly once.
    fake = StatefulFakeRunner(network_present=False)
    provisioner = _provisioner(fake, tmp_path)

    provisioner.provision()
    first_restarts = [
        c for c in fake.calls if c == ("systemctl", "restart", "mosquitto")
    ]
    assert first_restarts == [("systemctl", "restart", "mosquitto")]

    # Second run (e.g. a helper upgrade) finds the network present and the
    # drop-ins already holding the exact rendered text: a complete no-op. The
    # network is not re-created and mosquitto is NOT restarted again.
    fake.calls.clear()
    provisioner.provision()

    assert [c for c in fake.calls if c[:3] == ("docker", "network", "create")] == []
    assert [c for c in fake.calls if c == ("systemctl", "restart", "mosquitto")] == []


def test_reprovisioning_restarts_when_the_listener_dropin_drifted(tmp_path):
    # If the rendered config changes (e.g. a new listener port), a re-run must
    # rewrite it and restart mosquitto so the new listener actually binds.
    fake = StatefulFakeRunner(network_present=False)
    _provisioner(fake, tmp_path).provision()

    fake.calls.clear()
    _provisioner(fake, tmp_path, listener_port=21883).provision()

    assert ("systemctl", "restart", "mosquitto") in fake.calls
