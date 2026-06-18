"""Idempotent seeder of the user data layout.

The USER layer under ``/mnt/data/wb-docker-apps/<app>/`` is seed-if-absent: the
``docker-compose.override.yml``, ``.env`` and ``data/``
directory are written from defaults only when missing. They are never a
conffile and never overwritten on upgrade, so a user's edits survive
byte-for-byte. This module performs exactly that — create-if-absent, never
clobber — on the real filesystem.

A package may also ship its per-app default config under a ``seed/`` tree that
mirrors the user layout; :func:`seed_tree` drops that tree into the user layer
under the same create-if-absent, never-clobber rule.
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


def seed_tree(src_dir: Path, dst_root: Path) -> list[str]:
    """Copy every file under ``src_dir`` into ``dst_root``, only where absent.

    Walks ``src_dir`` recursively and, for each FILE, copies it to
    ``dst_root/<relative-path>`` (creating parent dirs) only when the
    destination does not already exist. A package ships its per-app default
    config under a ``seed/`` tree that mirrors the user layer; this drops those
    defaults into the user layer without ever clobbering a user's edits.

    Returns the relative paths actually created (existing destinations are
    skipped). If ``src_dir`` does not exist, returns ``[]``.
    """
    created: list[str] = []

    if not src_dir.exists():
        return created

    for src in sorted(p for p in src_dir.rglob("*") if p.is_file()):
        rel = src.relative_to(src_dir)
        target = dst_root / rel
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(src.read_bytes())
            created.append(str(rel))

    return created
