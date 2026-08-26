"""Behavior tests for debian/postinst and debian/postrm: the real script runs via sh against a
throwaway root directory (the WB_NODE_RED_ROOT seam), with system commands
replaced by stubs on PATH.

Why: postinst is the riskiest part of the package (runtime swap on upgrade,
user-flows preservation, rollback on a corrupt archive), and this is the only
way to exercise its logic on every build — without a controller and without
installing the package.
"""

import os
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
POSTINST = REPO / "debian" / "postinst"
POSTRM = REPO / "debian" / "postrm"

# STUB_* env knobs let each test drive a branch without editing the stubs
SHELL_STUBS = {
    "mountpoint": '#!/bin/sh\nexit "${STUB_MOUNTPOINT_RC:-0}"\n',
    "systemd-sysusers": '#!/bin/sh\necho "systemd-sysusers $*" >> "$STUB_LOG"\nexit 0\n',
    "chown": "#!/bin/sh\nexit 0\n",  # the sandbox has no wb-node-red user
    "deb-systemd-invoke": '#!/bin/sh\necho "deb-systemd-invoke $*" >> "$STUB_LOG"\nexit 0\n',
    "wb-homeui-gates": '#!/bin/sh\necho "wb-homeui-gates $*" >> "$STUB_LOG"\nexit 0\n',
    # port-guard knobs: empty STUB_SS_OUT = nothing listens on :1880,
    # systemctl rc 3 = our service inactive (systemd's "inactive" exit code)
    "ss": '#!/bin/sh\n[ -n "${STUB_SS_OUT:-}" ] && echo "$STUB_SS_OUT"\nexit 0\n',
    "systemctl": '#!/bin/sh\nexit "${STUB_SYSTEMCTL_RC:-3}"\n',
}


class Sandbox:
    def __init__(self, tmp_path: Path):
        self.root = tmp_path / "root"
        self.log = tmp_path / "stub.log"
        self.log.touch()

        self.stub_bin = tmp_path / "bin"
        self.stub_bin.mkdir()
        for name, body in SHELL_STUBS.items():
            self._write_stub(name, body)

        # the fake controller filesystem
        self.data = self.root / "mnt/data/wb-node-red"
        self.runtime = self.root / "mnt/data/wb-node-red-runtime"
        self.share = self.root / "usr/share/wb-node-red"
        for d in (self.root / "mnt/data", self.share):
            d.mkdir(parents=True)

        def payload(rel: str) -> str:
            src = REPO / rel
            return src.read_text() if src.exists() else f"# placeholder for {rel}\n"

        (self.share / "flows.json").write_text(payload("config/flows.json"))
        self.make_tarball("shipped")

    def _write_stub(self, name: str, body: str):
        p = self.stub_bin / name
        p.write_text(body)
        p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def drop_gate_cli(self):
        """Simulate homeui not installed: the gate CLI is absent from PATH."""
        (self.stub_bin / "wb-homeui-gates").unlink()

    def make_tarball(self, marker: str):
        """node_modules.tar.gz with node_modules/node-red/red.js == marker."""
        tree = self.share / "_tree"
        red = tree / "node_modules/node-red"
        red.mkdir(parents=True, exist_ok=True)
        (red / "red.js").write_text(marker)
        with tarfile.open(self.share / "node_modules.tar.gz", "w:gz") as tf:
            tf.add(tree / "node_modules", arcname="node_modules")

    def corrupt_tarball(self):
        (self.share / "node_modules.tar.gz").write_bytes(b"not a tarball")

    def place_data_partition_label(self):
        """Standard layout: udev exposes the data partition as by-label/data."""
        label = self.root / "dev/disk/by-label"
        label.mkdir(parents=True)
        (label / "data").touch()

    def place_old_runtime(self, marker: str):
        red = self.runtime / "node_modules/node-red"
        red.mkdir(parents=True)
        (red / "red.js").write_text(marker)

    def runtime_marker(self) -> str:
        return (self.runtime / "node_modules/node-red/red.js").read_text()

    def run(self, mountpoint_rc=0, action="configure", ss_out="", systemctl_rc=3):
        env = dict(
            os.environ,
            WB_NODE_RED_ROOT=str(self.root),
            PATH=f"{self.stub_bin}:/usr/bin:/bin",
            STUB_LOG=str(self.log),
            STUB_MOUNTPOINT_RC=str(mountpoint_rc),
            STUB_SS_OUT=ss_out,
            STUB_SYSTEMCTL_RC=str(systemctl_rc),
        )
        return subprocess.run(
            ["sh", str(POSTINST), action], env=env, capture_output=True, text=True)

    def run_postrm(self, action="remove"):
        env = dict(
            os.environ,
            WB_NODE_RED_ROOT=str(self.root),
            PATH=f"{self.stub_bin}:/usr/bin:/bin",
            STUB_LOG=str(self.log),
        )
        return subprocess.run(
            ["sh", str(POSTRM), action], env=env, capture_output=True, text=True)

    def log_text(self) -> str:
        return self.log.read_text()


