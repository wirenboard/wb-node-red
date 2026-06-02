"""Packaging tails for day-2 lifecycle & observability (issue #5).

The CLI verbs (status/logs/restart/update/list) live in cli.py and are covered
by tests/test_cli.py. This module pins the *packaging* tails the helper ships
so a containerized service behaves like a native WB service and upgrades happen
only on purpose:

* an apt.conf.d drop-in that EXCLUDES wb-docker-app and the wb-* service
  packages from unattended-upgrades;
* a wb-diag-collect drop-in + collector script that put each service's
  ``systemctl status`` and recent logs into the diagnostic archive;
* the helper's ``debian/*.install`` actually maps all three to their on-target
  paths.

Everything here is local file presence/shape/shellcheck — the real
unattended-upgrades and wb-diag-collect/collect behaviour on a controller is a
manual-verification item (see the issue-5 audit). YAML shape is asserted only
when PyYAML is present, so the suite stays green without it.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HELPER = REPO / "packaging" / "wb-docker-app"

APT_CONF = HELPER / "apt" / "52wb-docker-app-no-unattended"
DIAG_DROPIN = HELPER / "diag" / "60wb-docker-app.conf"
DIAG_SCRIPT = HELPER / "diag" / "wb-docker-app-diag-collect"
INSTALL = HELPER / "debian" / "wb-docker-app.install"


# --- unattended-upgrades exclusion ------------------------------------------


def test_apt_conf_exists():
    assert APT_CONF.is_file()


def test_apt_conf_blacklists_helper_and_service_packages():
    text = APT_CONF.read_text()
    # The directive unattended-upgrades reads to skip packages.
    assert "Unattended-Upgrade::Package-Blacklist" in text
    # The shared helper and the node-red service package are excluded, each
    # anchored with `$` so they match only themselves.
    assert '"wb-docker-app$"' in text
    assert '"wb-node-red$"' in text


def test_apt_conf_lands_in_apt_conf_d():
    mappings = INSTALL.read_text()
    assert "apt/52wb-docker-app-no-unattended etc/apt/apt.conf.d/" in mappings


# --- wb-diag-collect drop-in + collector ------------------------------------


def test_diag_dropin_and_script_exist():
    assert DIAG_DROPIN.is_file()
    assert DIAG_SCRIPT.is_file()


def test_diag_collector_is_executable():
    mode = DIAG_SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR, "collector must be executable"


def test_diag_dropin_invokes_the_collector_and_captures_journals():
    text = DIAG_DROPIN.read_text()
    # The drop-in mirrors the wb-diag-collect schema: a commands entry whose
    # stdout is archived, pointing at the installed collector path.
    assert "commands:" in text
    assert "/usr/lib/wb-docker-app/diag/wb-docker-app-diag-collect" in text
    # Service journals are picked up by the instance glob.
    assert "wb-docker-app@*.service" in text


def test_diag_collector_captures_status_and_logs_per_instance():
    text = DIAG_SCRIPT.read_text()
    # Discovers apps from the same self-describing label source as the CLI.
    assert "label=wb.app" in text
    # Captures both systemctl status and recent journal logs per instance.
    assert "systemctl status" in text
    assert "journalctl" in text


def test_diag_files_land_in_their_target_paths():
    mappings = INSTALL.read_text()
    assert ("diag/wb-docker-app-diag-collect usr/lib/wb-docker-app/diag/"
            in mappings)
    assert ("diag/60wb-docker-app.conf usr/share/wb-diag-collect/conf.d/"
            in mappings)


@pytest.mark.skipif(
    shutil.which("shellcheck") is None, reason="shellcheck not available"
)
def test_diag_collector_is_shellcheck_clean():
    result = subprocess.run(
        ["shellcheck", str(DIAG_SCRIPT)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr


# --- YAML shape of the drop-in (only when PyYAML is present) -----------------


def _yaml_or_skip():
    yaml = pytest.importorskip("yaml")
    return yaml


def test_diag_dropin_is_valid_yaml_with_the_expected_schema():
    yaml = _yaml_or_skip()
    data = yaml.safe_load(DIAG_DROPIN.read_text())
    assert isinstance(data, dict)
    # commands: list of {filename, command}; the collector is one of them.
    cmds = data["commands"]
    assert any(
        c.get("command", "").strip().endswith("wb-docker-app-diag-collect")
        and c.get("filename")
        for c in cmds
    )
    # journald_logs.names includes our instance glob.
    assert "wb-docker-app@*.service" in data["journald_logs"]["names"]


# --- the collector actually runs (no docker/systemd needed) ------------------


def test_collector_runs_and_reports_no_apps_when_docker_is_absent(tmp_path):
    # Run the collector with an empty PATH (so docker/systemctl/journalctl are
    # all "unavailable"): it must still exit 0 and produce its section header,
    # i.e. it never fails the diag collect.
    if shutil.which("sh") is None:
        pytest.skip("no POSIX sh")
    env = {"PATH": ""}
    result = subprocess.run(
        ["/bin/sh", str(DIAG_SCRIPT)],
        capture_output=True,
        text=True,
        env={**os.environ, **env},
    )
    assert result.returncode == 0
    assert "installed services" in result.stdout
