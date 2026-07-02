"""Packaging invariants for the native wb-node-red Debian package.

File-reading checks that assert the package's contract (dependencies, bind
addresses, the auth gate) without building or running anything.
"""

import json
import re
import subprocess
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


def test_control_description_mentions_port_gate():
    # apt show should describe the dedicated-port access model.
    assert "21880" in _read("debian/control")


def test_postinst_installs_port_gate_with_cert_check_and_rollback():
    """Contract strings for the gate install: cert check, homeui version gate,
    restore-on-failed-nginx -t (behavior driven in test_postinst_behavior.py)."""
    postinst = _read("debian/postinst")
    assert 'NGINX_GATE_DST="$NGINX_CONFD/wb-node-red.conf"' in postinst
    assert 'NGINX_CACHE_DST="$NGINX_CONFD/wb-node-red-auth-cache.conf"' in postinst
    assert 'NGINX_REDIR_DST="$NGINX_WB_D/wb-node-red-redirect.conf"' in postinst
    assert "/etc/ssl/sslip.pem" in postinst
    assert "nginx -t" in postinst
    assert "nginx_backup" in postinst  # restore on failure, don't just delete
    assert 'HOMEUI_MIN="2.235.4~"' in postinst


# --- dpkg trigger: react to homeui appearing/leaving -------------------------

def test_dpkg_trigger_rewires_gate_on_homeui_changes():
    """The homeui check must not be a one-shot: the trigger re-wires the gate
    after a homeui upgrade and tears it down after a homeui removal."""
    triggers = _read("debian/wb-node-red.triggers")
    assert "interest-noawait /etc/nginx/snippets" in triggers
    assert "interest-noawait /usr/share/wb-mqtt-homeui" in triggers
    postinst = _read("debian/postinst")
    assert "triggered)" in postinst
    assert "wire_gate_and_menu" in postinst  # gate/menu wiring only, no swap


def test_maintainer_scripts_have_valid_sh_syntax():
    for script in ("debian/postinst", "debian/postrm"):
        subprocess.run(["sh", "-n", str(ROOT / script)], check=True)


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
    # never unpack onto the small root if /mnt/data isn't mounted.
    assert 'mountpoint -q "$ROOT/mnt/data"' in postinst
    # seed flows only if absent — an upgrade must not overwrite the user's flows.
    assert '[ ! -e "$DATA_DIR/flows.json" ]' in postinst
    # and never through a planted symlink (root cp would write through it)
    assert '[ ! -L "$DATA_DIR/flows.json" ]' in postinst


def test_postrm_removes_runtime_but_preserves_user_data():
    postrm = _read("debian/postrm")
    assert "/mnt/data/wb-node-red-runtime" in postrm
    assert 'rm -rf "$RUNTIME_DIR"' in postrm
    assert "deluser --system" in postrm
    # the user data dir is NEVER removed automatically.
    assert 'rm -rf "/mnt/data/wb-node-red"' not in postrm
    assert "$DATA_DIR" not in postrm
    assert "nginx -t" in postrm


def test_debhelper_token_appears_exactly_once_per_maintainer_script():
    """debhelper substitutes EVERY literal token occurrence, comments included —
    a stray mention splices generated code into a comment (field-found, exit 127)."""
    for script in ("debian/postinst", "debian/postrm"):
        lines = [l for l in _read(script).splitlines() if "#DEBHELPER#" in l]
        assert lines == ["#DEBHELPER#"], (
            f"{script}: the debhelper token must appear exactly once, "
            f"alone on its line; found {lines!r}")