@pytest.fixture
def sb(tmp_path):
    return Sandbox(tmp_path)


# --- runtime swap ------------------------------------------------------------

def test_fresh_install_extracts_runtime_seeds_flows_and_applies_gate(sb):
    res = sb.run()
    assert res.returncode == 0, res.stderr
    assert sb.runtime_marker() == "shipped"
    assert (sb.data / "flows.json").read_text() == (
        sb.share / "flows.json").read_text()
    assert "wb-homeui-gates apply" in sb.log_text()
    # the service user must exist before the chown; postinst can't wait for
    # the debhelper-generated systemd-sysusers call at the end of the script
    assert "systemd-sysusers" in sb.log_text()


def test_upgrade_replaces_runtime_and_preserves_user_flows(sb):
    sb.place_old_runtime("previous")
    sb.data.mkdir(parents=True)
    (sb.data / "flows.json").write_text("MY FLOWS")
    res = sb.run()
    assert res.returncode == 0, res.stderr
    assert sb.runtime_marker() == "shipped"
    assert (sb.data / "flows.json").read_text() == "MY FLOWS"
    # no swap debris survives a successful run
    assert not (sb.runtime / "node_modules.old").exists()
    assert not (sb.runtime / "node_modules.staging").exists()


def test_corrupt_tarball_keeps_old_runtime_and_restarts_the_service(sb):
    sb.place_old_runtime("previous")
    sb.corrupt_tarball()
    res = sb.run()
    assert res.returncode != 0
    assert sb.runtime_marker() == "previous"
    # the EXIT trap restarted the service on the intact old runtime
    assert "deb-systemd-invoke start wb-node-red.service" in sb.log_text()


def test_stale_staging_and_old_dirs_are_cleaned_up(sb):
    # debris from a previously interrupted run must not break the next one
    (sb.runtime / "node_modules.staging/junk").mkdir(parents=True)
    (sb.runtime / "node_modules.old/junk").mkdir(parents=True)
    sb.place_old_runtime("previous")
    res = sb.run()
    assert res.returncode == 0, res.stderr
    assert sb.runtime_marker() == "shipped"
    assert not (sb.runtime / "node_modules.old").exists()
    assert not (sb.runtime / "node_modules.staging").exists()


def test_unmounted_data_partition_aborts_before_touching_anything(sb):
    # standard layout: the data partition exists but is not mounted — broken state
    sb.place_data_partition_label()
    res = sb.run(mountpoint_rc=1)
    assert res.returncode == 1
    assert not sb.runtime.exists()


def test_extended_rootfs_plain_mnt_data_dir_installs(sb):
    # extended rootfs: no data partition at all, /mnt/data is a dir on the big root
    # (util-linux 2.41: rc 32 = "is not a mountpoint" for an existing directory)
    res = sb.run(mountpoint_rc=32)
    assert res.returncode == 0, res.stderr
    assert sb.runtime_marker() == "shipped"


# --- port-1880 guard ----------------------------------------------------------

FOREIGN_LISTENER = "LISTEN 0 511 *:1880 *:*"


def test_foreign_listener_on_1880_aborts_before_touching_anything(sb):
    res = sb.run(ss_out=FOREIGN_LISTENER)
    assert res.returncode == 1
    assert "1880" in res.stderr
    assert not sb.runtime.exists()


