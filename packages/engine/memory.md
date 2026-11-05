# Internal address-space policy and Mac memory observations

Build workers and sandboxed operations apply this S1 policy. The platform reads
`engine.memory` from `~/.aad/config.toml` (`[engine] memory = "auto"`) for each
job; direct `run_build` calls use the platform default unless an explicit byte
budget is passed. An aggregate resident-memory supervisor is not implemented.

## Explicit address-space policy

`aad.engine.memory.AddressSpaceBudget(bytes)` is an immutable low-level
**absolute per-process virtual-address-space ceiling** for `sandbox.popen`, not
a RAM, RSS, physical-footprint or aggregate job cap. `bytes` must be a built-in
integer with
`1 <= bytes < sys.maxsize`; booleans, other types, zero, negative and out-of-range
values are rejected. This conservative finite representation range excludes the
tested Mac's infinity sentinel. Platform setting `engine.memory` has different
semantics: it is an **additional virtual-address growth budget** from the
started worker's VMS baseline. `auto` is 16 GiB on Linux and macOS; explicit
settings accept a positive byte count or size using `B`, `KiB`, `MiB`, `GiB` or
`TiB`.

The engine build API uses that growth budget:

```python
from aad.engine.jobs import run_build
from aad.engine.types import BuildRequest

run_build(BuildRequest(source=source),
          memory_limit_bytes=16 * 1024 ** 3)
```

Omitting `memory_limit_bytes` selects the platform default. The trusted
bootstrap uses a temporary absolute ceiling large enough to start the worker
Python. The worker then measures its current VMS and sets both soft and hard
`RLIMIT_AS` to `current VMS + engine.memory` before importing model/kernel code.
This relative step allows useful caps on macOS, where an absolute cap below the
interpreter's hundreds of GB of VMS fails at setup. Operations, optional
previews and fallback scene tessellation use the same two-stage launch.
Importing the memory module itself imports neither
`resource`, `ctypes`, libproc, workers nor the CAD kernel.

The engine's independent `inspect_brep` worker also uses this policy. It takes
an optional `memory_limit_bytes`; omitting it selects the engine platform
default on Linux and macOS. The trusted build caller must pass its resolved
`engine.memory` value so a user override reaches that child. The validator
integration proposed in PR #96 is not part of `origin/main`: its clean 3D
validator must pass the build's resolved budget to `inspect_brep`, and its
clean 2D `inspect_sheet` worker must use the same budgeted `sandbox.popen`
path before that validator is enabled. If PR #96 merges first, add this
plumbing there before relying on `engine.memory` to cap validation.

Only the exact `AddressSpaceBudget` type is accepted by the low-level sandbox
hook. Unknown or resident-policy objects raise before launch. Unsupported
platforms or missing `resource.RLIMIT_AS`/get/set APIs also raise before launch.
No policy request selects a weaker backend or an unsandboxed fallback.

With a configured policy, the launcher validates both the original argv and the
wrapper. Inside the **existing** Seatbelt/bwrap boundary it starts the current
interpreter with `-I -S -B` and an absolute, trusted `memory.py` path. The
interpreter and bootstrap must be outside the writable job. Isolation excludes
job-directory/user/PYTHONPATH imports; disabling site excludes `.pth` startup.
The bootstrap imports only trusted standard-library code, installs the limit
before executing the target or importing worker/kernel code, then uses
`execvpe` with the original argv and sanitized environment. It never reads stdin
or creates a subprocess. No filesystem/network/process grant is added.

The startup bootstrap sets an absolute ceiling in the child before exec. The
worker's second stage reads its own VMS, adds the configured budget, and clamps
that value to any finite inherited limits. It sets both soft and hard limits in
one call and verifies the exact readback. Setup failure writes a trusted
`AAD_MEMORY_SETUP_FAILURE:` marker and prevents model/operation code from
running. The daemon/parent never changes its limits; there is no `preexec_fn`.
An unprivileged target cannot raise its hard ceiling; this is not a promise for
privileged targets.

