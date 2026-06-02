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


def test_enable_now_enables_the_instance_with_now_on_the_right_unit():
    fake = FakeRunner()

    SystemdInstanceManager(fake).enable_now("node-red")

    assert fake.calls == [
        (
            "systemctl",
            "enable",
            "--now",
            "wb-docker-app@node-red.service",
        )
    ]


def test_status_queries_the_instance_and_returns_its_stdout():
    fake = FakeRunner(stdout="● wb-docker-app@node-red.service - active\n")

    result = SystemdInstanceManager(fake).status("node-red")

    assert fake.calls == [
        ("systemctl", "status", "wb-docker-app@node-red.service")
    ]
    assert result.stdout == "● wb-docker-app@node-red.service - active\n"


def test_is_active_runs_is_active_unchecked_and_is_true_on_zero_exit():
    fake = FakeRunner(returncode=0, stdout="active\n")

    result = SystemdInstanceManager(fake).is_active("node-red")

    assert result is True
    assert fake.calls == [
        ("systemctl", "is-active", "wb-docker-app@node-red.service")
    ]
    # must not raise on a stopped unit: the query runs unchecked
    assert fake.checks == [False]


def test_is_active_is_false_on_non_zero_exit():
    fake = FakeRunner(returncode=3, stdout="inactive\n")

    assert SystemdInstanceManager(fake).is_active("node-red") is False


def test_instance_name_encodes_the_app_after_the_at_sign():
    fake = FakeRunner()

    SystemdInstanceManager(fake).start("grafana")

    assert fake.calls == [
        ("systemctl", "start", "wb-docker-app@grafana.service")
    ]


def test_start_stop_restart_issue_the_matching_verb_on_the_instance():
    for verb in ("start", "stop", "restart"):
        fake = FakeRunner()

        getattr(SystemdInstanceManager(fake), verb)("node-red")

        assert fake.calls == [
            ("systemctl", verb, "wb-docker-app@node-red.service")
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
