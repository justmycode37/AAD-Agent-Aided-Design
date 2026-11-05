# Geometry candidate cache

`aad.engine.cache.GeometryCache` stores one opaque, nonempty `part.brep` per
declared input identity. It imports only the standard library. It never loads
BREP geometry or imports/executes `model.py`.

This is a candidate-byte cache. It has no revision numbers, working copy,
checkout, head, job promotion, `BuildResult`, cached metrics/checks, status or
validation receipt. S2's `daemon/projects.py` owns numbered revisions and head
movement. Its planned #52 failed-job retention (up to five candidates per
project) is separate from this cache. Deleting this cache does not require
deleting revision history or retained jobs. This package does not complete S1
revisions/cache integration.

## API and identity

```python
from aad.engine.cache import GeometryCache

cache = GeometryCache(project / ".aad" / "cache")
# key and record came from cache_identity(**complete_trusted_snapshot), PR42.
entry = cache.put(key, record, generated / "part.brep", candidate_dir=generated)
entry = cache.lookup(key, record)
candidate_brep = cache.restore(key, record, fresh_destination)
```

All paths name caller-owned locations. The cache directory's parent and the
destination's parent must already exist. `put` creates the cache root if needed;
a lookup/restore miss does not create it. `fresh_destination` **must not already
exist**, including as an empty directory or dangling symlink. Restore creates
it with mode `0700`, then copies into an exclusively created `part.brep` with
mode `0600`. The returned `Path` is a regular, single-link file with independent
bytes and inode, ready to supply to a fresh validator. It is never a hardlink
to the cache. `CachedGeometry` is frozen and contains only `key`, `brep_path`,
`byte_length` and `sha256`; its cache path must be treated as read-only.

The supplied plain JSON record must have **exactly** the PR42 fields:
`schema`, `source`, `applied_params`, `spec`, `kind`, `units`, `material`,
`process`, `kernel_versions`, `tolerances`, `asset_sha256`, `pipeline_identity`.
The schema is `aad.cache-identity.v1`. Validation matches PR42: explicit object
fields; nonblank identifying strings; explicit null material/process when
absent; nonempty kernel versions with nonblank names/versions; nonblank asset
names mapped to lowercase SHA256 digests. Empty source is allowed. Nested
values must be plain dict/list/string/int/finite float/bool/null, with string
object keys and valid Unicode scalar values. Custom objects/subclasses,
tuples, nonfinite numbers, shared containers and cycles are rejected.

The key must equal SHA256 of the exact UTF-8 Python 3.12 encoding:

```python
json.dumps(record, ensure_ascii=False, allow_nan=False,
           sort_keys=True, separators=(",", ":")).encode("utf-8")
```

No values are defaulted or coerced. Integers, floats, booleans and signed zero
retain their distinct encodings. The cache takes an immutable encoded snapshot
at each call; subsequent caller mutation cannot alter a stored manifest. It
compares canonical bytes rather than Python's numeric-coercing dict equality.
There is no runtime dependency on the unmerged identity module and no vendored
copy of it. The manifest below is storage metadata for that existing identity,
not another way to compute input identity.

The caller must establish the **actual complete snapshot** consumed by the
pipeline: exact source, every applied/default parameter, whole spec, staged
asset digests, kernel versions, configured tolerances, and a pipeline identity
covering all result-affecting code/runtime/configuration. The module checks the
declaration's schema/hash, not its completeness or provenance. Unknown applied
parameters, assets or pipeline snapshots mean skipping caching. Converting live
tuples solely to obtain a hash is not acceptable. See PR42's identity docs and
issues #21/#31 for the integration and trust boundaries.

## Required ownership and lifecycle

Before `put`, the caller must fully terminate and reap the producing process
**and all descendants**, close outstanding writable handles, stop any other
writers, and prevent all source-directory mutation until `put` returns. An
exited process leader alone is insufficient. The source must be a nonempty
regular file, with `st_nlink == 1`, within `candidate_dir`. Here “private” means
an unaliased, single-link file in a tree under the caller's exclusive control;
source permissions are not changed. A final file symlink, directory/FIFO,
hardlink, symlink within the candidate tree, outside path or literal `..`
component is rejected. The input filename may differ; only `part.brep` is stored.

The cache and candidate trees must be disjoint, including after resolving the
configured roots' ancestors. In production, the model must have no writable
grant to the cache or any of its ancestors. The coordinator owns the cache,
its lock, and every restore destination. It must prevent concurrent mutation
of restore destinations too. Ancestors of configured cache/source/destination
roots are **trusted and resolved**; the roots themselves and paths inside the
owned trees must not be symlinks. This is not a hostile-directory-race defense.
File-descriptor checks and before/after file metadata checks detect some
changes; they cannot replace these ownership/lifecycle preconditions.

The cache never writes the source or committed entries. A successful restore
leaves the cache unchanged and supplies independent writable candidate bytes.
It touches only its cache/staging trees and the explicitly new destination;
it does not create missing ancestor directories or write project state.

