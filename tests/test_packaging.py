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
