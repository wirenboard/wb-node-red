"""Поведенческие тесты debian/postinst: настоящий скрипт запускается через sh
против одноразового каталога-корня (шов WB_NODE_RED_ROOT), системные команды
подменены заглушками на PATH.

Зачем: postinst — самая рискованная часть пакета (замена рантайма при
обновлении, сохранность пользовательских flows, откат при битом архиве),
и это единственный способ прогнать его логику на каждой сборке, без
контроллера и без установки пакета.
"""

import os
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
POSTINST = REPO / "debian" / "postinst"

# STUB_* env knobs let each test drive a branch without editing the stubs
SHELL_STUBS = {
    "mountpoint": '#!/bin/sh\nexit "${STUB_MOUNTPOINT_RC:-0}"\n',
    "systemd-sysusers": '#!/bin/sh\necho "systemd-sysusers $*" >> "$STUB_LOG"\nexit 0\n',
    "chown": "#!/bin/sh\nexit 0\n",  # the sandbox has no wb-node-red user
    "deb-systemd-invoke": '#!/bin/sh\necho "deb-systemd-invoke $*" >> "$STUB_LOG"\nexit 0\n',
    "wb-homeui-gates": '#!/bin/sh\necho "wb-homeui-gates $*" >> "$STUB_LOG"\nexit 0\n',
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

    def place_old_runtime(self, marker: str):
        red = self.runtime / "node_modules/node-red"
        red.mkdir(parents=True)
        (red / "red.js").write_text(marker)

    def runtime_marker(self) -> str:
        return (self.runtime / "node_modules/node-red/red.js").read_text()

    def run(self, mountpoint_rc=0, action="configure"):
        env = dict(
            os.environ,
            WB_NODE_RED_ROOT=str(self.root),
            PATH=f"{self.stub_bin}:/usr/bin:/bin",
            STUB_LOG=str(self.log),
            STUB_MOUNTPOINT_RC=str(mountpoint_rc),
        )
        return subprocess.run(
            ["sh", str(POSTINST), action], env=env, capture_output=True, text=True)

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


def test_unmounted_mnt_data_aborts_before_touching_anything(sb):
    res = sb.run(mountpoint_rc=1)
    assert res.returncode == 1
    assert not sb.runtime.exists()


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
