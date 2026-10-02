"""Bounded, descriptor-relative file access. The controller owns grant policy.

No deletion, recursion, content logging, or permission inference from preferences.
POSIX only until a Windows handle-relative backend is qualified.
"""

import hashlib
import os
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from time import time
from uuid import uuid4

from assistant.platform.base import (
    AccessError,
    PermissionDenied,
    UncertainAction,
    UnsupportedTarget,
)


@dataclass
class FileGrant:
    grant_id: str
    root: Path
    modes: frozenset[str]
    expires_at: float
    revoked: bool = False


@dataclass(frozen=True)
class FileObservation:
    relative_path: str
    size: int
    sha256: str
    observed_at: float


@dataclass(frozen=True)
class FileReceipt:
    operation_id: str
    method: str
    observation: FileObservation
    status: str = "observed"


class FileRoute:
    """One controller-supplied grant; optional authority checks live revocation.

    Caller must serialize operations with cancellation. Already committed changes
    cannot be cancelled; an uncertain receipt must be reconciled before retrying.
    """

    def __init__(
        self,
        grant: FileGrant,
        *,
        max_bytes: int = 4 * 1024 * 1024,
        max_entries: int = 512,
        authority: Callable[[FileGrant, str], bool] | None = None,
    ):
        if max_bytes < 1 or max_entries < 1:
            raise ValueError("Limits must be positive.")
        self.grant = grant
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self.authority = authority

    def _authorize(self, mode: str) -> None:
        if (
            self.grant.revoked
            or time() >= self.grant.expires_at
            or mode not in self.grant.modes
            or (self.authority and not self.authority(self.grant, mode))
        ):
            raise PermissionDenied("File grant is absent, expired, revoked, or out of scope.")

    @staticmethod
    def _parts(path: str, *, allow_root: bool = False) -> tuple[str, ...]:
        if (
            not isinstance(path, str)
            or "\x00" in path
            or "\\" in path
            or ":" in path
            or path.startswith("/")
            or ".." in path.split("/")
        ):
            raise PermissionDenied("Use a relative path within the granted root.")
        parts = PurePosixPath(path).parts
        if not parts and not allow_root:
            raise PermissionDenied("A file path is required.")
        return parts

    @contextmanager
    def _directory(self, parts: tuple[str, ...], mode: str) -> Iterator[int]:
        self._authorize(mode)
        if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
            raise UnsupportedTarget(
                "Secure descriptor-relative file backend unavailable on this OS."
            )
        root = Path(self.grant.root)
        if not root.is_absolute() or ".." in root.parts:
            raise PermissionDenied("Grant root must be an absolute canonical directory.")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptor = os.open(root.anchor, flags)
        try:
            for part in (*root.parts[1:], *parts):
                following = os.open(part, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = following
            self._authorize(mode)
            yield descriptor
        except OSError as error:
            raise AccessError(
                "File directory is inaccessible or contains a symbolic link."
            ) from error
        finally:
            os.close(descriptor)

    def _read_at(self, parent: int, name: str) -> bytes:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise PermissionDenied("Only regular non-hardlinked files are supported.")
            if info.st_size > self.max_bytes:
                raise AccessError("File exceeds the configured byte limit.")
            chunks = bytearray()
            while len(chunks) <= self.max_bytes:
                chunk = os.read(descriptor, min(65536, self.max_bytes + 1 - len(chunks)))
                if not chunk:
                    break
                chunks.extend(chunk)
            if len(chunks) > self.max_bytes:
                raise AccessError("File exceeds the configured byte limit.")
            after = os.fstat(descriptor)
            if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise AccessError("File changed during observation; retry with fresh state.")
            return bytes(chunks)
        finally:
            os.close(descriptor)

    @staticmethod
    def _observation(path: str, data: bytes) -> FileObservation:
        return FileObservation(path, len(data), hashlib.sha256(data).hexdigest(), time())

    def read(self, path: str) -> tuple[bytes, FileObservation]:
        parts = self._parts(path)
        with self._directory(parts[:-1], "read") as parent:
            data = self._read_at(parent, parts[-1])
            self._authorize("read")
            return data, self._observation(path, data)

    def list(self, path: str = ".") -> tuple[str, ...]:
        parts = self._parts(path, allow_root=True)
        with self._directory(parts, "list") as parent:
            names = []
            with os.scandir(parent) as entries:
                for entry in entries:
                    if len(names) >= self.max_entries:
                        raise AccessError(
                            "Directory exceeds entry limit; narrow the requested folder."
                        )
                    names.append(entry.name)
            self._authorize("list")
            return tuple(sorted(names))

    def write(self, path: str, data: bytes, *, expected_sha256: str | None = None) -> FileReceipt:
        """Atomically replace an existing file only with an expected prior hash.

        A new file uses link-based exclusive publication, so it never overwrites a
        racing creation. Existing-file CAS assumes no concurrent external writer.
        """
        if not isinstance(data, bytes) or len(data) > self.max_bytes:
            raise AccessError("Write requires bytes within the configured limit.")
        parts = self._parts(path)
        with self._directory(parts[:-1], "write") as parent:
            name = parts[-1]
            try:
                previous = self._read_at(parent, name)
            except FileNotFoundError:
                previous = None
            if previous is not None and (
                expected_sha256 is None or hashlib.sha256(previous).hexdigest() != expected_sha256
            ):
                raise PermissionDenied("Overwrite requires a matching fresh expected SHA-256.")
            if previous is None and expected_sha256 is not None:
                raise AccessError("Expected file disappeared.")
            temporary = f".permit-{uuid4().hex}"
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent,
            )
            committed = False
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                self._authorize("write")
                if previous is None:
                    os.link(
                        temporary, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False
                    )
                    committed = True
                    os.unlink(temporary, dir_fd=parent)
                else:
                    current = self._read_at(parent, name)
                    if hashlib.sha256(current).hexdigest() != expected_sha256:
                        raise AccessError("File changed before replacement.")
                    os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
                    committed = True
                os.fsync(parent)
                observed = self._read_at(parent, name)
                if observed != data:
                    raise UncertainAction("Write committed but postcondition differs; reconcile.")
                return FileReceipt(uuid4().hex, "atomic-write", self._observation(path, observed))
            except Exception as error:
                if committed and not isinstance(error, UncertainAction):
                    raise UncertainAction(
                        "Write committed; observation failed. Reconcile."
                    ) from error
                raise
            finally:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass

    def move(self, source: str, destination: str, *, expected_sha256: str) -> FileReceipt:
        """No-clobber regular-file move inside one grant, with an observed hash.

        Publish a hard link then unlink source. A failure between these operations
        leaves both names and is uncertain; reconcile rather than blindly retry.
        """
        src, dst = self._parts(source), self._parts(destination)
        with self._directory(src[:-1], "move") as src_parent:
            with self._directory(dst[:-1], "move") as dst_parent:
                original = self._read_at(src_parent, src[-1])
                if hashlib.sha256(original).hexdigest() != expected_sha256:
                    raise AccessError("Move requires a matching fresh expected SHA-256.")
                self._authorize("move")
                os.link(
                    src[-1],
                    dst[-1],
                    src_dir_fd=src_parent,
                    dst_dir_fd=dst_parent,
                    follow_symlinks=False,
                )
                try:
                    source_info = os.stat(src[-1], dir_fd=src_parent, follow_symlinks=False)
                    destination_info = os.stat(dst[-1], dir_fd=dst_parent, follow_symlinks=False)
                    if not stat.S_ISREG(destination_info.st_mode) or (
                        source_info.st_dev,
                        source_info.st_ino,
                    ) != (destination_info.st_dev, destination_info.st_ino):
                        raise UncertainAction("Move source changed during dispatch; reconcile.")
                    self._authorize("move")
                    os.unlink(src[-1], dir_fd=src_parent)
                    os.fsync(src_parent)
                    os.fsync(dst_parent)
                    observed = self._read_at(dst_parent, dst[-1])
                    if observed != original:
                        raise UncertainAction("Move postcondition differs; reconcile.")
                    return FileReceipt(
                        uuid4().hex, "no-clobber-move", self._observation(destination, observed)
                    )
                except Exception as error:
                    raise UncertainAction(
                        "Move may have committed or left two names; reconcile."
                    ) from error
