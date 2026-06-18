"""Packaging tail: keep curated services off unattended-upgrades (design §3.11).

Container apps are riskier to auto-upgrade than system packages, so the shared
helper ships an apt.conf.d drop-in that blacklists itself and the in-tree
node-red service package from unattended-upgrades; upgrades happen only on
purpose. This module pins that drop-in's presence, shape, and install mapping.

Running unattended-upgrades end-to-end on a controller is still a manual
verification item.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HELPER = REPO / "packaging" / "wb-docker-app"

APT_CONF = HELPER / "apt" / "52wb-docker-app-no-unattended"
INSTALL = HELPER / "debian" / "wb-docker-app.install"


def test_apt_conf_exists():
    assert APT_CONF.is_file()


def test_apt_conf_blacklists_helper_and_service_packages():
    text = APT_CONF.read_text()
    # The directive unattended-upgrades reads to skip packages.
    assert "Unattended-Upgrade::Package-Blacklist" in text
    # The shared helper and the in-tree node-red service package are excluded,
    # each anchored with `$` so they match only themselves.
    assert '"wb-docker-app$"' in text
    assert '"wb-node-red$"' in text


def test_apt_conf_lands_in_apt_conf_d():
    mappings = INSTALL.read_text()
    assert "apt/52wb-docker-app-no-unattended etc/apt/apt.conf.d/" in mappings
