"""Compose runner (module F).

Issues ``docker compose`` commands for one app with a fixed project name and
the base+override file pair, base first (design.md §3.5, §3.6). Takes a
:class:`Runner` so the orchestration is asserted on the argv it issues.
"""

from __future__ import annotations

from pathlib import Path

from .runner import CommandResult, Runner


class ComposeRunner:
    """Runs ``docker compose`` for one app over a fixed base+override pair."""

    def __init__(
        self,
        base_path: Path,
        override_path: Path,
        project_name: str,
        runner: Runner,
        env_file: Path | None = None,
    ):
        self._base_path = base_path
        self._override_path = override_path
        self._project_name = project_name
        self._runner = runner
        self._env_file = env_file

    def _argv(self, *args: str) -> list[str]:
        # ``--env-file`` is a top-level flag (before the subcommand). It is
        # required because compose otherwise auto-loads the ``.env`` from the
        # project directory, which it derives from the FIRST ``-f`` file's
        # directory (the read-only base layer under /usr/lib) — NOT the
        # /mnt/data dir where the helper seeds the allocated WB_INTERNAL_PORT
        # (design.md §3.6). Without this, every up/down ignores the allocated
        # loopback port and publishes the compose default instead.
        env_args: list[str] = []
        if self._env_file is not None:
            env_args = ["--env-file", str(self._env_file)]
        return [
            "docker",
            "compose",
            "-p",
            self._project_name,
            *env_args,
            "-f",
            str(self._base_path),
            "-f",
            str(self._override_path),
            *args,
        ]

    def up(self) -> CommandResult:
        return self._runner.run(self._argv("up", "-d"))

    def down(self) -> CommandResult:
        return self._runner.run(self._argv("down"))

    def pull(self) -> CommandResult:
        return self._runner.run(self._argv("pull"))

    def ps(self) -> CommandResult:
        return self._runner.run(self._argv("ps"))

    def config(self) -> str:
        return self._runner.run(self._argv("config", "--format", "json")).stdout
