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
  paths;
* the postinst/prerm hooks that *register* the collector command into
  wb-diag-collect's single main config — the released tool has no conf.d merge,
  so the drop-in alone is inert (the merge itself is unit-tested in
  tests/test_cli.py against wb_docker_app.diag).

Everything here is local file presence/shape/shellcheck — running
unattended-upgrades and wb-diag-collect end-to-end on a controller is still a
manual-verification item. YAML shape is asserted only when PyYAML is present,
so the suite stays green without it.
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
ECHO = REPO / "packaging" / "wb-echo"

APT_CONF = HELPER / "apt" / "52wb-docker-app-no-unattended"
DIAG_DROPIN = HELPER / "diag" / "60wb-docker-app.conf"
DIAG_SCRIPT = HELPER / "diag" / "wb-docker-app-diag-collect"
INSTALL = HELPER / "debian" / "wb-docker-app.install"

ECHO_APT_CONF = ECHO / "apt" / "52wb-echo-no-unattended"
ECHO_INSTALL = ECHO / "debian" / "wb-echo.install"


# --- unattended-upgrades exclusion ------------------------------------------


def test_apt_conf_exists():
    assert APT_CONF.is_file()


def test_apt_conf_blacklists_helper_and_service_packages():
    text = APT_CONF.read_text()
    # The directive unattended-upgrades reads to skip packages.
    assert "Unattended-Upgrade::Package-Blacklist" in text
    # The shared helper and the in-tree node-red service package are excluded,
    # each anchored with `$` so they match only themselves.
    assert '"wb-docker-app$"' in text
    assert '"wb-node-red$"' in text


def test_apt_conf_lands_in_apt_conf_d():
    mappings = INSTALL.read_text()
    assert "apt/52wb-docker-app-no-unattended etc/apt/apt.conf.d/" in mappings


def test_echo_ships_its_own_unattended_upgrades_dropin():
    # Per design §3.11.1, a service package whose slug is not already covered by
    # an end-anchored helper entry ships its own apt.conf.d drop-in. wb-echo
    # (matched by neither "wb-docker-app$" nor "wb-node-red$") must carry one,
    # and it must land in etc/apt/apt.conf.d/ so unattended-upgrades reads it.
    dropin = ECHO_APT_CONF
    assert dropin.is_file()
    text = dropin.read_text()
    assert "Unattended-Upgrade::Package-Blacklist" in text
    assert '"wb-echo$"' in text
    mappings = ECHO_INSTALL.read_text()
    assert "apt/52wb-echo-no-unattended etc/apt/apt.conf.d/" in mappings


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


def test_postinst_registers_collector_in_main_config():
    # The released wb-diag-collect has no conf.d merge, so the drop-in alone is
    # inert; the helper's postinst must actively register the collector command
    # into wb-diag-collect's main config for the AC to hold on a controller.
    text = (HELPER / "debian" / "postinst").read_text()
    assert "wb-docker-app register-diag" in text


def test_prerm_deregisters_collector_from_main_config():
    # On removal the helper must undo that registration so it leaves
    # wb-diag-collect's config as it found it.
    prerm = HELPER / "debian" / "prerm"
    assert prerm.is_file()
    text = prerm.read_text()
    assert "wb-docker-app deregister-diag" in text
    # Only on remove, not on upgrade (the entry must survive upgrades).
    assert "upgrade" in text


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


def test_collector_discovers_apps_via_valid_docker_format_template(tmp_path):
    # Regression for the broken `--format '{{ index .Labels "wb.app" }}'`
    # template: in `docker ps --format` Go context `.Labels` is a
    # comma-separated STRING, so `index .Labels ...` fails template execution
    # and discovery silently came back empty. Stub a `docker` that mimics that
    # Go-template semantics — it errors on the `index .Labels` form and only
    # emits app names for the correct `.Label "wb.app"` placeholder the CLI
    # uses — and assert the collector actually surfaces the running app.
    if shutil.which("sh") is None:
        pytest.skip("no POSIX sh")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    docker = bindir / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        "# Find the --format argument value.\n"
        "fmt=\"\"\n"
        "while [ $# -gt 0 ]; do\n"
        "  case \"$1\" in\n"
        "    --format) fmt=\"$2\"; shift 2;;\n"
        "    *) shift;;\n"
        "  esac\n"
        "done\n"
        "case \"$fmt\" in\n"
        "  *'index .Labels'*)\n"
        "    echo 'failed to execute template: error calling index:"
        " cannot index slice/array with type string' >&2\n"
        "    exit 1;;\n"
        "  *'.Label \"wb.app\"'*)\n"
        "    echo node-red\n"
        "    exit 0;;\n"
        "  *) exit 0;;\n"
        "esac\n"
    )
    docker.chmod(0o755)
    # Keep the real system bins (sort/sed/awk) on PATH but shadow docker with
    # our stub by putting bindir first; drop any real docker/systemctl so only
    # the stub answers and systemd discovery stays empty.
    syspath = ":".join(p for p in os.environ.get("PATH", "").split(":") if p)
    env = {"PATH": f"{bindir}:{syspath}"}
    result = subprocess.run(
        ["/bin/sh", str(DIAG_SCRIPT)],
        capture_output=True,
        text=True,
        env={**os.environ, **env},
    )
    assert result.returncode == 0
    assert "node-red" in result.stdout
    assert "(no running wb-docker-app containers" not in result.stdout
