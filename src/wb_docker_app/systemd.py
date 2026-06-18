"""Systemd instance manager.

Drives the ONE templated unit ``wb-docker-app@.service``.
Each app is a distinct instance ``wb-docker-app@<app>.service`` where ``%i``
resolves to the compose path. This wrapper turns the manager's verbs into the
matching ``systemctl`` invocations against that instance; it takes a
:class:`Runner` so the issued commands can be asserted without a real
subprocess.
"""

from __future__ import annotations

from .runner import Runner


def _unit(app: str) -> str:
    """The templated-unit instance name for ``app``."""
    return f"wb-docker-app@{app}.service"


class SystemdInstanceManager:
    """Drives ``systemctl`` for ``wb-docker-app@<app>.service`` instances."""

    def __init__(self, runner: Runner):
        self._runner = runner

    def enable_now(self, app: str):
        return self._runner.run(
            ["systemctl", "enable", "--now", _unit(app)]
        )

    def disable(self, app: str):
        return self._runner.run(
            ["systemctl", "disable", "--now", _unit(app)]
        )

    def start(self, app: str):
        return self._runner.run(["systemctl", "start", _unit(app)])

    def stop(self, app: str):
        return self._runner.run(["systemctl", "stop", _unit(app)])

    def restart(self, app: str):
        return self._runner.run(["systemctl", "restart", _unit(app)])

    def status(self, app: str):
        # ``systemctl status`` exits non-zero for an inactive unit, which is a
        # legitimate answer to a query — run it unchecked so it never raises.
        return self._runner.run(
            ["systemctl", "status", _unit(app)], check=False
        )

    def is_active(self, app: str) -> bool:
        result = self._runner.run(
            ["systemctl", "is-active", _unit(app)], check=False
        )
        return result.returncode == 0
