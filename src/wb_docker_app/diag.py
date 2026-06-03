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

This module closes that gap by *registering* our collector command into
wb-diag-collect's actual main config at helper install, and removing it on
package removal.

Two properties matter because that file is shipped and owned by the foreign
*wb-diag-collect* package (review finding, register-diag major):

1. **We must not reformat it.** It is hand-authored YAML with comments and a
   particular layout. We therefore edit it *surgically as text*: every byte of
   wb-diag-collect's own content is preserved, and we only splice in (or pull
   out) a single, sentinel-delimited block of our own. We never round-trip the
   whole document through ``yaml.safe_dump`` (which would emit canonical YAML
   and strip all upstream comments/structure).

2. **An upstream upgrade reverts it.** The config lives under ``/usr/share``,
   so it is *not* a dpkg conffile: an unrelated ``apt upgrade wb-diag-collect``
   silently overwrites it back to the package default and drops our entry, with
   no maintainer-script of ours firing. So registration cannot be a one-shot at
   our own install time. The helper additionally ships an apt
   ``DPkg::Post-Invoke`` hook (``packaging/.../apt/53wb-docker-app-reg-diag``)
   that re-runs ``wb-docker-app register-diag`` after *every* dpkg/apt
   transaction; because :func:`register` is idempotent it is a cheap no-op
   except right after a wb-diag-collect upgrade, when it heals our entry back
   in. The merge here stays the single source of truth for what gets
   registered; if conf.d support later lands upstream the shipped drop-in
   already describes the same entry and both the merge and the hook can retire.
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

# Sentinels bounding the block we (and only we) own inside the foreign config.
# deregister() removes exactly the lines between them, restoring the original.
_BEGIN = "# >>> wb-docker-app diag (managed, do not edit) >>>"
_END = "# <<< wb-docker-app diag (managed) <<<"


def _our_block(at_top_level: bool) -> str:
    """The sentinel-delimited text we splice into the foreign config.

    When ``at_top_level`` we also emit the ``commands:`` key itself (the file
    has no ``commands`` block yet); otherwise we emit just the list item to sit
    under wb-diag-collect's existing ``commands:`` block.
    """
    item = (
        f"  - filename: {COLLECTOR_FILENAME}\n"
        f"    command: {COLLECTOR_CMD}\n"
    )
    body = ("commands:\n" + item) if at_top_level else item
    return f"{_BEGIN}\n{body}{_END}\n"


def _strip_our_block(text: str) -> str:
    """Return ``text`` with our sentinel block (if any) removed verbatim."""
    start = text.find(_BEGIN)
    if start == -1:
        return text
    end = text.find(_END, start)
    if end == -1:
        return text
    end = text.find("\n", end)
    end = len(text) if end == -1 else end + 1
    return text[:start] + text[end:]


def _commands_insert_pos(text: str) -> int | None:
    """Byte offset just after a top-level ``commands:`` line, or ``None``.

    Only an unindented ``commands:`` (a top-level mapping key) qualifies; this
    deliberately ignores any ``commands`` nested under another key.
    """
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\n")
        if not stripped[:1].isspace() and (
            stripped == "commands:" or stripped.startswith("commands:")
        ):
            return offset + len(line)
        offset += len(line)
    return None


def register(conf_path: Path = DIAG_MAIN_CONF) -> bool:
    """Add the collector ``commands`` entry to wb-diag-collect's main config.

    Idempotent: returns ``False`` (no write) if our entry is already present or
    if the config is missing/unreadable (wb-diag-collect not installed — nothing
    to integrate with). Returns ``True`` when it added the entry.

    The edit is surgical text splicing: wb-diag-collect's own content (comments,
    layout, every other command) is preserved byte-for-byte; only our
    sentinel-delimited block is inserted.
    """
    if not conf_path.is_file():
        return False

    text = conf_path.read_text()
    if _BEGIN in text:
        return False  # already registered

    pos = _commands_insert_pos(text)
    if pos is None:
        # No commands: block yet — append one (with our key) at end of file.
        sep = "" if (not text or text.endswith("\n")) else "\n"
        new_text = text + sep + _our_block(at_top_level=True)
    else:
        # Splice our list item directly under the existing commands: line.
        new_text = text[:pos] + _our_block(at_top_level=False) + text[pos:]

    conf_path.write_text(new_text)
    return True


def deregister(conf_path: Path = DIAG_MAIN_CONF) -> bool:
    """Remove the collector entry from wb-diag-collect's main config.

    Idempotent inverse of :func:`register`: returns ``True`` if it removed our
    block, ``False`` if there was nothing to remove (config missing or block
    absent). Removes exactly our sentinel-delimited region, leaving the rest of
    the foreign config untouched.
    """
    if not conf_path.is_file():
        return False

    text = conf_path.read_text()
    if _BEGIN not in text:
        return False

    conf_path.write_text(_strip_our_block(text))
    return True
