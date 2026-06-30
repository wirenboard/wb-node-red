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
    apt = _read("apt/52wb-node-red-no-unattended")
    assert "wb-node-red" in apt
    assert "Package-Blacklist" in apt


# --- control ----------------------------------------------------------------

def test_control_depends_on_distro_nodejs():
    control = _read("debian/control")
    assert re.search(r"^Depends:.*", control, re.MULTILINE)
    assert "nodejs" in control
    assert "docker-ce" not in control
    assert "wb-docker-app" not in control


def test_control_is_arch_all():
    assert "Architecture: all" in _read("debian/control")


# --- systemd unit -----------------------------------------------------------

def test_service_runs_node_red_as_dedicated_user():
    unit = _read("debian/wb-node-red.service")
    assert "node-red/red.js" in unit
    assert "/mnt/data/wb-node-red-runtime/node_modules/node-red/red.js" in unit
    assert "/usr/lib/wb-node-red/settings.js" in unit
    assert "User=wb-node-red" in unit
    assert "User=root" not in unit
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


def test_rules_vendors_without_compilation():
    rules = _read("debian/rules")
    assert "npm ci" in rules
    # --omit=optional keeps the tree pure-JS so the package stays arch:all.
    assert "--omit=optional" in rules
    assert "node_modules" in rules


def test_rules_stops_service_across_the_upgrade_swap():
    """--no-restart-after-upgrade stops the unit before the code swap and starts
    it after, so the new Node-RED never collides with the old one on the port."""
    rules = _read("debian/rules")
    assert "--no-restart-after-upgrade" in rules


# --- SBOM (CRA) -------------------------------------------------------------

def test_rules_emits_and_ships_cyclonedx_sbom():
    """CRA: the build emits a CycloneDX SBOM of the vendored tree and ships it."""
    rules = _read("debian/rules")
    assert "npm sbom" in rules
    assert "cyclonedx" in rules.lower()
    assert "usr/share/wb-node-red/sbom.cdx.json" in rules


def test_control_description_mentions_port_gate():
    control = _read("debian/control")
    assert "21880" in control
    assert "/node-red/" in control
    assert "served as the /node-red/ path" not in control


def test_postinst_installs_port_gate_with_cert_check_and_rollback():
    """The gate is placed in conf.d, the redirect in the homeui block; postinst
    skips when the sslip cert is absent and rolls all three files back on nginx -t."""
    postinst = _read("debian/postinst")
    assert 'NGINX_GATE_DST="$NGINX_CONFD/wb-node-red.conf"' in postinst
    assert 'NGINX_CACHE_DST="$NGINX_CONFD/wb-node-red-auth-cache.conf"' in postinst
    assert 'NGINX_REDIR_DST="$NGINX_WB_D/wb-node-red-redirect.conf"' in postinst
    assert "/etc/ssl/sslip.pem" in postinst
    assert "nginx -t" in postinst
    assert 'rm -f "$NGINX_GATE_DST" "$NGINX_CACHE_DST" "$NGINX_REDIR_DST"' in postinst
    # the gate includes homeui snippets, so install only on homeui >= 2.235.4.
    assert "2.235.4" in postinst


# --- runtime tree lives on /mnt/data, not the tight root --------------------

def test_runtime_tree_delivered_to_mnt_data_consistently():
    """The node_modules tree ships as a tarball under /usr/share and is extracted
    to /mnt/data; rules, postinst and the unit must agree on the same path."""
    rules = _read("debian/rules")
    postinst = _read("debian/postinst")
    unit = _read("debian/wb-node-red.service")

    assert "node_modules.tar.gz" in rules
    assert "cp -a vendor/node_modules" not in rules
    assert "/usr/lib/wb-node-red/node_modules" not in rules

    assert "/usr/share/wb-node-red/node_modules.tar.gz" in postinst
    assert "/mnt/data/wb-node-red-runtime" in postinst
    assert "tar -xzf" in postinst

    assert "/mnt/data/wb-node-red-runtime/node_modules/node-red/red.js" in unit


