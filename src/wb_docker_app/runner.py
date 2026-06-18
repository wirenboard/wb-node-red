"""Subprocess seam shared by the thin wrappers.

The pure renderers never touch the system. The wrappers do — they shell out to
``docker``, ``systemctl``, ``nginx``, ``mosquitto`` (``docker compose`` itself
runs from the systemd unit, not from Python). To keep the orchestration
unit-testable without those binaries (real invocation is HITL on a controller),
every wrapper takes a :class:`Runner` and is asserted on the commands it issues.
``SubprocessRunner`` is the production implementation; tests inject a fake.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass(frozen=True)
class CommandResult:
    """Outcome of running one command."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""


class CommandError(RuntimeError):
    """Raised by a checked run when a command exits non-zero."""

    def __init__(self, result: CommandResult):
        self.result = result
        super().__init__(
            f"command {list(result.argv)!r} exited {result.returncode}: "
            f"{result.stderr.strip()}"
        )


class Runner(Protocol):
    """Runs a command and returns its result.

    When ``check`` is true, a non-zero exit must raise :class:`CommandError`.
    """

    def run(
        self, argv: Sequence[str], *, check: bool = True, input: str | None = None
    ) -> CommandResult: ...


class SubprocessRunner:
    """Production :class:`Runner` backed by :mod:`subprocess`."""

    def run(
        self, argv: Sequence[str], *, check: bool = True, input: str | None = None
    ) -> CommandResult:
        proc = subprocess.run(
            list(argv),
            input=input,
            capture_output=True,
            text=True,
        )
        result = CommandResult(
            argv=tuple(argv),
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )
        if check and proc.returncode != 0:
            raise CommandError(result)
        return result
