"""Packaging invariants: static checks over the debian/, config/ and vendor/ files.

Why: the package's contract (dependencies, loopback bind, auth gate, user-data
preservation) is pinned explicitly — an accidental edit of debian/* breaks a
test at build time (dh_auto_test) instead of surfacing on a controller. The
tests only read repository files; nothing is built or executed.
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


# --- service user -----------------------------------------------------------

def test_rules_ships_sysusers_conf_explicitly():
    # the CI chroot's debhelper lacks dh_installsysusers — rules must install it
    rules = _read("debian/rules")
    assert "usr/lib/sysusers.d/wb-node-red.conf" in rules
    assert "debian/wb-node-red.sysusers" in rules


# --- systemd unit -----------------------------------------------------------

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


def test_rules_stops_service_across_the_upgrade_swap():
    """--no-restart-after-upgrade stops the unit before the code swap and starts
    it after, so the new Node-RED never collides with the old one on the port."""
    rules = _read("debian/rules")
    assert "--no-restart-after-upgrade" in rules


# --- SBOM (CRA) -------------------------------------------------------------

def test_install_ships_sbom():
    """The CRA-mandated SBOM must keep shipping: nothing fails at build time if
    it silently drops out of .install."""
    install = _read("debian/wb-node-red.install")
    assert "sbom.cdx.json" in install
    assert "usr/share/wb-node-red" in install


# --- vendoring --------------------------------------------------------------

def test_vendor_pins_node_red_without_community_palette():
    pkg = json.loads(_read("vendor/package.json"))
    deps = pkg.get("dependencies", {})
    assert "node-red" in deps
    # the community palette is not shipped; users install it via Palette Manager
    assert "node-red-contrib-wirenboard" not in deps
    assert "node-red-contrib-wirenboard" not in _read("vendor/package-lock.json")


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


def test_postrm_removes_runtime_but_preserves_user_data():
    postrm = _read("debian/postrm")
    assert "/mnt/data/wb-node-red-runtime" in postrm
    assert 'rm -rf "$RUNTIME_DIR"' in postrm
    # sysusers convention: the user stays (it may still own files on /mnt/data)
    assert "deluser" not in postrm
    # The gate is a plain package file: dpkg unlinks it before postrm runs
    # (Policy 6.8), and unlinking it by hand is what broke reinstall while it
    # lived in /etc as a conffile. Only the re-render stays ours.
    assert "node-red.json" not in postrm
    assert "wb-homeui/gates.d" not in postrm
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


def test_flow_demonstrates_the_on_subtopic_write():
    """The write example: inject → mqtt out to the buzzer's /on subtopic."""
    flows = json.loads(_read("config/flows.json"))
    broker_id = next(n["id"] for n in flows if n.get("type") == "mqtt-broker")
    out = next(n for n in flows if n.get("type") == "mqtt out")
    assert out["topic"] == "/devices/buzzer/controls/enabled/on"
    assert out["broker"] == broker_id
    # WB commands are never retained
    assert out["retain"] == "false"
    injects = [n for n in flows if n.get("type") == "inject"]
    assert {n.get("payload") for n in injects} >= {"1", "0"}
    assert all(out["id"] in wires for n in injects for wires in n["wires"])
    # the /on convention is non-obvious — the flow must carry the explainer
    comments = [n for n in flows if n.get("type") == "comment"]
    assert any("/on" in n.get("info", "") for n in comments)


# --- homeui service gate (declarative gates.d) ------------------------------

def test_gate_declared_as_homeui_gates_d_json():
    """The gate is one JSON consumed by homeui's gates.d mechanism — internal
    port 1880, external 21880, admin role, menu.title for the Integrations item.
    homeui requires externalPort explicitly and nests the title under `menu`."""
    gate = json.loads(_read("config/gates.d/node-red.json"))
    assert gate["internalPort"] == 1880
    assert gate["externalPort"] == 21880
    assert gate["role"] == "admin"
    title = gate.get("menu", {}).get("title", {})
    assert title.get("ru") and title.get("en")


def test_gate_json_shipped_as_homeui_package_drop_in():
    """homeui reads gates from two dirs: /usr/share for package-shipped ones and
    /etc/wb-homeui for the admin's. Ours is a package gate — and a package file
    under /etc would be a dpkg conffile, which is what broke reinstall."""
    install = _read("debian/wb-node-red.install")
    assert "config/gates.d/node-red.json" in install
    assert "usr/share/wb-mqtt-homeui/gates.d" in install
    # neither of the two ways a file can land in /etc may put the gate back
    assert "etc/wb-homeui" not in install
    assert "etc/wb-homeui" not in _read("debian/rules")


def test_maintscript_drops_the_legacy_gate_conffile():
    """The gate used to be shipped into /etc as a conffile; dpkg never drops an
    obsolete conffile itself, and an /etc gate shadows the package one. The
    version is a historical fact — do not bump it with the package."""
    lines = [ln for ln in _read("debian/wb-node-red.maintscript").splitlines()
             if ln.strip() and not ln.lstrip().startswith("#")]
    assert len(lines) == 1, lines
    fields = lines[0].split()
    assert fields[0] == "rm_conffile"
    assert fields[1] == "/etc/wb-homeui/gates.d/node-red.json"
    # "~" also covers local rebuilds of the last conffile release
    assert fields[2].endswith("~")


def test_maintscript_prior_version_brackets_the_conffile_era():
    """Too high and the migration never runs; too low — the quiet failure — and
    boxes that still carry the old conffile are skipped (1.1.0 was the last
    release shipping it)."""
    prior = _read("debian/wb-node-red.maintscript").split()[2]
    version = re.match(r"\S+ \(([^)]+)\)", _read("debian/changelog")).group(1)
    dpkg = shutil.which("dpkg")
    if dpkg is None:
        pytest.skip("dpkg not available")

    def compare(a, op, b):
        return subprocess.run([dpkg, "--compare-versions", a, op, b],
                              check=False).returncode == 0

    assert compare(prior, "gt", "1.1.0"), (prior, "must cover 1.1.0 boxes")
    assert compare(prior, "le", version), (prior, version)


def test_maintscript_file_is_not_executable():
    """debhelper would run it and take its stdout as the content. The build
    fails loudly on that, so this is just the cheaper guard."""
    mode = (DEBIAN / "wb-node-red.maintscript").stat().st_mode
    assert not mode & 0o111, f"{mode & 0o777:#o}"


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