Native/Python get/set/exec exceptions keep their type and diagnostic. A readback
mismatch raises `AddressSpaceSetupError` containing both pairs. The parent maps
setup failures to `SANDBOX_DENIED`, not OOM. Build and operation workers map
explicit `MemoryError` or `ENOMEM` to `OUT_OF_MEMORY`; signal-only exits remain
`WORKER_CRASH`. The reported exception is evidence of allocation failure, but
does not prove the configured ceiling was the only cause.

Immediately before target exec, the bootstrap restores the `SIGPIPE`, `SIGXFZ`
and `SIGXFSZ` defaults where those signals exist, matching the launcher's
`Popen(restore_signals=True)` behavior. Python startup had changed these to
ignored dispositions; carrying them into a native target could make a producer
survive a signal that normally terminates it. Other inherited dispositions are
preserved. Compiled native signal tests compare budgeted and ordinary launches;
parent dispositions remain unchanged.

`engine.memory=auto` allows 16 GiB of additional virtual address space per
worker on Linux and macOS. Linux child processes each receive their own budget,
so their total can exceed 16 GiB. On the tested Mac, absolute 64 MiB and 2 GiB
limits fail because the started Python VMS is around 420 GB; the worker-relative
budget sets the ceiling above that baseline. The bootstrap uses a temporary
absolute startup ceiling (2 TiB for the default budget) and reduces it before
model or operation code runs. This
policy limits virtual address growth, not resident memory. The sampled RSS API
below is not connected to a periodic supervisor; neither platform enforces an
aggregate resident budget. Set `aad config engine.memory=SIZE` to choose
another positive growth budget.

## Mac sampling, observation only

`sample_process_memory(process: subprocess.Popen) -> ProcessMemorySample | None`
lazily loads `/usr/lib/libproc.dylib` on the first live Mac sample. It returns:

| Field | Meaning |
|---|---|
| `pid` | The owned process queried |
| `timestamp_ns` | Monotonic timestamp immediately after the valid read |
| `resident_bytes` | Current per-process resident size |
| `virtual_bytes` | Current per-process virtual-address size |

It uses only `proc_pidinfo(pid, PROC_PIDTASKINFO=4, 0, buffer, 96)`, typed as
`(c_int, c_int, c_uint64, c_void_p, c_int) -> c_int` with `use_errno=True`.
The SDK-derived `proc_taskinfo` layout is six `uint64` fields followed by twelve
`int32` fields: size 96, alignment 8, VMS offset 0, RSS 8, policy 48, priority 92.
These are checked in Python and independently by compiled C against the SDK.
Only an exact 96-byte return is success. Errno is captured immediately after
the native call. Short/failed reads raise `MemoryAccountingReadError` retaining
PID, returned bytes and errno; they never become zero usage. Missing library,
symbol or matching layout raises `MemoryAccountingUnavailable`, with the
original loader/symbol exception chained where applicable.

The caller must exclusively own this child's unreaped PID for the entire call
and serialize sampling with waiting/reaping. An already exited child, or a
failed read followed by `poll()` confirming exit, returns `None`. A failed read
while the child remains live raises, even for `ESRCH`. Never query a PID again
after its ownership ends. The sampler neither kills nor reaps a live child,
drains pipes, releases input, polls periodically nor enforces a budget.

Linux and other accounting backends are explicitly refused, including for
already exited handles. In particular, bwrap's outer `Popen.pid` can identify a
monitor, not the command. Neither that PID nor one descendant is whole-job
accounting. Mac Seatbelt continues to deny creating processes and allows exec;
on Linux inherited AS ceilings apply separately to each descendant and do not
bound their sum. No cgroup backend is provided.

