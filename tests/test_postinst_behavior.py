"""Behavior tests for debian/postinst.

Each test runs the real `sh debian/postinst configure` against a throwaway
root (re-rooted via the WB_NODE_RED_ROOT seam) with the system commands
stubbed on PATH, then asserts on the resulting filesystem state — the swap's
crash-safety, the homeui/cert gating matrix, the nginx -t restore path and the
menu<->gate coupling, none of which a string-grep test can pin.
"""

import os
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
POSTINST = REPO / "debian" / "postinst"

HOMEUI_OK = "2.235.4"
HOMEUI_OLD = "2.226.1"

# dpkg is stubbed with just enough of the real thing: `--compare-versions A op B`
# with Debian ordering (incl. `~` sorting before everything, even end-of-string).
DPKG_STUB = """#!/usr/bin/env python3
import sys


def _order(c):
    if c == "~":
        return -1
    if c.isdigit():
        return 0
    if c.isalpha():
        return ord(c)
    return ord(c) + 256


def _verrevcmp(a, b):
    ia = ib = 0
    while ia < len(a) or ib < len(b):
        first_diff = 0
        while (ia < len(a) and not a[ia].isdigit()) or (
            ib < len(b) and not b[ib].isdigit()
        ):
            ac = _order(a[ia]) if ia < len(a) else 0
            bc = _order(b[ib]) if ib < len(b) else 0
            if ac != bc:
                return ac - bc
            ia += 1
            ib += 1
        while ia < len(a) and a[ia] == "0":
            ia += 1
        while ib < len(b) and b[ib] == "0":
            ib += 1
        while ia < len(a) and a[ia].isdigit() and ib < len(b) and b[ib].isdigit():
            if not first_diff:
                first_diff = ord(a[ia]) - ord(b[ib])
            ia += 1
            ib += 1
        if ia < len(a) and a[ia].isdigit():
            return 1
        if ib < len(b) and b[ib].isdigit():
            return -1
        if first_diff:
            return first_diff
    return 0


assert sys.argv[1] == "--compare-versions", sys.argv
a, op, b = sys.argv[2], sys.argv[3], sys.argv[4]
r = _verrevcmp(a, b)
ok = {"lt": r < 0, "le": r <= 0, "eq": r == 0, "ge": r >= 0, "gt": r > 0}[op]
sys.exit(0 if ok else 1)
"""

