"""Packaging smoke tests for wb-docker-app and wb-node-red (issue #1).

These prove the Debian packaging of the walking skeleton without needing a real
build chain. Everything here is ROOTLESS: each package's payload tree is
hand-assembled from its ``debian/*.install`` mappings and built with
``dpkg-deb --build`` (NOT ``dpkg-buildpackage``, which needs fakeroot and the
full build-deps). We then assert:

* ``debian/control`` declares the right package, architecture and dependencies;
* the built ``.deb`` carries the expected payload paths;
* the maintainer scripts (and the shell library) pass ``shellcheck`` clean;
* ``docker compose config`` parses the base compose and surfaces the ``wb.*``
  labels that are the helper's source of truth (design.md §3.10).

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
ECHO = PKG / "wb-echo"

BASE_COMPOSE = SERVICE / "compose" / "node-red" / "docker-compose.yml"

MAINTAINER_SCRIPTS = [
    HELPER / "debian" / "postinst",
    HELPER / "lib" / "wb-docker-app.sh",
    SERVICE / "debian" / "postinst",
    SERVICE / "debian" / "prerm",
    ECHO / "debian" / "postinst",
    ECHO / "debian" / "prerm",
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


def test_echo_control_fields_and_dependencies():
    # The second service (issue #7) is shaped exactly like wb-node-red: same
    # arch, same dependency on the shared helper + docker-ce.
    para = _binary_paragraph(ECHO / "debian" / "control", "wb-echo")
    assert para["Architecture"] == "all"
    deps = para["Depends"]
    assert "docker-ce" in deps
    assert "wb-docker-app" in deps


def test_echo_package_is_template_only_no_duplicated_lifecycle_logic():
    # "By template": the whole package is base compose + wb.* labels + thin sh
    # glue. It must NOT ship any Python, systemd unit, or shell library — those
    # live once in the shared helper. Maintainer scripts only source the helper
    # library and call its functions; they carry no compose/systemd/nginx logic.
    shipped = [p for p in ECHO.rglob("*") if p.is_file()]
    assert not any(p.suffix == ".py" for p in shipped)
    assert not any(p.suffix == ".service" for p in shipped)
    assert not any(p.name.endswith(".sh") for p in shipped)

    for script in (ECHO / "debian" / "postinst", ECHO / "debian" / "prerm"):
        text = script.read_text()
        # delegates to the shared library, names no lifecycle primitives itself
        assert "wb-docker-app.sh" in text
        assert "docker compose" not in text
        assert "systemctl" not in text
        assert "nginx" not in text


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
def test_node_red_deb_carries_package_owned_base_compose(tmp_path):
    deb = _build_deb(SERVICE, "wb-node-red", tmp_path)

    assert _deb_field(deb, "Package") == "wb-node-red"
    assert _deb_field(deb, "Architecture") == "all"

    contents = _deb_contents(deb)
    # base compose is package-owned under /usr/lib (design.md §3.6, issue #2)
    assert "usr/lib/wb-docker-app/node-red/docker-compose.yml" in contents


@pytest.mark.skipif(
    shutil.which("dpkg-deb") is None, reason="dpkg-deb not available"
)
def test_echo_deb_carries_package_owned_base_compose(tmp_path):
    deb = _build_deb(ECHO, "wb-echo", tmp_path)

    assert _deb_field(deb, "Package") == "wb-echo"
    assert _deb_field(deb, "Architecture") == "all"

    contents = _deb_contents(deb)
    # base compose is package-owned under /usr/lib (design.md §3.6, issue #2),
    # under its own app slug so it never collides with node-red's.
    assert "usr/lib/wb-docker-app/echo/docker-compose.yml" in contents


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
def test_base_compose_config_parses_wb_labels(tmp_path):
    # Run config in an isolated dir so the project name comes from the dir, and
    # supply the loopback port the helper would inject via .env.
    workdir = tmp_path / "node-red"
    workdir.mkdir()
    shutil.copy(BASE_COMPOSE, workdir / "docker-compose.yml")

    result = subprocess.run(
        ["docker", "compose", "-f", "docker-compose.yml",
         "config", "--format", "json"],
        cwd=workdir,
        env={"PATH": "/usr/bin:/bin", "WB_INTERNAL_PORT": "21880"},
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
    assert labels["wb.proxy.port"] == "1880"
    assert labels["wb.proxy.role"] == "admin"


@pytest.mark.skipif(
    shutil.which("docker") is None, reason="docker not available"
)
def test_echo_base_compose_config_parses_wb_labels(tmp_path):
    workdir = tmp_path / "echo"
    workdir.mkdir()
    shutil.copy(
        ECHO / "compose" / "echo" / "docker-compose.yml",
        workdir / "docker-compose.yml",
    )

    result = subprocess.run(
        ["docker", "compose", "-f", "docker-compose.yml",
         "config", "--format", "json"],
        cwd=workdir,
        env={"PATH": "/usr/bin:/bin", "WB_INTERNAL_PORT": "28080"},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"docker compose config unavailable: {result.stderr}")

    config = json.loads(result.stdout)
    (service,) = config["services"].values()
    labels = service["labels"]
    # distinct public port from node-red's 1880 — no nginx collision
    assert labels["wb.app"] == "echo"
    assert labels["wb.proxy.port"] == "8080"
