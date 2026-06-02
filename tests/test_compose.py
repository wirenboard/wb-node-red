"""Behavior of the compose runner (module F).

Asserts the exact ``docker compose`` argv the wrapper issues for one app —
fixed project name, the base-then-override ``-f`` pair (design.md §3.6), and
the right subcommand/flags. A fake :class:`Runner` records every argv and
returns a canned :class:`CommandResult`, so no real subprocess runs.
"""

from pathlib import Path

from wb_docker_app.compose import ComposeRunner
from wb_docker_app.runner import CommandResult


class FakeRunner:
    """Records argv it is asked to run and returns a programmed result."""

    def __init__(self, stdout: str = ""):
        self.calls: list[tuple[str, ...]] = []
        self._stdout = stdout

    def run(self, argv, *, check: bool = True, input: str | None = None):
        self.calls.append(tuple(argv))
        return CommandResult(
            argv=tuple(argv), returncode=0, stdout=self._stdout
        )


def _compose(runner):
    return ComposeRunner(
        base_path=Path("/usr/lib/wb-docker-app/node-red/docker-compose.yml"),
        override_path=Path(
            "/mnt/data/wb-docker-apps/node-red/docker-compose.override.yml"
        ),
        project_name="node-red",
        runner=runner,
        # The allocated WB_INTERNAL_PORT lives in the seeded .env under the
        # user-layer data dir, which compose does NOT auto-load (it derives the
        # project dir from the first -f file's dir, the read-only base layer);
        # the wrapper must pass it explicitly with --env-file.
        env_file=Path("/mnt/data/wb-docker-apps/node-red/.env"),
    )


def test_up_issues_compose_up_detached_with_base_then_override():
    fake = FakeRunner()

    _compose(fake).up()

    assert fake.calls == [
        (
            "docker",
            "compose",
            "-p",
            "node-red",
            "--env-file",
            "/mnt/data/wb-docker-apps/node-red/.env",
            "-f",
            "/usr/lib/wb-docker-app/node-red/docker-compose.yml",
            "-f",
            "/mnt/data/wb-docker-apps/node-red/docker-compose.override.yml",
            "up",
            "-d",
        )
    ]


def _prefix():
    return (
        "docker",
        "compose",
        "-p",
        "node-red",
        "--env-file",
        "/mnt/data/wb-docker-apps/node-red/.env",
        "-f",
        "/usr/lib/wb-docker-app/node-red/docker-compose.yml",
        "-f",
        "/mnt/data/wb-docker-apps/node-red/docker-compose.override.yml",
    )


def test_down_issues_compose_down():
    fake = FakeRunner()

    _compose(fake).down()

    assert fake.calls == [_prefix() + ("down",)]


def test_pull_issues_compose_pull():
    fake = FakeRunner()

    _compose(fake).pull()

    assert fake.calls == [_prefix() + ("pull",)]


def test_ps_issues_compose_ps_and_returns_stdout():
    fake = FakeRunner(stdout="NAME   STATUS\nnode-red   running\n")

    result = _compose(fake).ps()

    assert fake.calls == [_prefix() + ("ps",)]
    assert result.stdout == "NAME   STATUS\nnode-red   running\n"


def test_config_issues_compose_config_json_and_returns_stdout():
    fake = FakeRunner(stdout='{"services": {}}')

    out = _compose(fake).config()

    assert fake.calls == [_prefix() + ("config", "--format", "json")]
    assert out == '{"services": {}}'
