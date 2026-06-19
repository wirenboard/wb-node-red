"""Behavior of the systemd instance manager (module G).

Drives the ONE templated unit ``wb-docker-app@.service`` for a named app
(design.md §3.5.1): instances are ``wb-docker-app@<app>.service``, where ``%i``
resolves to the compose path. These tests assert the exact ``systemctl`` argv
the manager issues — the ``@<app>`` instance encoding is the crux — and that
``is_active`` maps the command outcome to a bool. A fake :class:`Runner`
records every argv and returns a programmed :class:`CommandResult`, so no real
subprocess runs.
"""

from wb_docker_app.runner import CommandResult
from wb_docker_app.systemd import SystemdInstanceManager


class FakeRunner:
    """Records argv it is asked to run and returns a programmed result."""

    def __init__(self, returncode: int = 0, stdout: str = ""):
        self.calls: list[tuple[str, ...]] = []
        self.checks: list[bool] = []
        self._returncode = returncode
        self._stdout = stdout

    def run(self, argv, *, check: bool = True, input: str | None = None):
        self.calls.append(tuple(argv))
        self.checks.append(check)
        return CommandResult(
            argv=tuple(argv), returncode=self._returncode, stdout=self._stdout
        )


def test_enable_enables_the_instance_on_the_right_unit():
    # Enable for boot only (no --now): install pairs this with an explicit
    # restart, so a new image tag is applied on upgrade (not just on first start).
    fake = FakeRunner()

    SystemdInstanceManager(fake).enable("node-red")

    assert fake.calls == [
        ("systemctl", "enable", "wb-docker-app@node-red.service")
    ]


def test_instance_name_encodes_the_app_after_the_at_sign():
    fake = FakeRunner()

    SystemdInstanceManager(fake).restart("grafana")

    assert fake.calls == [
        ("systemctl", "restart", "wb-docker-app@grafana.service")
    ]


def test_restart_issues_restart_on_the_instance():
    fake = FakeRunner()

    SystemdInstanceManager(fake).restart("node-red")

    assert fake.calls == [
        ("systemctl", "restart", "wb-docker-app@node-red.service")
    ]


def test_disable_disables_the_instance_with_now():
    fake = FakeRunner()

    SystemdInstanceManager(fake).disable("node-red")

    assert fake.calls == [
        (
            "systemctl",
            "disable",
            "--now",
            "wb-docker-app@node-red.service",
        )
    ]
