"""Fetch a remote directory over SFTP without trusting the server's tree.

asyncssh's recursive `get()` recreates the server's symlinks locally and then writes through
them, and its own check of a link target does not catch `x/../` chains; a hostile server can
make it write outside the destination (SEC-2). This walk creates only the directories and
regular files it has validated itself: it never creates a symlink, refuses a name that is empty,
holds a slash or repeats in one directory, skips symlinks and special files, and caps depth and
entry count.
"""

from __future__ import annotations

import asyncio
import stat
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    import asyncssh

MAX_DEPTH = 32
MAX_ENTRIES = 200_000
CONCURRENT_FILES = 8


class UnsafeTree(Exception):
    """The remote tree has an entry that cannot come from a real filesystem."""


def _kind(entry: asyncssh.SFTPName) -> str:
    """`dir`, `file` or `other` (symlink, device, socket) from the entry's mode bits."""
    mode = entry.attrs.permissions
    if mode is None:
        return "other"
    if stat.S_ISDIR(mode):
        return "dir"
    if stat.S_ISREG(mode):
        return "file"
    return "other"


def _plain_name(name: str) -> bool:
    return name not in ("", ".", "..") and "/" not in name and "\0" not in name


async def fetch_tree_checked(sftp: asyncssh.SFTPClient, remote_dir: str, dest: Path) -> None:
    """Copy `remote_dir` into the new directory `dest` (made here, it must not exist).

    Raises:
        UnsafeTree: if the server lists a name or a depth that a real tree cannot have.
        asyncssh.SFTPError: for a remote read error.
    """
    dest.mkdir()
    gate = asyncio.Semaphore(CONCURRENT_FILES)
    entries = 0

    async def copy_file(remote: str, local: Path) -> None:
        async with gate:
            # follow_symlinks=True: a file that turns out to be a link is read through, never
            # recreated as a local link.
            await sftp.get(remote, str(local), follow_symlinks=True)

    async def walk(remote: str, local: Path, depth: int) -> None:
        nonlocal entries
        if depth > MAX_DEPTH:
            raise UnsafeTree(f"the tree is deeper than {MAX_DEPTH} levels")
        seen: set[str] = set()
        files: list[tuple[str, Path]] = []
        subdirs: list[tuple[str, Path]] = []
        for entry in await sftp.readdir(remote):
            name = entry.filename
            if isinstance(name, bytes):
                name = name.decode("utf-8", "surrogateescape")
            if name in (".", ".."):
                continue
            if not _plain_name(name) or name in seen:
                raise UnsafeTree(f"the server listed {name!r} in {remote!r}")
            seen.add(name)
            entries += 1
            if entries > MAX_ENTRIES:
                raise UnsafeTree(f"the tree has more than {MAX_ENTRIES} entries")
            kind = _kind(entry)
            child_remote = f"{remote.rstrip('/')}/{name}"
            if kind == "dir":
                subdirs.append((child_remote, local / name))
            elif kind == "file":
                files.append((child_remote, local / name))
        for _, child_local in subdirs:
            child_local.mkdir()
        for child_remote, child_local in subdirs:
            await walk(child_remote, child_local, depth + 1)
        await asyncio.gather(*(copy_file(remote_file, path) for remote_file, path in files))

    await walk(remote_dir, dest, 0)