SHELL_STUBS = {
    # env knobs let each test drive a branch without editing the stubs.
    "mountpoint": '#!/bin/sh\nexit "${STUB_MOUNTPOINT_RC:-0}"\n',
    "getent": "#!/bin/sh\nexit 0\n",  # service user "already exists"
    "adduser": "#!/bin/sh\nexit 0\n",
    "chown": "#!/bin/sh\nexit 0\n",  # the sandbox has no wb-node-red user
    "deb-systemd-invoke": '#!/bin/sh\necho "deb-systemd-invoke $*" >> "$STUB_LOG"\nexit 0\n',
    "invoke-rc.d": '#!/bin/sh\necho "invoke-rc.d $*" >> "$STUB_LOG"\nexit 0\n',
    "systemctl": (
        "#!/bin/sh\n"
        'echo "systemctl $*" >> "$STUB_LOG"\n'
        # is-active drives the foreign-port-1880 check: 0 = our unit is active
        # (so any 1880 listener is ours), non-zero = stopped/failed.
        'if [ "$1" = "is-active" ]; then exit "${STUB_UNIT_ACTIVE_RC:-0}"; fi\n'
        "exit 0\n"
    ),
    "ss": '#!/bin/sh\nprintf \'%s\\n\' "${STUB_SS_OUTPUT:-}"\n',
    "dpkg-query": (
        "#!/bin/sh\n"
        'if [ -n "$STUB_HOMEUI_VER" ]; then printf \'%s\' "$STUB_HOMEUI_VER"; exit 0; fi\n'
        "exit 1\n"
    ),
    "nginx": (
        "#!/bin/sh\n"
        'echo "nginx $*" >> "$STUB_LOG"\n'
        'if [ "$1" = "-t" ]; then exit "${STUB_NGINX_T_RC:-0}"; fi\n'
        "exit 0\n"
    ),
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
        self._write_stub("dpkg", DPKG_STUB)

        # the fake controller filesystem
        self.data = self.root / "mnt/data/wb-node-red"
        self.runtime = self.root / "mnt/data/wb-node-red-runtime"
        self.confd = self.root / "etc/nginx/conf.d"
        self.wb_d = self.root / "etc/nginx/includes/default.wb.d"
        self.share = self.root / "usr/share/wb-node-red"
        self.menu_dir = self.root / "usr/share/wb-mqtt-homeui/custom-menu"
        for d in (self.root / "mnt/data", self.confd, self.wb_d,
                  self.root / "etc/ssl", self.share / "nginx", self.menu_dir):
            d.mkdir(parents=True)

        # Package payload, as the build would install it. postinst only copies
        # these files, so their content is irrelevant here — and the nginx confs
        # and config seeds live in later PRs of the stack; fall back to
        # placeholders when a payload file is not in this tree yet.
        def payload(rel: str) -> str:
            src = REPO / rel
            return src.read_text() if src.exists() else f"# placeholder for {rel}\n"

        for conf in ("wb-node-red.conf", "wb-node-red-auth-cache.conf",
                     "wb-node-red-redirect.conf"):
            (self.share / "nginx" / conf).write_text(payload(f"nginx/{conf}"))
        (self.share / "custom-menu.json").write_text(
            payload("config/custom-menu.json"))
        (self.share / "flows.json").write_text(payload("config/flows.json"))
        self.write_cert()
        self.make_tarball("shipped")

    def _write_stub(self, name: str, body: str):
        p = self.stub_bin / name
        p.write_text(body)
        p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def write_cert(self):
        (self.root / "etc/ssl/sslip.pem").write_text("cert")
        (self.root / "etc/ssl/sslip.key").write_text("key")

    def drop_cert(self):
        (self.root / "etc/ssl/sslip.pem").unlink()

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

    # gate file handles
    @property
    def gate(self):
        return self.confd / "wb-node-red.conf"

    @property
    def cache(self):
        return self.confd / "wb-node-red-auth-cache.conf"

    @property
    def redirect(self):
        return self.wb_d / "wb-node-red-redirect.conf"

    @property
    def menu(self):
        return self.menu_dir / "wb-node-red.json"

    def run(self, homeui_ver=HOMEUI_OK, nginx_t_rc=0, mountpoint_rc=0,
            action="configure", ss_output="", unit_active_rc=0):
        env = dict(
            os.environ,
            WB_NODE_RED_ROOT=str(self.root),
            PATH=f"{self.stub_bin}:{os.environ['PATH']}",
            STUB_LOG=str(self.log),
            STUB_NGINX_T_RC=str(nginx_t_rc),
            STUB_MOUNTPOINT_RC=str(mountpoint_rc),
            STUB_SS_OUTPUT=ss_output,
            STUB_UNIT_ACTIVE_RC=str(unit_active_rc),
        )
        if homeui_ver is not None:
            env["STUB_HOMEUI_VER"] = homeui_ver
        else:
            env.pop("STUB_HOMEUI_VER", None)
        argv = ["sh", str(POSTINST), action]
        if action == "triggered":
            argv.append("/etc/nginx/snippets")
        return subprocess.run(argv, env=env, capture_output=True, text=True)

    def log_text(self) -> str:
        return self.log.read_text()


@pytest.fixture
def sb(tmp_path):
    return Sandbox(tmp_path)


# --- runtime swap ------------------------------------------------------------

def test_fresh_install_extracts_runtime_seeds_flows_and_wires_gate(sb):
    res = sb.run()
    assert res.returncode == 0, res.stderr
    assert sb.runtime_marker() == "shipped"
    assert (sb.data / "flows.json").read_text() == (
        sb.share / "flows.json").read_text()
    assert sb.gate.exists() and sb.cache.exists() and sb.redirect.exists()
    assert sb.menu.exists()
    assert "reload" in sb.log_text()  # a valid config got reloaded into nginx


def test_upgrade_replaces_runtime_and_preserves_user_flows(sb):
    sb.place_old_runtime("previous")
    (sb.data).mkdir(parents=True)
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
    # the previously working runtime is untouched...
    assert sb.runtime_marker() == "previous"
    # ...and the EXIT trap restarted the service on it (the #DEBHELPER# start
    # never runs when set -e aborts), so the user's flows are not left down.
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
    assert not sb.gate.exists()


# --- seed symlink guard ------------------------------------------------------

def test_seed_never_writes_through_a_planted_dangling_symlink(sb):
    # the service user owns DATA_DIR; a compromised editor can plant this and
    # wait for a root-run configure. `-e` alone is false for a dangling link.
    sb.data.mkdir(parents=True)
    target = sb.root / "etc/pwned"
    (sb.data / "flows.json").symlink_to(target)
    res = sb.run()
    assert res.returncode == 0, res.stderr
    assert not target.exists(), "postinst wrote through a planted symlink as root"


# --- gate install matrix -----------------------------------------------------

@pytest.mark.parametrize("homeui_ver", [None, HOMEUI_OLD])
def test_gate_and_menu_skipped_without_new_enough_homeui(sb, homeui_ver):
    res = sb.run(homeui_ver=homeui_ver)
    assert res.returncode == 0, res.stderr
    assert not sb.gate.exists()
    assert not sb.cache.exists()
    assert not sb.redirect.exists()
    assert not sb.menu.exists()
    assert "dpkg-reconfigure wb-node-red" in res.stderr  # recovery is named


def test_gate_and_menu_skipped_without_sslip_cert(sb):
    sb.drop_cert()
    res = sb.run()
    assert res.returncode == 0, res.stderr
    assert not sb.gate.exists()
    assert not sb.menu.exists()
    assert "dpkg-reconfigure wb-node-red" in res.stderr


def test_stale_menu_entry_is_removed_when_homeui_no_longer_qualifies(sb):
    sb.menu.write_text("stale entry from a previous install")
    res = sb.run(homeui_ver=HOMEUI_OLD)
    assert res.returncode == 0, res.stderr
    assert not sb.menu.exists()


def test_gate_boundary_versions(sb):
    # exactly 2.235.4 qualifies (the ~ in HOMEUI_MIN), one patch below does not
    assert sb.run(homeui_ver="2.235.4").returncode == 0
    assert sb.gate.exists()


# --- nginx -t validation -----------------------------------------------------

def test_failed_nginx_t_on_fresh_install_removes_gate_and_menu(sb):
    res = sb.run(nginx_t_rc=1)
    assert res.returncode == 0, res.stderr
    assert not sb.gate.exists()
    assert not sb.cache.exists()
    assert not sb.redirect.exists()
    # no gate -> no menu entry: never show a dead "Node-RED" link in homeui
    assert not sb.menu.exists()
    assert "reload" not in sb.log_text()  # a broken config is never reloaded


def test_failed_nginx_t_on_upgrade_restores_the_previous_working_gate(sb):
    # a working v(N) gate is installed; the v(N+1) config fails nginx -t
    sb.gate.write_text("previous gate")
    sb.cache.write_text("previous cache")
    sb.redirect.write_text("previous redirect")
    sb.menu.write_text("previous menu")
    res = sb.run(nginx_t_rc=1)
    assert res.returncode == 0, res.stderr
    # the previously working files are RESTORED, not deleted
    assert sb.gate.read_text() == "previous gate"
    assert sb.cache.read_text() == "previous cache"
    assert sb.redirect.read_text() == "previous redirect"
    # the restored gate still serves /node-red, so the menu entry stays
    assert sb.menu.exists()


# --- foreign listener on 1880 ------------------------------------------------

SS_1880 = "LISTEN 0 511   127.0.0.1:1880   0.0.0.0:*"


def test_foreign_1880_listener_skips_gate_instead_of_exposing_it(sb):
    # our unit is stopped (rc=3) yet something holds 1880: a manually installed
    # Node-RED — the gate must NOT proxy :21880 to that foreign instance.
    res = sb.run(ss_output=SS_1880, unit_active_rc=3)
    assert res.returncode == 0, res.stderr
    assert not sb.gate.exists()
    assert not sb.menu.exists()
    assert "1880" in res.stderr and "dpkg-reconfigure wb-node-red" in res.stderr


def test_own_active_service_on_1880_is_not_a_conflict(sb):
    # unit active (rc=0): the 1880 listener is ours, the gate goes in normally
    res = sb.run(ss_output=SS_1880, unit_active_rc=0)
    assert res.returncode == 0, res.stderr
    assert sb.gate.exists()
    assert sb.menu.exists()


# --- dpkg trigger: homeui changing under us ----------------------------------

def test_trigger_wires_gate_after_homeui_upgrade(sb):
    # install-time state: homeui too old -> no gate, no menu
    res = sb.run(homeui_ver=HOMEUI_OLD)
    assert res.returncode == 0, res.stderr
    assert not sb.gate.exists()
    # homeui gets upgraded later; dpkg fires our trigger — gate + menu appear
    # without a reinstall, and the runtime/service are not touched.
    sb.log.write_text("")
    res = sb.run(action="triggered")
    assert res.returncode == 0, res.stderr
    assert sb.gate.exists() and sb.cache.exists() and sb.redirect.exists()
    assert sb.menu.exists()
    assert "deb-systemd-invoke stop" not in sb.log_text()
    assert "reload" in sb.log_text()


def test_trigger_tears_gate_down_when_homeui_is_removed(sb):
    # a fully wired install...
    res = sb.run()
    assert res.returncode == 0, res.stderr
    assert sb.gate.exists() and sb.menu.exists()
    # ...then homeui is removed: its snippets vanish and OUR gate's includes
    # would break nginx at the next restart — the trigger must tear it down.
    sb.log.write_text("")
    res = sb.run(action="triggered", homeui_ver=None)
    assert res.returncode == 0, res.stderr
    assert not sb.gate.exists()
    assert not sb.cache.exists()
    assert not sb.redirect.exists()
    assert not sb.menu.exists()
    assert "reload" in sb.log_text()  # nginx picks up the removal


def test_trigger_never_touches_the_runtime(sb):
    res = sb.run(action="triggered")
    assert res.returncode == 0, res.stderr
    assert not sb.runtime.exists()  # no swap, no extraction on a trigger


def test_reconfigure_after_homeui_downgrade_cleans_up_the_gate(sb):
    # same idempotence through the configure path (dpkg-reconfigure)
    res = sb.run()
    assert sb.gate.exists() and sb.menu.exists()
    res = sb.run(homeui_ver=HOMEUI_OLD)
    assert res.returncode == 0, res.stderr
    assert not sb.gate.exists()
    assert not sb.menu.exists()
