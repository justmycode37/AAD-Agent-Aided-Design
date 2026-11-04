"""Atomic output operations anchored to a worker directory on POSIX."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import uuid


class OutputDirectory:
    """Keep all confined traversal and file operations relative to open handles.

    A later symlink substitution cannot redirect these operations. This does
    not replace the worker's OS sandbox or protect against moving its root.
    """

    def __init__(self, path: Path, job_root: Path | None):
        self.path = path
        self.fd: int | None = None
        self.existed = path.exists()
        if job_root is None:
            path.mkdir(parents=True, exist_ok=True)
            self.path = path.resolve()
            return
        if os.name != "posix" or not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
            raise NotImplementedError("Confined exports require POSIX directory-handle operations.")
        root = job_root.expanduser().resolve()
        relative = path.relative_to(root)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open(root.anchor, flags)
        try:
            # Also pin the root's ancestors; opening the root by absolute name
            # would otherwise follow a symlink introduced in an ancestor.
            for component in root.parts[1:]:
                child = os.open(component, flags, dir_fd=fd)
                os.close(fd)
                fd = child
            for component in relative.parts:
                try:
                    os.mkdir(component, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                child = os.open(component, flags, dir_fd=fd)
                os.close(fd)
                fd = child
            self.fd = fd
        except BaseException:
            os.close(fd)
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def mode(self, name: str) -> int | None:
        try:
            if self.fd is not None:
                return os.stat(name, dir_fd=self.fd, follow_symlinks=False).st_mode
            return (self.path / name).lstat().st_mode
        except FileNotFoundError:
            return None

    def ensure_current(self) -> None:
        if self.fd is not None:
            pinned = os.fstat(self.fd)
            current = self.path.stat(follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (pinned.st_dev, pinned.st_ino):
                raise OSError("Output directory changed during publication.")

    def is_dir(self, name: str) -> bool:
        mode = self.mode(name)
        return mode is not None and stat.S_ISDIR(mode)

    def replace(self, source: str, destination: str) -> None:
        if self.fd is not None:
            os.replace(source, destination, src_dir_fd=self.fd, dst_dir_fd=self.fd)
        else:
            os.replace(self.path / source, self.path / destination)

    def preserve(self, name: str) -> str | None:
        if self.fd is None or self.mode(name) is None:
            return None
        backup = f".{name}.{uuid.uuid4().hex}.bak"
        os.link(name, backup, src_dir_fd=self.fd, dst_dir_fd=self.fd, follow_symlinks=False)
        return backup

    def unlink(self, name: str) -> None:
        try:
            if self.fd is not None:
                os.unlink(name, dir_fd=self.fd)
            else:
                (self.path / name).unlink()
        except FileNotFoundError:
            pass

    def stage(self, name: str, data: bytes) -> str:
        temporary = f".{name}.{uuid.uuid4().hex}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        if self.fd is not None:
            fd = os.open(temporary, flags | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
        else:
            fd = os.open(self.path / temporary, flags, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            self.unlink(temporary)
            raise
        return temporary
