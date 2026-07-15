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
    # in the runtime Depends stanza, not just Build-Depends
    assert re.search(r"^Depends:(?:.|\n )*\bnodejs\b", control, re.MULTILINE), \
        "nodejs missing from the runtime Depends field"


def test_control_is_arch_all():
    assert "Architecture: all" in _read("debian/control")


# --- service user -----------------------------------------------------------

def test_service_user_is_declared_via_sysusers():
    sysusers = _read("debian/wb-node-red.sysusers")
    assert re.search(r"^u wb-node-red\b", sysusers, re.MULTILINE)
    assert "/mnt/data/wb-node-red" in sysusers
    # the user comes from systemd-sysusers, not a manual adduser call
    assert "adduser" not in _read("debian/control")


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
    # crash loop must be bounded
    assert "StartLimitIntervalSec=" in unit
    assert "StartLimitBurst=" in unit


def test_rules_vendors_without_compilation():
    rules = _read("debian/rules")
    # noarch comes from the @node-rs strip + *.node invariant, NOT from
    # --omit=optional on npm ci (that would break npm sbom)
    ci_lines = [l for l in rules.splitlines() if "npm ci" in l]
    assert ci_lines, "expected the npm ci vendoring line"
    assert all("--omit=optional" not in l for l in ci_lines)
    assert "rm -rf vendor/node_modules/@node-rs" in rules
    assert re.search(r"find vendor/node_modules -name '\*\.node'", rules)
    assert re.search(r"npm sbom .*--omit=optional", rules)


def test_rules_runs_the_suite_in_dh_auto_test():
    """The suite runs in every build (dh_auto_test), honoring nocheck."""
    rules = _read("debian/rules")
    assert "python3 -m pytest" in rules
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


def test_install_ships_sbom():
    """The vendor layer, which builds the SBOM, declares it for installation."""
    install = _read("debian/wb-node-red.install")
    assert "sbom.cdx.json" in install
    assert "usr/share/wb-node-red" in install


def test_control_description_mentions_port_gate():
    # apt show should describe the dedicated-port access model.
    assert "21880" in _read("debian/control")


# --- vendoring --------------------------------------------------------------

def test_vendor_pins_node_red_and_wb_palette():
    pkg = json.loads(_read("vendor/package.json"))
    deps = pkg.get("dependencies", {})
    assert "node-red" in deps
    assert "node-red-contrib-wirenboard" in deps
