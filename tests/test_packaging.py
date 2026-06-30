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