def test_postinst_guards_mount_and_never_clobbers_user_flows():
    postinst = _read("debian/postinst")
    assert "mountpoint -q /mnt/data" in postinst
    # seed flows only if absent — an upgrade must not overwrite the user's flows.
    assert '[ ! -e "$DATA_DIR/flows.json" ]' in postinst
    assert "nginx -t" in postinst


def test_postrm_removes_runtime_but_preserves_user_data():
    postrm = _read("debian/postrm")
    assert "/mnt/data/wb-node-red-runtime" in postrm
    assert 'rm -rf "$RUNTIME_DIR"' in postrm
    assert "deluser --system" in postrm
    # the user data dir is NEVER removed automatically.
    assert 'rm -rf "/mnt/data/wb-node-red"' not in postrm
    assert "$DATA_DIR" not in postrm
    assert "nginx -t" in postrm


# --- vendoring --------------------------------------------------------------

def test_vendor_pins_node_red_and_wb_palette():
    pkg = json.loads(_read("vendor/package.json"))
    deps = pkg.get("dependencies", {})
    assert "node-red" in deps
    assert "node-red-contrib-wirenboard" in deps


# --- settings ---------------------------------------------------------------

def test_settings_bind_loopback_and_mnt_data():
    settings = _read("config/settings.js")
    assert 'uiHost: "127.0.0.1"' in settings
    assert "uiPort: 1880" in settings
    assert 'httpAdminRoot: "/"' in settings
    assert '/mnt/data/wb-node-red' in settings
    # no adminAuth setting — nginx is the auth boundary (match the key form).
    assert "adminAuth:" not in settings


# --- default flow -----------------------------------------------------------

def test_flow_points_at_local_broker_and_device_tree():
    flows = json.loads(_read("config/flows.json"))
    brokers = [n for n in flows if n.get("type") == "mqtt-broker"]
    assert brokers, "expected a pre-wired mqtt-broker node"
    # 127.0.0.1, not "localhost": under verbatim DNS (Node >= 17) localhost can
    # resolve to ::1 first, but mosquitto usually listens IPv4-only.
    assert brokers[0]["broker"] == "127.0.0.1"
    assert brokers[0]["port"] == "1883"
    topics = [n.get("topic") for n in flows if n.get("type") == "mqtt in"]
    assert "/devices/#" in topics


# --- homeui menu integration ------------------------------------------------

def test_custom_menu_dropin_targets_node_red_as_external():
    menu = json.loads(_read("config/custom-menu.json"))
    assert menu["id"] == "integrations"
    nodered = [c for c in menu.get("children", []) if c.get("id") == "node-red"]
    assert nodered, "expected a node-red child entry"
    entry = nodered[0]
    # still /node-red/ — the redirect drop-in bounces it to the port.
    assert entry["url"] == "/node-red/"
    # isExternal renders a full-page <a>; openInNewTab keeps homeui put (>= 2.235.4).
    assert entry["isExternal"] is True
    assert entry["openInNewTab"] is True
    assert entry.get("title", {}).get("en") and entry["title"].get("ru")


def test_menu_dropin_gated_on_homeui_version_and_cleaned_up():
    rules = _read("debian/rules")
    postinst = _read("debian/postinst")
    postrm = _read("debian/postrm")
    assert "custom-menu.json" in rules
    assert "/usr/share/wb-node-red/custom-menu.json" in postinst
    # placed into homeui's drop-in dir only on a homeui that renders isExternal.
    assert "/usr/share/wb-mqtt-homeui/custom-menu" in postinst
    assert 'dpkg --compare-versions "$homeui_ver" ge "2.235.4~"' in postinst
    assert "/usr/share/wb-mqtt-homeui/custom-menu/wb-node-red.json" in postrm
    assert "wb-mqtt-homeui" in _read("debian/control")
