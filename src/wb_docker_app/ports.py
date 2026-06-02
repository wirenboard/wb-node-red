"""Internal loopback port allocator (module C).

Containers bind only to ``127.0.0.1:<internal>`` (design.md §3.8); the helper
allocates that internal port per app and keeps a registry so assignments
survive between invocations (the helper runs once per package operation, not
as a daemon). The allocator guarantees no collisions across apps.
"""

from __future__ import annotations

import json
from pathlib import Path


class PortAllocationError(RuntimeError):
    """Raised when no free loopback port is left in the configured range."""


class PortAllocator:
    """Hands out a persistent free loopback port per app."""

    def __init__(self, registry_path: Path, start: int = 20000, end: int = 29999):
        self._path = Path(registry_path)
        self._start = start
        self._end = end
        self._assigned: dict[str, int] = self._load()

    def _load(self) -> dict[str, int]:
        try:
            data = json.loads(self._path.read_text())
        except FileNotFoundError:
            return {}
        return {app: int(port) for app, port in data.items()}

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._assigned))

    def allocate(self, app: str) -> int:
        """Return a port in ``[start, end]`` for ``app``, persisting it."""
        if app in self._assigned:
            return self._assigned[app]
        taken = set(self._assigned.values())
        for port in range(self._start, self._end + 1):
            if port not in taken:
                self._assigned[app] = port
                self._save()
                return port
        raise PortAllocationError(
            f"no free port in range {self._start}-{self._end}"
        )

    def release(self, app: str) -> None:
        """Free ``app``'s port so it can be reused; a no-op if unassigned."""
        if self._assigned.pop(app, None) is not None:
            self._save()
