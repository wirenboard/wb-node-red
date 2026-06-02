"""wb-diag-collect integration (design.md §3.5.1, issue #5).

Issue #5 AC: "Service status and logs appear in the ``wb-diag-collect``
archive." The helper ships a collector script
(``/usr/lib/wb-docker-app/diag/wb-docker-app-diag-collect``) that prints each
service's ``systemctl status`` + recent logs, and a drop-in describing the
``commands`` entry that runs it.

The catch (the original audit finding): the *released* wb-diag-collect reads
ONLY its single main config (``/usr/share/wb-diag-collect/wb-diag-collect.conf``
— see ``wb.diag.diag_collect.DEFAULT_CONF_PATH`` upstream) and has **no
conf.d merge**. So a drop-in under ``conf.d/`` is never read and the collector
is never invoked — the AC was only partially met (the pre-existing
``wb-*.service`` journal glob still reached the archive, but the new
per-service ``systemctl status`` view did not).

This module closes that gap by *registering* our collector command directly
into wb-diag-collect's actual main config at helper install, and removing it on
package removal. The merge is idempotent and keyed on the command's
``filename`` so re-running (upgrades, second invocation) is a no-op, and it
touches only our own entry, leaving the rest of wb-diag-collect's config
untouched. If conf.d support later lands upstream, the shipped drop-in already
describes the same entry and this active merge can be retired.
"""

from __future__ import annotations

from pathlib import Path

# wb-diag-collect's single main config (upstream DEFAULT_CONF_PATH). The helper
# augments this file in place because the released tool has no conf.d merge.
DIAG_MAIN_CONF = Path("/usr/share/wb-diag-collect/wb-diag-collect.conf")

# Installed collector path and the archive filename its stdout is captured under
# (must match diag/60wb-docker-app.conf and debian/wb-docker-app.install).
COLLECTOR_CMD = "/usr/lib/wb-docker-app/diag/wb-docker-app-diag-collect"
COLLECTOR_FILENAME = "service/wb-docker-app"


def _is_ours(entry: object) -> bool:
    """True for the managed ``commands`` entry, identified by its filename."""
    return isinstance(entry, dict) and entry.get("filename") == COLLECTOR_FILENAME


def register(conf_path: Path = DIAG_MAIN_CONF) -> bool:
    """Add the collector ``commands`` entry to wb-diag-collect's main config.

    Idempotent: returns ``False`` (no write) if our entry is already present or
    if the config is missing/unreadable (wb-diag-collect not installed — nothing
    to integrate with). Returns ``True`` when it added the entry.

    Only our single entry is appended; every other key and command in
    wb-diag-collect's config is preserved as-is.
    """
    if not conf_path.is_file():
        return False
    import yaml

    data = yaml.safe_load(conf_path.read_text()) or {}
    commands = data.get("commands")
    if not isinstance(commands, list):
        commands = []
        data["commands"] = commands
    if any(_is_ours(c) for c in commands):
        return False
    commands.append({"filename": COLLECTOR_FILENAME, "command": COLLECTOR_CMD})
    conf_path.write_text(yaml.safe_dump(data, default_flow_style=False, sort_keys=False))
    return True


def deregister(conf_path: Path = DIAG_MAIN_CONF) -> bool:
    """Remove the collector entry from wb-diag-collect's main config.

    Idempotent inverse of :func:`register`: returns ``True`` if it removed our
    entry, ``False`` if there was nothing to remove (config missing or entry
    absent). Leaves the rest of the config untouched.
    """
    if not conf_path.is_file():
        return False
    import yaml

    data = yaml.safe_load(conf_path.read_text()) or {}
    commands = data.get("commands")
    if not isinstance(commands, list):
        return False
    kept = [c for c in commands if not _is_ours(c)]
    if len(kept) == len(commands):
        return False
    data["commands"] = kept
    conf_path.write_text(yaml.safe_dump(data, default_flow_style=False, sort_keys=False))
    return True
