"""Write-once geometry candidates, never revision state or validation evidence.

Only stdlib code runs here; even a BREP is opaque bytes. See ``cache.md`` for
the terminated-producer, trusted-directory and fresh-validation preconditions.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Iterator

try:
    import fcntl
except ImportError:  # Importing is harmless elsewhere; constructing a cache fails explicitly.
    fcntl = None


class CacheError(Exception):
    """Cache operation failed; no geometry-validity conclusion follows."""


class CacheMiss(CacheError):
    """No committed entry exists for the requested key."""


class CacheCorruption(CacheError):
    """An existing entry cannot pass its identity or byte-integrity checks."""


class CacheConflict(CacheError):
    """Different candidate bytes were supplied for an already committed key."""


class CacheInputError(CacheError, ValueError):
    """The identity, file or path does not meet the caller contract."""


class CacheUnsupported(CacheError):
    """The required POSIX locking/file primitives are unavailable."""


@dataclass(frozen=True)
class CachedGeometry:
    key: str
    brep_path: Path  # Read-only cache data; restore() supplies a writable private copy.
    byte_length: int
    sha256: str


@dataclass(frozen=True)
class CachedGeometryEntry:
    """A validated geometry candidate and the identity that names it."""

    geometry: CachedGeometry
    input_record: dict[str, Any]


_FIELDS = {
    "schema", "source", "applied_params", "spec", "kind", "units", "material",
    "process", "kernel_versions", "tolerances", "asset_sha256", "pipeline_identity",
}


def _digest(value: object) -> bool:
    return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _json_tree(value: Any, seen: set[int]) -> None:
    if value is None or type(value) in (bool, int, str):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) not in (dict, list):
        raise CacheInputError("input record must contain only plain, finite JSON values")
    if id(value) in seen:
        raise CacheInputError("input record contains a shared or cyclic container")
    seen.add(id(value))
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise CacheInputError("input record object keys must be plain strings")
        value = value.values()
    for item in value:
        _json_tree(item, seen)


def _encode(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _identity(key: str, record: dict[str, Any]) -> bytes:
    """Validate PR42's complete supplied record; never discover/default inputs."""
    if not _digest(key):
        raise CacheInputError("key must be a lowercase SHA256 hex digest")
    try:
        _json_tree(record, set())
        if type(record) is not dict or set(record) != _FIELDS:
            raise CacheInputError("input record must contain exactly the cache-identity.v1 fields")
        if record["schema"] != "aad.cache-identity.v1":
            raise CacheInputError("unsupported input record schema")
        for field in ("schema", "source", "kind", "units", "material", "process", "pipeline_identity"):
            value = record[field]
            if field in ("material", "process") and value is None:
                continue
            if type(value) is not str or (field != "source" and not value.strip()):
                raise CacheInputError(f"{field} must be a plain nonblank string (source may be empty)")
        for field in ("applied_params", "spec", "kernel_versions", "tolerances", "asset_sha256"):
            if type(record[field]) is not dict:
                raise CacheInputError(f"{field} must be a plain JSON object")
        versions = record["kernel_versions"]
        if not versions or any(
            not name.strip() or type(version) is not str or not version.strip()
            for name, version in versions.items()
        ):
            raise CacheInputError("kernel_versions requires nonblank component names and versions")
        if any(not name.strip() or not _digest(digest) for name, digest in record["asset_sha256"].items()):
            raise CacheInputError("asset_sha256 requires nonblank names and lowercase SHA256 digests")
        encoded = _encode(record)
    except (ValueError, RecursionError) as exc:
        raise CacheInputError(str(exc)) from exc
    if hashlib.sha256(encoded).hexdigest() != key:
        raise CacheInputError("key does not match the canonical input record")
    return encoded  # Immutable snapshot, detached from every caller-owned container.


def _path(path: Path) -> Path:
    path = Path(path).absolute()
    if ".." in path.parts or not path.name:
        raise CacheInputError(f"path must name a directory/file without '..': {path}")
    return path


def _directory(path: Path) -> Path:
    if not stat.S_ISDIR(path.lstat().st_mode):  # Includes rejection of final symlinks.
        raise CacheInputError(f"expected a directory without a symlink: {path}")
    return path.resolve(strict=True)


def _file(info: os.stat_result, path: Path, *, nonempty: bool = True) -> None:
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or (nonempty and info.st_size <= 0):
        raise CacheInputError(f"expected a private, {'nonempty ' if nonempty else ''}single-link regular file: {path}")