## Publication and lookup

The on-disk layout is deliberately fixed:

```text
cache/
  .lock
  .stage-<random>/              # private, unpublished; ignored by lookup
  <64-character-key>/
    part.brep                  # mode 0400
    manifest.json              # mode 0400
```

Each UTF-8 manifest has exactly four fields: `key`, `input_record`,
`byte_length`, `sha256`. There are no manifest-selected paths or arbitrary
artifact names. A committed directory must contain exactly these two files.
Lookup rejects malformed/duplicate-key manifests, identity/key mismatches,
missing/extra members, symlinks, hardlinks, wrong file types, empty payloads,
and byte length/hash mismatches before returning anything. Checksums provide
integrity consistency, not authentication against someone able to rewrite both
manifest and payload. No physical/kernel geometry limits are inferred; payloads
are streamed in bounded I/O chunks with no arbitrary total-size limit.

Writers use a fresh mode-`0700` staging directory on the cache filesystem,
copy/hash the bytes, flush and fsync both files, and atomically rename the
directory under an exclusive advisory POSIX `flock`. All trusted writers must
use this protocol. Readers/restores hold shared locks. The single lock
serializes publications across keys; it is intentionally simple. An identical
publication reuses the validated entry. Different bytes for a committed key
raise `CacheConflict` and preserve the entry, without inferring a cause.
A corrupt committed entry is not overwritten or automatically repaired.

Ordinary publication exceptions clean up their staging directory. A killed
writer releases the OS lock and can leave abandoned `.stage-*` data, which is
never a hit. A kill after the atomic rename may leave a complete committed
entry. There is no automatic staging janitor. Only delete abandoned staging or
the cache after stopping **all** cache operations; never unlink/replace `.lock`
while operations can run. Publication atomicity here concerns process failures
on a local POSIX filesystem, not a guarantee of power-loss persistence,
network-filesystem semantics or cross-OS durability.

## Restore, failures and validation

Restore validates the entry, then opens the payload again without following
symlinks and recomputes its size/hash **during copying**, before returning a
candidate. Existing destinations are never overwritten. If a copy fails, the
new directory and any partial `part.brep` are retained for diagnostics, and
the exception includes a note naming the directory. These bytes are not a
successful restore; discard/quarantine that destination and retry in another
fresh directory. Original I/O errors are preserved rather than diagnosed as a
geometry failure.

`CacheMiss` means no committed root/key; `CacheCorruption` means an existing
entry failed identity/file/integrity validation; `CacheConflict` means differing
publication bytes; `CacheInputError` means an invalid supplied identity or
unsafe path/file. Other filesystem errors retain their original `OSError`
(including `FileExistsError` for an existing destination).
`CacheUnsupported` fails explicitly when POSIX `flock`/`O_NOFOLLOW` primitives
are unavailable. Lock/filesystem-operation failures are surfaced; there is no
unlocked or Windows fallback.

A hit skips **at most geometry generation**. It supplies bytes to a fresh
trusted native hard/spec/process validator, never cached proof. Same-process
model metrics/checks cannot attest these bytes. S2 must use a distinct restored
candidate and run its validator before any revision promotion. Existing
`run_build` rejects preexisting `part.brep`; this package leaves that guard
intact and adds no cache-hit branch to it.

## Tests

Use Python 3.12 and `uv`. An isolated environment outside the repository avoids
changing root configuration or the lock file:

```sh
cache_test_dir=$(mktemp -d)
uv venv --python 3.12 "$cache_test_dir/venv"
uv pip install --python "$cache_test_dir/venv/bin/python" -e packages/engine pytest pytest-timeout
git show origin/s1/cache-identity:packages/engine/src/aad/engine/identity.py > "$cache_test_dir/pr42_identity.py"
chmod 444 "$cache_test_dir/pr42_identity.py"
PYTHONDONTWRITEBYTECODE=1 AAD_CACHE_IDENTITY_SOURCE="$cache_test_dir/pr42_identity.py" \
  "$cache_test_dir/venv/bin/python" -m pytest -p no:cacheprovider packages/engine/tests/test_cache.py -q
```

The external-source test executes the **actual** PR42 function read-only from
temporary storage, without installing or vendoring it. It visibly skips if
`AAD_CACHE_IDENTITY_SOURCE` is absent. The regular tests independently exercise
schema/key binding and numeric distinctions without that branch dependency.
Pure storage tests need no kernel or Linux model sandbox. The import/opaque
operations probe runs with `-I -S`, blocks kernel and pipeline imports, and
checks stdlib-only imports.

The separate `test_native_box_brep_store_restore_and_fresh_reload_probe` uses a
trusted native Box producer that exits before publication, then reloads the
restored BREP in a fresh trusted process. It checks native validity, one solid,
volume 24 and bounding box `[2, 3, 4]`. This demonstrates real BREP
interoperability; storage alone proves neither native validity nor satisfaction
of a spec/process gate. Test subprocesses are trusted lifecycle fixtures, not
an unsandboxed production model fallback.
