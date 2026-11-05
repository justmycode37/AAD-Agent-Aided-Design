# Build input identity

`aiad.engine.identity.cache_identity` is a standard-library-only function. It
returns `(key, record)`: a lowercase SHA256 hex key and a detached JSON input
record. It does not import the CAD kernel or the revision implementation.

```python
from aiad.engine.identity import cache_identity

key, record = cache_identity(
    source="result = Box(60, 40, 6)\r\n",
    applied_params={"width": 60.0, "spacing": [46.0, 26.0]},
    spec={"requirements": [{"id": "one_body", "solids": 1}]},
    kind="part",
    units="mm",
    material="steel_1018",
    process="cnc_3axis",
    kernel_versions={"build123d": "0.13.0", "occt": "8.0.1.1.0"},
    tolerances={"linear": 0.001},
    asset_sha256={},
    pipeline_identity="<identity of the complete build pipeline and runtime>",
)
```

All arguments are required. Pass `None` for an absent material or process,
and `{}` for no assets or no configured tolerances. `kind`, `units`, and
`pipeline_identity` must be nonblank strings. `kernel_versions` must be a
nonempty object of nonblank component names and version strings. Asset names
must be nonblank strings; each value must be a 64-character lowercase SHA256
hex digest. Material and process, when present, must also be nonblank strings.

## Canonical form

The record contains every argument and `"schema": "aiad.cache-identity.v1"`.
The digest is SHA256 over these exact bytes, with no trailing newline:

```python
import json

json.dumps(
    record, ensure_ascii=False, allow_nan=False,
    sort_keys=True, separators=(",", ":"),
).encode("utf-8")
```

This is the specified Python 3.12 JSON encoding, not a claim of compatibility
with another canonical JSON standard. Objects are copied with keys sorted by
Unicode code point, recursively. Their input order is immaterial; array order,
string contents, Unicode normalization, and source line endings are preserved.
Integer `1`, float `1.0`, boolean `true`, `0.0`, and `-0.0` have distinct
encodings. Increment the schema before changing the encoding or its meaning.
The returned dict and its nested containers are mutable copies, not immutable
storage; keep the record unchanged when retaining it alongside its key.

Accepted values are exactly JSON null, booleans, plain Python `int`, finite
`float`, `str`, `list`, and `dict` with string keys. Strings must contain valid
Unicode scalar values. Tuples, sets, bytes, `Decimal`, custom types and
container subclasses are rejected, not coerced. Cycles and repeated references
to the same mutable container, including across arguments, are rejected:
JSON cannot preserve Python container identity. Python's integer-to-string
limit also applies. Invalid types raise `TypeError`; invalid values, including
non-finite numbers and invalid Unicode, raise `ValueError`.

## Integrating a future cache

The caller must establish a complete, stable input snapshot before using the
key. This function does not discover or attest inputs:

1. Pass the **exact source text** the worker will execute. To retain UTF-8
   file bytes and line endings, decode the bytes directly with strict UTF-8;
   do not strip, normalize, or use a text read that translates newlines.
2. Pass all **applied** parameter values, including defaults and overrides,
   and the entire parsed spec consumed by the checks. Do not substitute the
   override subset or discard unknown spec fields. Reject duplicate YAML keys
   and unsupported YAML values before this call. If the effective values or
   spec cannot be established reliably, do not look up a cache entry.
3. Stage every asset the build and checks can read, then hash the exact staged
   bytes. Supply every logical name exposed to the worker and its digest in
   `asset_sha256`. The worker must consume that same stable snapshot. This
   module neither reads files nor validates paths, staging, or digest contents.
4. Supply all relevant kernel component versions and configured tolerances.
   Supply a nonblank, caller-owned `pipeline_identity` that changes whenever
   build or validation code, dependencies, runtime, platform, or other
   result-affecting configuration changes. It must also distinguish requested
   outputs and options, such as preview or check suites, if those are cached.
   A digest of a complete trusted build manifest is one suitable identity;
   an unqualified version or a commit that ignores local changes is not.

Build and check code must consume these same JSON values and must not depend on
object insertion order or Python container aliasing. The canonical record can
be used to supply those values. Do not convert a tuple or other unsupported
value solely for hashing while the model still observes the original type.
If time, randomness, environment, project paths, unstaged files, or any other
unbound input can affect the result, control or bind it in the pipeline
identity, or skip caching. No determinism or input completeness is inferred.

The key identifies declared inputs only. It is not an authenticated receipt,
an artifact integrity check, or evidence that a build passed quality gates.
A cache hit must not by itself promote a revision to `verified`. Immutable
storage, head movement, file staging, and trusted verification are later work.