def test_upgrade_proceeds_when_own_service_holds_the_port(sb):
    sb.place_old_runtime("previous")
    res = sb.run(ss_out=FOREIGN_LISTENER, systemctl_rc=0)
    assert res.returncode == 0, res.stderr
    assert sb.runtime_marker() == "shipped"


def test_free_port_and_inactive_service_pass_the_guard(sb):
    res = sb.run(ss_out="", systemctl_rc=3)
    assert res.returncode == 0, res.stderr


# --- old-install hint ----------------------------------------------------------

def test_first_install_hints_at_old_data_but_never_adopts_it(sb):
    old = sb.root / "root/.node-red"
    old.mkdir(parents=True)
    (old / "flows.json").write_text("OLD FLOWS")
    res = sb.run()
    assert res.returncode == 0, res.stderr
    assert "/root/.node-red" in res.stdout
    assert "wiki" in res.stdout
    assert (sb.data / "flows.json").read_text() == (
        sb.share / "flows.json").read_text(), "must seed the default, not adopt"


def test_no_hint_once_userdir_is_populated(sb):
    (sb.root / "mnt/data/root/nodered").mkdir(parents=True)
    sb.data.mkdir(parents=True)
    (sb.data / "flows.json").write_text("MY FLOWS")
    res = sb.run()
    assert res.returncode == 0, res.stderr
    assert "old Node-RED" not in res.stdout
    assert (sb.data / "flows.json").read_text() == "MY FLOWS"


# --- seed symlink guard ------------------------------------------------------

def test_seed_never_writes_through_a_planted_dangling_symlink(sb):
    # `-e` alone is false for a dangling link — cp would write through it as root
    sb.data.mkdir(parents=True)
    target = sb.root / "etc/pwned"
    (sb.data / "flows.json").symlink_to(target)
    res = sb.run()
    assert res.returncode == 0, res.stderr
    assert not target.exists(), "postinst wrote through a planted symlink as root"


# --- gate application is best-effort ----------------------------------------

def test_configure_succeeds_when_homeui_gate_cli_is_absent(sb):
    # homeui not installed yet: the drop-in sits inert, homeui applies it later.
    sb.drop_gate_cli()
    res = sb.run()
    assert res.returncode == 0, res.stderr
    assert sb.runtime_marker() == "shipped"
    assert "wb-homeui-gates" not in sb.log_text()


# --- removal (postrm) --------------------------------------------------------

def test_postrm_remove_drops_the_runtime_but_keeps_user_data(sb):
    sb.place_old_runtime("previous")
    sb.data.mkdir(parents=True, exist_ok=True)
    (sb.data / "flows.json").write_text("MY FLOWS")

    res = sb.run_postrm("remove")

    assert res.returncode == 0, res.stderr
    assert not sb.runtime.exists()
    assert (sb.data / "flows.json").read_text() == "MY FLOWS"
    assert "wb-homeui-gates apply" in sb.log_text()


def test_postrm_leaves_the_gate_declaration_alone(sb):
    """dpkg unlinks the package's files before postrm (Policy 6.8); removing the
    gate by hand is what broke reinstall."""
    gate = sb.root / "usr/share/wb-mqtt-homeui/gates.d/node-red.json"
    gate.parent.mkdir(parents=True)
    gate.write_text('{"internalPort": 1880}')

    res = sb.run_postrm("purge")

    assert res.returncode == 0, res.stderr
    assert gate.exists()


def test_postrm_upgrade_keeps_the_runtime(sb):
    """postrm also runs with "upgrade": wiping the runtime there would delete
    the tree just unpacked."""
    sb.place_old_runtime("previous")

    res = sb.run_postrm("upgrade")

    assert res.returncode == 0, res.stderr
    assert sb.runtime_marker() == "previous"
    assert "wb-homeui-gates apply" not in sb.log_text()


def test_postrm_survives_a_missing_gate_cli(sb):
    """homeui may already be gone when we are removed."""
    sb.place_old_runtime("previous")
    sb.drop_gate_cli()

    res = sb.run_postrm("remove")

    assert res.returncode == 0, res.stderr
    assert not sb.runtime.exists()
