"""Second-service isolation at the orchestrator level (issue #7).

Proves the "thin per-app package + shared helper" model holds when TWO services
are managed by the same :class:`Helper`. Both are driven purely through the
shared lifecycle (no per-service code path): we install Node-RED and a second
stub service (``echo``) through the same orchestrator, with a fake
:class:`Runner` and a ``Paths`` rooted at ``tmp_path`` — no docker/systemd/nginx
is touched.

We assert isolation:

* the two apps get DISTINCT allocated internal loopback ports;
* the two nginx server-blocks are distinct and do not collide (different public
  ports, different proxied loopback ports, one file per app);
* the one-time MQTT/network provisioning (provisioner H) is NOT re-run per
  service — installing apps never issues ``docker network create`` nor
  ``systemctl restart mosquitto`` (that is the helper-install responsibility,
  not per-app);
* removing one app leaves the other's container/unit/nginx-block/port intact.

The fake renders ``docker compose config`` from the *real* per-app base compose
shipped in ``packaging/`` plus the ``.env`` the helper seeds, so the descriptor
the orchestrator reads reflects each service's true labels and its actually
allocated loopback port — exactly what nginx would proxy to on a controller.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from wb_docker_app.cli import Helper, Paths
from wb_docker_app.runner import CommandResult

REPO = Path(__file__).resolve().parent.parent
NODE_RED_COMPOSE = (
    REPO / "packaging" / "wb-node-red" / "compose" / "node-red" / "docker-compose.yml"
)
ECHO_COMPOSE = REPO / "packaging" / "wb-echo" / "compose" / "echo" / "docker-compose.yml"


def _parse_base_compose(path: Path) -> dict:
    """Pull the single service's wb.* labels and container port out of a base
    compose file by hand (no PyYAML dependency in the suite)."""
    text = path.read_text()
    labels = dict(re.findall(r"^\s+(wb\.[\w.]+):\s*\"?([^\"\n]+)\"?\s*$", text, re.M))
    # container target port from the ports mapping ``...:<published>:<target>``
    target = re.search(r":\$\{WB_INTERNAL_PORT[^}]*\}:(\d+)\"", text)
    image = re.search(r"^\s+image:\s*(\S+)\s*$", text, re.M)
    return {
        "labels": {k: v.strip() for k, v in labels.items()},
        "target": int(target.group(1)),
        "image": image.group(1),
    }


class FakeRunner:
    """Records every argv; for ``compose config`` it renders the canonical
    ``docker compose config --format json`` from the per-app base compose and
    the ``.env`` the helper just seeded, so the published loopback port reflects
    the real allocation. Every other command returns rc 0.

    ``compose_by_base`` maps an app's base ``docker-compose.yml`` path to the
    parsed base, and ``env_dir`` resolves the seeded ``.env`` to read the
    allocated ``WB_INTERNAL_PORT``.
    """

    def __init__(self, paths: Paths, bases: dict[str, dict]):
        self.calls: list[list[str]] = []
        self._paths = paths
        self._bases = bases  # keyed by absolute base compose path (str)

    def _published_port(self, app: str) -> int:
        env = (self._paths.data_dir / app / ".env").read_text()
        m = re.search(r"WB_INTERNAL_PORT=(\d+)", env)
        return int(m.group(1))

    def run(self, argv, *, check=True, input=None):
        argv = list(argv)
        self.calls.append(argv)
        stdout = ""
        if "config" in argv:
            base_path = argv[argv.index("-f") + 1]
            base = self._bases[base_path]
            app = base["labels"]["wb.app"]
            published = self._published_port(app)
            config = {
                "services": {
                    app: {
                        "image": base["image"],
                        "ports": [
                            {
                                "host_ip": "127.0.0.1",
                                "target": base["target"],
                                "published": str(published),
                                "protocol": "tcp",
                            }
                        ],
                        "labels": base["labels"],
                    }
                }
            }
            stdout = json.dumps(config)
        return CommandResult(argv=tuple(argv), returncode=0, stdout=stdout)

    def issued(self, *needles):
        return any(all(n in " ".join(c) for n in needles) for c in self.calls)


@pytest.fixture
def paths(tmp_path):
    return Paths(
        base_dir=tmp_path / "usr/lib/wb-docker-app",
        data_dir=tmp_path / "mnt/data/wb-docker-apps",
        nginx_includes=tmp_path / "etc/nginx/includes/default.wb.d",
        port_registry=tmp_path / "var/lib/wb-docker-app/ports.json",
    )


@pytest.fixture
def helper(paths):
    """A helper whose base layer holds the real per-app base compose files, and
    a fake runner that renders config from them."""
    bases: dict[str, dict] = {}
    for app, compose in (("node-red", NODE_RED_COMPOSE), ("echo", ECHO_COMPOSE)):
        base_path = paths.base_dir / app / "docker-compose.yml"
        base_path.parent.mkdir(parents=True, exist_ok=True)
        base_path.write_text(compose.read_text())
        bases[str(base_path)] = _parse_base_compose(compose)
    runner = FakeRunner(paths, bases)
    return Helper(runner, paths)


def _block(paths: Paths, app: str) -> Path:
    return paths.nginx_includes / f"{app}.conf"


def test_two_apps_get_distinct_internal_loopback_ports(helper, paths):
    helper.install("node-red")
    helper.install("echo")

    node_red_port = (paths.data_dir / "node-red" / ".env").read_text()
    echo_port = (paths.data_dir / "echo" / ".env").read_text()

    nr = int(re.search(r"WB_INTERNAL_PORT=(\d+)", node_red_port).group(1))
    echo = int(re.search(r"WB_INTERNAL_PORT=(\d+)", echo_port).group(1))
    assert nr != echo  # the allocator never hands the same loopback port twice


def test_two_apps_get_distinct_non_colliding_nginx_blocks(helper, paths):
    helper.install("node-red")
    helper.install("echo")

    nr_block = _block(paths, "node-red").read_text()
    echo_block = _block(paths, "echo").read_text()

    # one file per app, distinct contents
    assert nr_block != echo_block

    # distinct PUBLIC listen ports (from wb.proxy.port) — no collision
    nr_listen = re.search(r"listen (\d+);", nr_block).group(1)
    echo_listen = re.search(r"listen (\d+);", echo_block).group(1)
    assert nr_listen == "1880"
    assert echo_listen == "8080"
    assert nr_listen != echo_listen

    # each block proxies to its OWN allocated internal loopback port
    nr_proxy = re.search(r"127\.0\.0\.1:(\d+)", nr_block).group(1)
    echo_proxy = re.search(r"127\.0\.0\.1:(\d+)", echo_block).group(1)
    assert nr_proxy != echo_proxy
    # ...and that proxied port is the one the helper actually allocated
    assert nr_proxy == re.search(
        r"WB_INTERNAL_PORT=(\d+)", (paths.data_dir / "node-red" / ".env").read_text()
    ).group(1)
    assert echo_proxy == re.search(
        r"WB_INTERNAL_PORT=(\d+)", (paths.data_dir / "echo" / ".env").read_text()
    ).group(1)


def test_compose_up_loads_each_apps_seeded_env_file(helper, paths):
    # The allocated WB_INTERNAL_PORT is seeded into /mnt/data/.../<app>/.env,
    # but compose auto-loads .env from the FIRST -f file's directory — the
    # read-only base layer under /usr/lib — NOT that data dir. Unless the
    # orchestrator (and the systemd unit) pass --env-file explicitly, every
    # up/down ignores the allocation and publishes the compose default port,
    # silently mismatching the nginx block rendered from `compose config`.
    helper.install("node-red")
    helper.install("echo")

    runner = helper.runner

    def assert_env_file(app: str) -> None:
        env_path = str(paths.data_dir / app / ".env")
        ups = [
            c
            for c in runner.calls
            if c[:4] == ["docker", "compose", "-p", f"wb-{app}"]
            and "up" in c
        ]
        assert ups, f"no compose up issued for {app}"
        for argv in ups:
            assert "--env-file" in argv, argv
            assert argv[argv.index("--env-file") + 1] == env_path, argv

    assert_env_file("node-red")
    assert_env_file("echo")


def test_installing_apps_never_reruns_one_time_mqtt_provisioning(helper):
    helper.install("node-red")
    helper.install("echo")

    runner = helper.runner
    # provisioner H is helper-install scope, not per-app: the per-app install
    # path must never create the network nor restart the broker.
    assert not runner.issued("docker", "network", "create")
    assert not runner.issued("systemctl", "restart", "mosquitto")
    # nor inspect/touch the wb network at all from the per-app flow
    assert not runner.issued("docker", "network")


def test_removing_one_app_leaves_the_other_intact(helper, paths):
    helper.install("node-red")
    helper.install("echo")

    nr_port_before = (paths.data_dir / "node-red" / ".env").read_text()
    nr_block_before = _block(paths, "node-red").read_text()

    helper.remove("echo")

    # echo is gone...
    assert not _block(paths, "echo").exists()
    assert helper.allocator._assigned.get("echo") is None
    runner = helper.runner
    assert runner.issued("systemctl", "disable", "--now", "wb-docker-app@echo.service")
    assert runner.issued("docker", "compose", "-p", "wb-echo", "down")

    # ...node-red's container/unit/nginx-block/port are untouched
    assert _block(paths, "node-red").exists()
    assert _block(paths, "node-red").read_text() == nr_block_before
    assert (paths.data_dir / "node-red" / ".env").read_text() == nr_port_before
    assert "node-red" in helper.allocator._assigned
    assert not runner.issued(
        "systemctl", "disable", "--now", "wb-docker-app@node-red.service"
    )
    assert not runner.issued("docker", "compose", "-p", "wb-node-red", "down")
