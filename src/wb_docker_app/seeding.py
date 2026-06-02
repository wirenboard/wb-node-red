"""Idempotent seeder of the user data layout (module E).

The USER layer under ``/mnt/data/wb-docker-apps/<app>/`` is seed-if-absent
(design.md §3.6): the ``docker-compose.override.yml``, ``.env`` and ``data/``
directory are written from defaults only when missing. They are never a
conffile and never overwritten on upgrade, so a user's edits survive
byte-for-byte. This module performs exactly that — create-if-absent, never
clobber — on the real filesystem.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping


def seed_app_dir(
    app_root: Path,
    files: Mapping[str, str],
    dirs: Iterable[str] = (),
) -> list[str]:
    """Seed ``app_root`` with default ``files`` and ``dirs``, only where absent.

    Returns the relative paths actually created (existing entries are skipped).
    """
    created: list[str] = []

    for rel in dirs:
        target = app_root / rel
        if not target.exists():
            target.mkdir(parents=True)
            created.append(rel)

    for rel, content in files.items():
        target = app_root / rel
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
            created.append(rel)

    return created