RSS is not physical footprint. Samples can miss intervening usage; there is no
unqualified peak or hard resident guarantee. A future parent supervisor must
record its own valid sampled-RSS exceedance before claiming that observation
caused termination. A source-raised `MemoryError` and self-sent signal are not
such observations. `ENOMEM` from the accounting API can describe its buffer
failure and is not evidence of target memory exhaustion.

## Evidence and validation limits

Tested host: unprivileged UID 501, macOS 26.6.2 (25G83), arm64, Python 3.12.10;
ABI checks use installed macosx26.5 SDK headers `libproc.h`, `sys/proc_info.h` and
`sys/resource.h`. libproc explicitly calls these interfaces private and subject
to change. Successful local reads and matching layouts do not promise unlimited
OS/architecture compatibility.

The reproduced research (`/tmp/aad-memory-research.sVoDEX/REPORT.md`, independently
rerun under `/private/tmp/aad-memory-repro.zvd7s73t`) observed VMS around 420 GB
with RSS in tens of MB. Installing absolute 64 MiB and 2 GiB AS limits failed
before allocations. CPython reported `ValueError: current limit exceeds maximum
limit`; native calls reported `EINVAL`. These are observed setup failures, not
kernel maxima or proof that requested soft exceeded requested hard. The
low-level 1 TiB installation/inheritance/raise test allocates no such amount;
the product uses a temporary 2 TiB startup ceiling and then lowers it relative
to the worker's measured VMS. The installed SDK aliases `RLIMIT_RSS` to
`RLIMIT_AS`, supplying no separate RSS limiter. The research's privileged
physical-footprint setter returned `KERN_NO_ACCESS`; no hard resident/physical
mechanism is available in this tested unprivileged boundary.

Run from the repository with an external test environment to avoid root/lock
changes:

```sh
test_env=$(mktemp -d /tmp/aad-memory-tests.XXXXXX)
UV_PROJECT_ENVIRONMENT="$test_env/venv" PYTHONDONTWRITEBYTECODE=1 \
  uv run --frozen --python 3.12 --all-packages python -B -m pytest \
  -p no:cacheprovider -q packages/engine/tests
```

The memory tests compile a small C probe under a temporary directory and run
payloads through `sandbox.popen`, with 10-second communication deadlines and
bounded kill/drain/reap on failure. They check exact installation/readback,
failure-before-exec, stricter inheritance, unchanged parent limits, same-PID
exec inheritance, unprivileged hard-cap raise rejection, raw Mac accounting,
SDK ABI/short/error reads, kernel-free imports, default-launch preservation and
unchanged OS denials. Explicit `current VMS + 32 MiB` is **test setup only** for
native mmap/Python mmap/bytearray rejection, paired with no-cap controls. Each
allocation child requests at most 8 + 96 = 104 MiB additional memory; the
sampling child uses 64 MiB. No supervisor is tested or claimed by this package.

Functional Linux AS tests use real bwrap and fail if it is absent; they do not
skip to obtain green CI. Ubuntu CI runs that bwrap path. Only Mac-specific
accounting/setup/Seatbelt tests are marked Mac-only. This local run was on Mac
and does not report a Linux result.

On the tested Mac, the focused memory/build/sandbox/preview/platform/guide run
passed 146 tests in 90.07 s with no skips. It exercised the relative 32 MiB
worker budget: an 8 MiB mapping succeeded and a touched 96 MiB mapping failed
with `ENOMEM`. The 64 MiB and 2 GiB absolute setup failures were also
reproduced before target execution. After adding OOM propagation regressions,
the focused spec/process/2D/ops run passed 102 tests in 2.20 s. A real fallback
scene child and a real operation worker under a 4 GiB configured budget each
passed in a separate test. After tightening startup OOM detection and updating
the config CLI expectation, the focused CLI/memory/build/sandbox/ops/guide run
passed 145 tests in 93.76 s. These results cover this Mac only; Linux
enforcement and independent PR review are not claimed.
