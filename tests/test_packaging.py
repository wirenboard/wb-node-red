"""Packaging invariants for the native wb-node-red Debian package.

File-reading checks that assert the package's contract (dependencies, bind
addresses, the auth gate) without building or running anything.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEBIAN = ROOT / "debian"


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _nginx_location_blocks(conf: str) -> list:
    """Split the conf into top-level `location ...` blocks (no nesting)."""
    parts = re.split(r"(?m)^\s*location ", conf)
    return ["location " + p for p in parts[1:]]


# --- the package is native, not docker -------------------------------------



# --- explicit updates -------------------------------------------------------

def test_excluded_from_unattended_upgrades():
    apt = _read("52wb-node-red")
    assert "wb-node-red" in apt
    assert "Package-Blacklist" in apt


# --- control ----------------------------------------------------------------

def test_control_depends_on_distro_nodejs():
    control = _read("debian/control")
    # nodejs must sit in the RUNTIME Depends stanza (distro-provided, CRA-shared
    # patching) — `"nodejs" in control` would be satisfied by Build-Depends alone.
    assert re.search(r"^Depends:(?:.|\n )*\bnodejs\b", control, re.MULTILINE), \
        "nodejs missing from the runtime Depends field"


def test_control_is_arch_all():
    assert "Architecture: all" in _read("debian/control")


# --- systemd unit -----------------------------------------------------------

def test_service_runs_node_red_as_dedicated_user():
    unit = _read("debian/wb-node-red.service")
    assert "/mnt/data/wb-node-red-runtime/node_modules/node-red/red.js" in unit
    assert "/usr/lib/wb-node-red/settings.js" in unit
    assert "User=wb-node-red" in unit
    assert "RequiresMountsFor=/mnt/data" in unit


def test_service_is_hardened():
    unit = _read("debian/wb-node-red.service")
    assert "NoNewPrivileges=true" in unit
    assert "ReadWritePaths=/mnt/data/wb-node-red" in unit
    # ProtectSystem=strict is what makes the rest of the FS read-only; pin the
    # sandboxing directives so a future edit can't silently weaken confinement.
    assert "ProtectSystem=strict" in unit
    assert "ProtectHome=true" in unit
    assert "PrivateTmp=true" in unit
    # a service that can't start (interrupted swap, port taken) must end in a
    # loud `failed` state, not respawn every 5 s forever on flash storage.
    assert "StartLimitIntervalSec=" in unit
    assert "StartLimitBurst=" in unit


def test_rules_vendors_without_compilation():
    rules = _read("debian/rules")
    # npm ci runs WITHOUT --omit=optional (npm's bundled minipass-fetch needs
    # its nested optional iconv-lite@0.7.x or `npm sbom` breaks) — arch:all
    # purity comes from the @node-rs strip + the *.node invariant instead.
    ci_lines = [l for l in rules.splitlines() if "npm ci" in l]
    assert ci_lines, "expected the npm ci vendoring line"
    assert all("--omit=optional" not in l for l in ci_lines)
    # the real noarch guard: strip the platform-specific binding, then fail the
    # build if ANY native binary remains in the tree.
    assert "rm -rf vendor/node_modules/@node-rs" in rules
    assert re.search(r"find vendor/node_modules -name '\*\.node'", rules)
    # the SBOM (and only the SBOM) still omits optionals, matching the strip.
    assert re.search(r"npm sbom .*--omit=optional", rules)


def test_rules_runs_the_suite_in_dh_auto_test():
    """The suite must run inside every package build (dh_auto_test), not only
    when a developer remembers pytest locally — otherwise the invariants it
    pins can drift through CI unnoticed."""
    rules = _read("debian/rules")
    assert "python3 -m pytest" in rules
    # respect DEB_BUILD_OPTIONS=nocheck, the standard skip switch.
    assert "nocheck" in rules
    assert "python3-pytest" in _read("debian/control")


def test_rules_stops_service_across_the_upgrade_swap():
    """--no-restart-after-upgrade stops the unit before the code swap and starts
    it after, so the new Node-RED never collides with the old one on the port."""
    rules = _read("debian/rules")
    assert "--no-restart-after-upgrade" in rules


# --- SBOM (CRA) -------------------------------------------------------------

def test_rules_emits_cyclonedx_sbom():
    """CRA: the build emits a CycloneDX SBOM of the vendored tree in debian/rules.

    Shipping it via .install is asserted in the vendor layer (where the SBOM
    and its .install line are introduced)."""
    rules = _read("debian/rules")
    assert "npm sbom" in rules
    assert "cyclonedx" in rules.lower()


def test_control_description_mentions_port_gate():
    # apt show should describe the dedicated-port access model.
    assert "21880" in _read("debian/control")