def _fingerprint(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _open_regular(path: Path) -> BinaryIO:
    before = path.lstat()
    _file(before, path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if _fingerprint(os.fstat(fd)) != _fingerprint(before):
            raise CacheInputError(f"file changed while opening: {path}")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def _copy_and_hash(path: Path, output: BinaryIO | None = None) -> tuple[int, str]:
    with _open_regular(path) as source:
        before = os.fstat(source.fileno())
        digest, length = hashlib.sha256(), 0
        while chunk := source.read(1024 * 1024):
            if output is not None:
                output.write(chunk)
            length += len(chunk)
            digest.update(chunk)
        if length != before.st_size or _fingerprint(os.fstat(source.fileno())) != _fingerprint(before):
            raise CacheInputError(f"file changed while reading: {path}")
    return length, digest.hexdigest()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate manifest object key: {key}")
        result[key] = value
    return result


class GeometryCache:
    def __init__(self, cache_dir: Path):
        if os.name != "posix" or fcntl is None or not hasattr(os, "O_NOFOLLOW"):
            raise CacheUnsupported("GeometryCache requires POSIX flock and O_NOFOLLOW")
        path = _path(cache_dir)
        # Ancestors of configured roots are caller-trusted; only resolve those.
        self.cache_dir = path.parent.resolve(strict=True) / path.name

    def _separate(self, directory: Path) -> None:
        if directory.is_relative_to(self.cache_dir) or self.cache_dir.is_relative_to(directory):
            raise CacheInputError("cache and candidate directory trees must not overlap")

    @contextmanager
    def _locked(self, *, create: bool = False) -> Iterator[Path]:
        if create:
            self.cache_dir.mkdir(mode=0o700, exist_ok=True)
        try:
            root = _directory(self.cache_dir)
        except FileNotFoundError as exc:
            raise CacheMiss("cache directory does not exist") from exc
        fd = os.open(root / ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        with os.fdopen(fd, "r+b") as lock:
            _file(os.fstat(lock.fileno()), root / ".lock", nonempty=False)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX if create else fcntl.LOCK_SH)
            try:
                yield root
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _entry(self, root: Path, key: str, identity: bytes) -> CachedGeometry:
        entry = root / key
        try:
            entry.lstat()
        except FileNotFoundError as exc:
            raise CacheMiss(f"no committed entry for {key}") from exc
        try:
            _directory(entry)
            if {item.name for item in entry.iterdir()} != {"manifest.json", "part.brep"}:
                raise CacheInputError("entry must contain only manifest.json and part.brep")
            with _open_regular(entry / "manifest.json") as stream:
                manifest = json.loads(stream.read().decode("utf-8"), object_pairs_hook=_unique_object)
            if type(manifest) is not dict or set(manifest) != {"key", "input_record", "byte_length", "sha256"}:
                raise CacheInputError("unexpected manifest fields")
            if manifest["key"] != key or _identity(key, manifest["input_record"]) != identity:
                raise CacheInputError("manifest input identity differs from the requested identity")
            length, digest = manifest["byte_length"], manifest["sha256"]
            if type(length) is not int or length <= 0 or not _digest(digest):
                raise CacheInputError("invalid manifest byte length or SHA256")
            if _copy_and_hash(entry / "part.brep") != (length, digest):
                raise CacheInputError("payload byte length or SHA256 differs from the manifest")
            return CachedGeometry(key, entry / "part.brep", length, digest)
        except (OSError, ValueError, RecursionError) as exc:
            raise CacheCorruption(f"corrupt entry {entry}: {exc}") from exc

    def put(self, key: str, input_record: dict[str, Any], brep_path: Path, *, candidate_dir: Path) -> CachedGeometry:
        """Copy a terminated producer's private file and atomically publish once."""
        identity = _identity(key, input_record)
        original = _path(candidate_dir)
        candidate = _directory(original)
        self._separate(candidate)
        source = _path(brep_path)
        prefix = original if source.is_relative_to(original) else candidate
        if not source.is_relative_to(prefix):
            raise CacheInputError("candidate file is outside candidate_dir")
        relative = source.relative_to(prefix)
        parent = candidate
        for component in relative.parts[:-1]:
            parent = _directory(parent / component)
        source = candidate / relative
        _file(source.lstat(), source)
        with self._locked(create=True) as root:
            staging = Path(tempfile.mkdtemp(prefix=".stage-", dir=root))
            try:
                with (staging / "part.brep").open("xb") as output:
                    length, digest = _copy_and_hash(source, output)
                    output.flush()
                    os.fsync(output.fileno())
                manifest = {"key": key, "input_record": json.loads(identity),
                            "byte_length": length, "sha256": digest}
                with (staging / "manifest.json").open("xb") as output:
                    output.write(_encode(manifest))
                    output.flush()
                    os.fsync(output.fileno())
                for name in ("part.brep", "manifest.json"):
                    (staging / name).chmod(0o400)
                try:
                    existing = self._entry(root, key, identity)
                except CacheMiss:
                    os.rename(staging, root / key)  # Same filesystem, under the writer lock.
                    return CachedGeometry(key, root / key / "part.brep", length, digest)
                if (existing.byte_length, existing.sha256) != (length, digest):
                    raise CacheConflict(f"different candidate bytes for committed key {key}; entry preserved")
                return existing
            finally:
                if staging.exists():
                    shutil.rmtree(staging)

    def prune(self, max_bytes: int) -> int:
        """Delete the oldest entries until the cache fits; return how many were removed."""
        with self._locked(create=True) as root:
            sizes = []
            for entry in root.iterdir():
                if not _digest(entry.name):
                    continue
                info = entry.lstat()
                size = sum(item.lstat().st_size for item in entry.iterdir()) if stat.S_ISDIR(info.st_mode) else info.st_size
                sizes.append((info.st_mtime_ns, entry.name, size))
            total = sum(size for _, _, size in sizes)
            removed = 0
            for _, name, size in sorted(sizes):
                if total <= max_bytes:
                    break
                shutil.rmtree(root / name)
                total -= size
                removed += 1
            return removed

    def lookup(self, key: str, input_record: dict[str, Any]) -> CachedGeometry:
        """Return an entry only after checking its full identity and payload."""
        identity = _identity(key, input_record)
        with self._locked() as root:
            return self._entry(root, key, identity)

    def candidates(self, *, source: str, pipeline_identity: str) -> tuple[CachedGeometryEntry, ...]:
        """List valid candidates for one exact source and pipeline snapshot.

        Corrupt, missing and unrelated entries are ignored. The coordinator
        must still filter the returned input records against the current
        request before staging them for a worker.
        """
        with self._locked() as root:
            matches: list[CachedGeometryEntry] = []
            for entry in root.iterdir():
                key = entry.name
                if not _digest(key):
                    continue
                try:
                    _directory(entry)
                    with _open_regular(entry / "manifest.json") as stream:
                        manifest = json.loads(stream.read().decode("utf-8"), object_pairs_hook=_unique_object)
                    if type(manifest) is not dict or set(manifest) != {"key", "input_record", "byte_length", "sha256"}:
                        continue
                    record = manifest["input_record"]
                    identity = _identity(key, record)
                    geometry = self._entry(root, key, identity)
                except (CacheError, OSError, ValueError, RecursionError):
                    # A damaged cache entry is a miss, never a build failure.
                    continue
                if record["source"] == source and record["pipeline_identity"] == pipeline_identity:
                    matches.append(CachedGeometryEntry(geometry=geometry, input_record=record))
            return tuple(matches)

    def restore(self, key: str, input_record: dict[str, Any], fresh_candidate_dir: Path) -> Path:
        """Copy into a new directory; recheck the bytes during that copy."""
        identity = _identity(key, input_record)
        path = _path(fresh_candidate_dir)
        destination = path.parent.resolve(strict=True) / path.name
        self._separate(destination)
        with self._locked() as root:
            entry = self._entry(root, key, identity)
            destination.mkdir(mode=0o700)  # Must not exist, even as an empty directory/symlink.
            target = destination / "part.brep"
            try:
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "wb") as output:
                    try:
                        copied = _copy_and_hash(entry.brep_path, output)
                    except CacheInputError as exc:
                        raise CacheCorruption(f"cached payload failed its file checks: {exc}") from exc
                    if copied != (entry.byte_length, entry.sha256):
                        raise CacheCorruption("copied payload differs from the validated manifest")
                    output.flush()
                    _file(os.fstat(output.fileno()), target)
                    if os.fstat(output.fileno()).st_size != entry.byte_length:
                        raise OSError(f"restored file has an unexpected byte length: {target}")
                    os.fsync(output.fileno())
            except BaseException as exc:
                exc.add_note(f"Restore did not succeed; diagnostic candidate data is retained at {destination}")
                raise
            return target
