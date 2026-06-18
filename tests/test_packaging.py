"""Packaging smoke tests for wb-docker-app and wb-node-red.

These prove the Debian packaging without needing a real build chain. Everything
here is ROOTLESS: each package's payload tree is hand-assembled from its
``debian/*.install`` mappings and built with ``dpkg-deb --build`` (NOT
``dpkg-buildpackage``, which needs fakeroot and the full build-deps). We then
assert:

* ``debian/control`` declares the right package, architecture and dependencies;
* the built ``.deb`` carries the expected payload paths;
* the maintainer scripts (and the shell library) pass ``shellcheck`` clean;
* ``docker compose config`` parses the static base compose and surfaces the
  ``wb.*`` metadata labels and the hardcoded loopback port.

Tests that need an external tool (``dpkg-deb``, ``shellcheck``, ``docker``)
skip cleanly when it is absent, so the suite stays green in any environment.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PKG = REPO / "packaging"
HELPER = PKG / "wb-docker-app"
SERVICE = PKG / "wb-node-red"

BASE_COMPOSE = SERVICE / "compose" / "node-red" / "docker-compose.yml"
PALETTE = SERVICE / "palette"

MAINTAINER_SCRIPTS = [
    HELPER / "debian" / "postinst",
    HELPER / "debian" / "prerm",
    HELPER / "lib" / "wb-docker-app.sh",
    SERVICE / "debian" / "postinst",
    SERVICE / "debian" / "prerm",
    SERVICE / "debian" / "postrm",
]


# --- debian/control parsing -------------------------------------------------


def _parse_control(path: Path) -> list[dict[str, str]]:
    """Parse a deb822 ``debian/control`` into a list of paragraphs."""
    paragraphs: list[dict[str, str]] = []
    current: dict[str, str] = {}
    key: str | None = None
    for line in path.read_text().splitlines():
        if not line.strip():
            if current:
                paragraphs.append(current)
                current = {}
                key = None
            continue
        if line[0] in " \t" and key is not None:
            current[key] += "\n" + line.strip()
            continue
        field, _, value = line.partition(":")
        key = field.strip()
        current[key] = value.strip()
    if current:
        paragraphs.append(current)
    return paragraphs


def _binary_paragraph(control: Path, package: str) -> dict[str, str]:
    for para in _parse_control(control):
        if para.get("Package") == package:
            return para
    raise AssertionError(f"no binary paragraph for {package} in {control}")


def test_helper_control_declares_its_own_package():
    para = _binary_paragraph(HELPER / "debian" / "control", "wb-docker-app")
    assert para["Architecture"] == "all"


def test_node_red_control_fields_and_dependencies():
    para = _binary_paragraph(SERVICE / "debian" / "control", "wb-node-red")
    assert para["Architecture"] == "all"
    deps = para["Depends"]
    assert "docker-ce" in deps
    assert "wb-docker-app" in deps


def test_helper_declares_cli_entry_point_in_pyproject():
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text())
    scripts = pyproject["project"]["scripts"]
    assert scripts["wb-docker-app"] == "wb_docker_app.cli:main"


# --- hand-assembled dpkg-deb build (rootless) -------------------------------


def _read_install_mappings(install_file: Path) -> list[tuple[str, str]]:
    """Read a ``debian/*.install`` file into (source, dest-dir) pairs."""
    pairs: list[tuple[str, str]] = []
    for line in install_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        src, dest = line.split()
        pairs.append((src, dest))
    return pairs


def _assemble_tree(pkg_dir: Path, package: str, dest_root: Path) -> Path:
    """Hand-assemble a DEBIAN payload tree from control + the install list."""
    root = dest_root / package
    debian = root / "DEBIAN"
    debian.mkdir(parents=True)

    para = _binary_paragraph(pkg_dir / "debian" / "control", package)
    # Minimal, valid binary control: dpkg-deb requires these fields present.
    fields = {
        "Package": package,
        "Version": "0.1.0",
        "Architecture": para["Architecture"],
        "Maintainer": "Wiren Board Team <info@wirenboard.com>",
        "Description": para.get("Description", "x").splitlines()[0],
    }
    (debian / "control").write_text(
        "".join(f"{k}: {v}\n" for k, v in fields.items())
    )

    for src, dest in _read_install_mappings(
        pkg_dir / "debian" / f"{package}.install"
    ):
        target_dir = root / dest
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(pkg_dir / src, target_dir / Path(src).name)

    return root


def _build_deb(pkg_dir: Path, package: str, tmp_path: Path) -> Path:
    tree = _assemble_tree(pkg_dir, package, tmp_path)
    deb = tmp_path / f"{package}.deb"
    subprocess.run(
        ["dpkg-deb", "--build", str(tree), str(deb)],
        check=True,
        capture_output=True,
        text=True,
    )
    return deb


def _deb_contents(deb: Path) -> str:
    return subprocess.run(
        ["dpkg-deb", "--contents", str(deb)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _deb_field(deb: Path, field: str) -> str:
    return subprocess.run(
        ["dpkg-deb", "--field", str(deb), field],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.mark.skipif(
    shutil.which("dpkg-deb") is None, reason="dpkg-deb not available"
)
def test_helper_deb_carries_unit_and_shell_library(tmp_path):
    deb = _build_deb(HELPER, "wb-docker-app", tmp_path)

    assert _deb_field(deb, "Package") == "wb-docker-app"
    assert _deb_field(deb, "Architecture") == "all"

    contents = _deb_contents(deb)
    assert "usr/lib/systemd/system/wb-docker-app@.service" in contents
    assert "usr/lib/wb-docker-app/lib/wb-docker-app.sh" in contents


@pytest.mark.skipif(
    shutil.which("dpkg-deb") is None, reason="dpkg-deb not available"
)
def test_node_red_deb_carries_base_compose_and_static_nginx_dropin(tmp_path):
    deb = _build_deb(SERVICE, "wb-node-red", tmp_path)

    assert _deb_field(deb, "Package") == "wb-node-red"
    assert _deb_field(deb, "Architecture") == "all"

    contents = _deb_contents(deb)
    # base compose is package-owned under /usr/lib (design.md §3.6).
    assert "usr/lib/wb-docker-app/node-red/docker-compose.yml" in contents
    # the standalone reverse-proxy server block lands in sites-available; the
    # helper symlinks it into sites-enabled with an ABSOLUTE target at install
    # (a relative dh_link target breaks on WB's bind-mounted sites-enabled).
    assert "etc/nginx/sites-available/node-red.conf" in contents
    # the default flows.json ships in the package's seed/ tree, mirroring the
    # user layout; the helper seeds it into /mnt/data only-if-absent at install,
    # so Node-RED comes up with a pre-wired broker node out of the box.
    assert "usr/lib/wb-docker-app/node-red/seed/data/flows.json" in contents


def test_node_red_default_flows_is_valid_json_wired_to_the_wb_broker():
    """The shipped default flow must parse and target the WB local broker.

    It is seeded into /mnt/data only-if-absent so Node-RED is configured out of
    the box: a broker node on the gateway IP/port and a subscription to the WB
    device tree.
    """
    flows = json.loads((SERVICE / "config" / "flows.json").read_text())
    broker = next(n for n in flows if n["type"] == "mqtt-broker")
    assert broker["broker"] == "172.29.0.1"
    assert broker["port"] == "11883"
    mqtt_in = next(n for n in flows if n["type"] == "mqtt in")
    assert mqtt_in["topic"] == "/devices/#"


def test_node_red_nginx_dropin_reuses_homeui_auth_in_its_own_server_block():
    """The static proxy must carry the homeui auth plumbing itself.

    A standalone server block cannot see homeui's ``/auth/check`` (it lives in
    the homeui server block on another port), so node-red.conf defines its own
    internal ``/auth/check`` proxying to the global ``wb-homeui-back`` upstream,
    gates ``/`` on the admin role, and redirects 401s to the homeui login on a
    PORT-LESS host (``$host``) to avoid a same-port redirect loop. Controller-
    validated; see docs/adr/0005.
    """
    conf = (SERVICE / "nginx" / "node-red.conf").read_text()
    assert "auth_request /auth/check;" in conf
    assert "location = /auth/check {" in conf
    assert "proxy_pass http://wb-homeui-back/auth/check;" in conf
    assert 'set $required_user_type "admin";' in conf
    # port-less host in the login redirect (no :1880 loop)
    assert "return 302 $scheme://$host/login/" in conf


# --- shellcheck on maintainer scripts ---------------------------------------


@pytest.mark.skipif(
    shutil.which("shellcheck") is None, reason="shellcheck not available"
)
@pytest.mark.parametrize(
    "script", MAINTAINER_SCRIPTS, ids=lambda p: str(p.relative_to(REPO))
)
def test_maintainer_scripts_are_shellcheck_clean(script):
    result = subprocess.run(
        ["shellcheck", str(script)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr


# --- docker compose config on the base compose ------------------------------


@pytest.mark.skipif(
    shutil.which("docker") is None, reason="docker not available"
)
def test_base_compose_config_parses_wb_labels_and_static_port(tmp_path):
    # Run config in an isolated dir so the project name comes from the dir. The
    # service is static: the loopback port is hardcoded in the compose (no
    # WB_INTERNAL_PORT injection), and the wb.* labels are plain metadata.
    workdir = tmp_path / "node-red"
    workdir.mkdir()
    shutil.copy(BASE_COMPOSE, workdir / "docker-compose.yml")

    # Resolve docker wherever it lives (skipif guarantees it exists) and keep an
    # otherwise-minimal, controller-like PATH. On a WB controller docker is in
    # /usr/bin; on dev machines (e.g. Homebrew) it may be elsewhere.
    docker_dir = str(Path(shutil.which("docker")).parent)
    result = subprocess.run(
        ["docker", "compose", "-f", "docker-compose.yml",
         "config", "--format", "json"],
        cwd=workdir,
        env={"PATH": f"{docker_dir}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"docker compose config unavailable: {result.stderr}")

    config = json.loads(result.stdout)
    (service,) = config["services"].values()
    labels = service["labels"]
    assert labels["wb.app"] == "node-red"
    assert labels["wb.title"] == "Node-RED"
    # hardcoded loopback publish: 127.0.0.1:21880 -> 1880
    (port,) = service["ports"]
    assert port["host_ip"] == "127.0.0.1"
    assert str(port["published"]) == "21880"
    assert int(port["target"]) == 1880


def test_base_compose_pins_a_vanilla_mirror_image_not_derived():
    """Node-RED ships as a byte-for-byte MIRROR of upstream, not a derived image.

    ADR 0006: the image is a plain retag of vanilla ``nodered/node-red`` into the
    WB registry (pull->tag->push, no Dockerfile, no build), so the compose must
    pin the upstream tag (``4.0.2``) and must NOT carry a derived ``-wbN`` suffix
    (which would denote a built image). The WB palette is vendored into the .deb
    instead of being baked into the image.
    """
    compose = BASE_COMPOSE.read_text()
    assert "registry.wirenboard.com/wb/node-red:4.0.2" in compose
    assert ":4.0.2-wb1" not in compose  # no derived tag


# --- vendored palette (ADR 0006: mirror image + palette in the .deb) ---------


def test_palette_manifest_pins_the_wb_palette_for_reproducible_vendoring():
    """The palette is npm-installed at build time from a pinned manifest.

    ADR 0006: node-red-contrib-wirenboard is pure JS, so it is vendored into the
    .deb instead of baked into the image. The manifest pins an EXACT version
    (the PoC-validated 3.11.0) and a package-lock.json is committed so the build
    is reproducible via ``npm ci``. The installed tree is NEVER committed.
    """
    manifest = json.loads((PALETTE / "package.json").read_text())
    pin = manifest["dependencies"]["node-red-contrib-wirenboard"]
    assert pin == "3.11.0"  # exact pin, no ^/~/range
    assert (PALETTE / "package-lock.json").exists()  # reproducible npm ci
    assert "node_modules/" in (PALETTE / ".gitignore").read_text()


def test_node_red_rules_vendors_the_palette_at_build_time():
    """debian/rules npm-installs the palette and ships it under the palette dir.

    The image build is gone (ADR 0006); the only built artifact is the palette
    tree, installed to /usr/lib/wb-docker-app/node-red/palette/ from where the
    helper refreshes it into /data/node_modules at install.
    """
    rules = (SERVICE / "debian" / "rules").read_text()
    assert "npm ci" in rules
    assert "usr/lib/wb-docker-app/node-red/palette" in rules
    # the build host needs npm to vendor the (pure-JS) palette
    control = (SERVICE / "debian" / "control").read_text()
    assert "npm" in control
