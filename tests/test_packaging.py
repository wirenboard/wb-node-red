"""Packaging invariants for the native wb-node-red Debian package.

These are file-reading checks — they assert the *contract* of the package
(what it depends on, where it binds, how it is gated) without building or
running anything. They are the cheap guard rails; the real validation is the
controller install (HITL on aat3d5fw), see docs/native-node-red_plan.md.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEBIAN = ROOT / "debian"


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _nginx_location_blocks(conf: str) -> list:
    """Crudely split the conf into top-level `location ...` blocks (no nesting
    here), so a test can assert which blocks do / do not carry auth_request."""
    parts = re.split(r"(?m)^\s*location ", conf)
    return ["location " + p for p in parts[1:]]



# --- explicit updates -------------------------------------------------------

def test_excluded_from_unattended_upgrades():
    apt = _read("apt/52wb-node-red-no-unattended")
    assert "wb-node-red" in apt
    assert "Package-Blacklist" in apt


# --- control ----------------------------------------------------------------

def test_control_depends_on_distro_nodejs():
    control = _read("debian/control")
    assert re.search(r"^Depends:.*", control, re.MULTILINE)
    # nodejs is a runtime dependency (distro-provided, CRA-shared patching).
    assert "nodejs" in control
    assert "docker-ce" not in control
    assert "wb-docker-app" not in control


def test_control_is_arch_all():
    assert "Architecture: all" in _read("debian/control")


# --- systemd unit -----------------------------------------------------------

def test_service_runs_node_red_as_dedicated_user():
    unit = _read("debian/wb-node-red.service")
    assert "node-red/red.js" in unit
    # the runtime tree lives on /mnt/data (off the tight root); settings on /usr.
    assert "/mnt/data/wb-node-red-runtime/node_modules/node-red/red.js" in unit
    assert "/usr/lib/wb-node-red/settings.js" in unit
    assert "User=wb-node-red" in unit
    assert "User=root" not in unit
    # both runtime + userDir are on /mnt/data — wait for that mount before start.
    assert "RequiresMountsFor=/mnt/data" in unit


def test_service_is_hardened():
    unit = _read("debian/wb-node-red.service")
    assert "NoNewPrivileges=true" in unit
    assert "ReadWritePaths=/mnt/data/wb-node-red" in unit
    # ProtectSystem=strict is what makes the rest of the FS read-only and gives
    # ReadWritePaths its meaning; pin it and the other sandboxing directives so a
    # future edit can't silently weaken the confinement of an RCE-capable service.
    assert "ProtectSystem=strict" in unit
    assert "ProtectHome=true" in unit
    assert "PrivateTmp=true" in unit


def test_rules_vendors_without_compilation():
    rules = _read("debian/rules")
    assert "npm ci" in rules
    # --omit=optional keeps the tree pure-JS so the package stays arch:all.
    assert "--omit=optional" in rules
    assert "node_modules" in rules


def test_rules_stops_service_across_the_upgrade_swap():
    """dh_installsystemd --no-restart-after-upgrade stops the unit before the code
    swap and starts it after, so the new Node-RED never collides with the old one on
    the editor port during an upgrade. Load-bearing flag — pin it."""
    rules = _read("debian/rules")
    assert "--no-restart-after-upgrade" in rules


# --- SBOM (CRA) -------------------------------------------------------------

def test_rules_emits_and_ships_cyclonedx_sbom():
    """CRA: the build emits a CycloneDX SBOM of the vendored tree and ships it
    inside the package (so the bill of materials travels with the artifact)."""
    rules = _read("debian/rules")
    assert "npm sbom" in rules
    assert "cyclonedx" in rules.lower()
    assert "usr/share/wb-node-red/sbom.cdx.json" in rules


def test_control_description_mentions_port_gate():
    control = _read("debian/control")
    # apt show should describe the dedicated-port model, with /node-red/ kept only
    # as a redirect — not the old "served as the /node-red/ path" prose.
    assert "21880" in control
    assert "/node-red/" in control
    assert "served as the /node-red/ path" not in control
