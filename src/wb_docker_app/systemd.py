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

    def enable(self, app: str):
        return self._runner.run(["systemctl", "enable", _unit(app)])

    def disable(self, app: str):
        return self._runner.run(
            ["systemctl", "disable", "--now", _unit(app)]
        )

    def restart(self, app: str):
        return self._runner.run(["systemctl", "restart", _unit(app)])
