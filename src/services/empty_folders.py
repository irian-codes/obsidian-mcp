"""Bounded plans and directory-only removal; never unlinks or trashes files."""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import PurePosixPath

from src.services import vault_fs

MAX_EMPTY_TREE_DIRECTORIES = 1000


class TreeNotEmpty(vault_fs.VaultFSError):
    """Preflight found an entry other than a directory."""


class TreeTooLarge(vault_fs.VaultFSError):
    """The whole tree cannot fit the bounded preflight plan."""


@dataclass(frozen=True)
class Directory:
    path: str
    device: int
    inode: int

    def matches(self, info: os.stat_result) -> bool:
        return stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino) == (
            self.device, self.inode,
        )


def plan_empty_tree(root_fd: int, relative: str) -> list[Directory]:
    """Preflight everything before removing anything; absent selection is [].

    Return preorder so reversing it removes children before parents. The stack
    stores paths/identities, not one open descriptor per directory. Every open
    is a fresh whole-path beneath-root lookup, including after a descent.
    """
    parts = PurePosixPath(relative).parts
    if not parts or any(part.startswith(".") for part in parts):
        raise vault_fs.UnsafePath("The vault root and hidden paths cannot be removed.")
    stack: list[tuple[str, Directory | None]] = [(relative, None)]
    plan: list[Directory] = []
    while stack:
        path, expected = stack.pop()
        try:
            fd = vault_fs.open_dir_beneath(root_fd, path)
        except FileNotFoundError:
            if not plan and expected is None:
                return []
            raise vault_fs.Conflict(f"Directory changed during preflight: {path}") from None
        try:
            info = os.fstat(fd)
            if expected is not None and not expected.matches(info):
                raise vault_fs.Conflict(f"Directory changed during preflight: {path}")
            plan.append(Directory(path, info.st_dev, info.st_ino))
            # Never filter hidden entries out of this listing: hidden content
            # must stop cleanup rather than make a nonempty tree look empty.
            with os.scandir(fd) as entries:
                for entry in entries:
                    child = f"{path}/{entry.name}"
                    if entry.name.startswith("."):
                        raise vault_fs.UnsafePath(f"Hidden entry prevents cleanup: {child}")
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode):
                        raise vault_fs.UnsafePath(f"Symlink prevents cleanup: {child}")
                    if not stat.S_ISDIR(info.st_mode):
                        raise TreeNotEmpty(f"Tree contains a non-directory entry: {child}")
                    if len(plan) + len(stack) >= MAX_EMPTY_TREE_DIRECTORIES:
                        raise TreeTooLarge(
                            f"Tree exceeds {MAX_EMPTY_TREE_DIRECTORIES} directories; select a smaller tree."
                        )
                    stack.append((child, Directory(child, info.st_dev, info.st_ino)))
        finally:
            vault_fs.close_quietly(fd, f"preflight directory {path}")
    return plan


def remove_empty_directory(root_fd: int, directory: Directory) -> None:
    """Remove one planned directory; a late file is protected by the kernel.

    Identity checking is optimistic. A substitution after stat may remove an
    empty replacement directory, but rmdir cannot delete its files. Anchoring
    retains the existing post-lookup parent-rename limitation (D26).
    """
    parent_fd, name = vault_fs.open_parent(root_fd, directory.path)
    try:
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not directory.matches(info):
            raise vault_fs.Conflict(f"Directory changed before removal: {directory.path}")
        os.rmdir(name, dir_fd=parent_fd)
        vault_fs.flush_dir_quietly(parent_fd, f"parent directory of {directory.path}")
    finally:
        vault_fs.close_quietly(parent_fd, f"parent directory of {directory.path}")
