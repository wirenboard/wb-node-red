"""Packaging invariants for the native wb-node-red Debian package.

File-reading checks that assert the package's contract (dependencies, bind
addresses, the auth gate) without building or running anything.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEBIAN = ROOT / "debian"


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


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


# --- maintainer scripts + runtime -------------------------------------------

def test_maintainer_scripts_have_valid_sh_syntax():
    for script in ("debian/postinst", "debian/postrm"):
        subprocess.run(["sh", "-n", str(ROOT / script)], check=True)


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
    # never unpack onto the small root if /mnt/data isn't mounted.
    assert 'mountpoint -q "$ROOT/mnt/data"' in postinst
    # seed flows only if absent — an upgrade must not overwrite the user's flows.
    assert '[ ! -e "$DATA_DIR/flows.json" ]' in postinst
    # and never through a planted symlink (root cp would write through it)
    assert '[ ! -L "$DATA_DIR/flows.json" ]' in postinst


def test_postrm_removes_runtime_and_gate_but_preserves_user_data():
    postrm = _read("debian/postrm")
    assert "/mnt/data/wb-node-red-runtime" in postrm
    assert 'rm -rf "$RUNTIME_DIR"' in postrm
    assert "deluser --system" in postrm
    # the gate declaration is dropped and the remaining gates re-rendered
    assert "/etc/wb-homeui/gates.d/node-red.json" in postrm
    assert "wb-homeui-gates apply" in postrm
    # the user data dir is NEVER removed automatically.
    assert 'rm -rf "/mnt/data/wb-node-red"' not in postrm
    assert "$DATA_DIR" not in postrm


def test_debhelper_token_appears_exactly_once_per_maintainer_script():
    """debhelper substitutes EVERY literal token occurrence, comments included —
    a stray mention splices generated code into a comment (field-found, exit 127)."""
    for script in ("debian/postinst", "debian/postrm"):
        lines = [l for l in _read(script).splitlines() if "#DEBHELPER#" in l]
        assert lines == ["#DEBHELPER#"], (
            f"{script}: the debhelper token must appear exactly once, "
            f"alone on its line; found {lines!r}")


# --- settings ---------------------------------------------------------------

def test_settings_bind_loopback_and_mnt_data():
    settings = _read("config/settings.js")
    assert 'uiHost: "127.0.0.1"' in settings
    assert "uiPort: 1880" in settings
    assert 'httpAdminRoot: "/"' in settings
    assert '/mnt/data/wb-node-red' in settings
    # no adminAuth setting — nginx is the auth boundary (match the key form).
    assert "adminAuth:" not in settings


def test_settings_user_overlay_keeps_security_keys_pinned():
    """An override must not widen the loopback bind or move the userDir:
    the pinned keys are re-asserted AFTER the require."""
    settings = _read("config/settings.js")
    overlay = settings.index('require("/mnt/data/wb-node-red/settings-user.js")')
    for pinned in ('module.exports.uiHost = "127.0.0.1";',
                   "module.exports.uiPort = 1880;",
                   'module.exports.userDir = "/mnt/data/wb-node-red";'):
        assert settings.index(pinned) > overlay, \
            f"{pinned!r} must be re-asserted after the user overlay merge"


def test_settings_js_parses():
    """A syntax error in settings.js kills the service at boot."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available in this environment")
    subprocess.run([node, "--check", str(ROOT / "config" / "settings.js")],
                   check=True)


# --- default flow -----------------------------------------------------------

def test_flow_points_at_local_broker_and_device_tree():
    flows = json.loads(_read("config/flows.json"))
    brokers = [n for n in flows if n.get("type") == "mqtt-broker"]
    assert brokers, "expected a pre-wired mqtt-broker node"
    # 127.0.0.1, not "localhost": under verbatim DNS (Node >= 17) localhost can
    # resolve to ::1 first, but mosquitto usually listens IPv4-only.
    assert brokers[0]["broker"] == "127.0.0.1"
    assert brokers[0]["port"] == "1883"
    topics_nodes = [n for n in flows if n.get("type") == "mqtt in"]
    assert any(n.get("topic") == "/devices/#" for n in topics_nodes)
    # the mqtt-in node must reference the broker node's id, or it is disconnected
    assert all(n["broker"] == brokers[0]["id"] for n in topics_nodes)


# --- homeui service gate (declarative gates.d) ------------------------------

def test_gate_declared_as_homeui_gates_d_json():
    """The gate is one JSON consumed by homeui's gates.d mechanism — internal
    port 1880 (homeui derives external 21880), admin role, titled for the menu."""
    gate = json.loads(_read("config/gates.d/node-red.json"))
    assert gate["internalPort"] == 1880
    assert gate["role"] == "admin"
    assert gate.get("title", {}).get("ru") and gate["title"].get("en")
    # no externalPort: homeui derives 20000 + 1880 = 21880 deterministically.
    assert "externalPort" not in gate


def test_gate_json_shipped_to_homeui_gates_dir():
    install = _read("debian/wb-node-red.install")
    assert "config/gates.d/node-red.json" in install
    assert "etc/wb-homeui/gates.d" in install


def test_postinst_applies_gate_through_homeui_cli_not_handrolled_nginx():
    """The gate is rendered by homeui (wb-homeui-gates apply); the package must
    not hand-roll an nginx server block or auth_request of its own."""
    postinst = _read("debian/postinst")
    assert "wb-homeui-gates apply" in postinst
    for handrolled in ("listen 21880", "auth_request", "conf.d/wb-node-red",
                       "proxy_pass", "custom-menu"):
        assert handrolled not in postinst, \
            f"postinst hand-rolls the gate ({handrolled!r}) instead of using gates.d"


def test_no_handrolled_nginx_gate_conf_in_repo():
    """The service-gate server block, cache zone and menu drop-in are homeui's
    job now — the package ships none of them."""
    for gone in ("nginx/wb-node-red.conf",
                 "nginx/wb-node-red-auth-cache.conf",
                 "config/custom-menu.json"):
        assert not (ROOT / gone).exists(), f"{gone} should be gone (homeui owns the gate)"


def test_redirect_dropin_bounces_old_subpath_to_the_gate():
    """Old /node-red/ is kept as a pure 302 to homeui's /open-node-red bounce,
    shipped as a homeui-server-block drop-in."""
    redir = _read("nginx/wb-node-red-redirect.conf")
    assert "return 302 /open-node-red;" in redir
    assert "/node-red" in redir
    assert "proxy_pass" not in redir
    install = _read("debian/wb-node-red.install")
    assert "nginx/wb-node-red-redirect.conf" in install
    assert "etc/nginx/includes/default.wb.d" in install
