# Internal address-space policy and Mac memory observations

This is one S1 foundation package. Public `BuildRequest` and `run_build` do not
accept or apply this policy. Worker integration belongs with #51; an actual
resident supervisor is still needed. S1 memory budgets are not complete.

## Explicit address-space policy

`aiad.engine.memory.AddressSpaceBudget(bytes)` is an immutable, internal
**per-process virtual-address-space ceiling**, not a RAM, RSS, physical-footprint
or aggregate job cap. `bytes` must be a built-in integer with
`1 <= bytes < sys.maxsize`; booleans, other types, zero, negative and out-of-range
values are rejected. This conservative finite representation range excludes the
tested Mac's infinity sentinel. It is not a kernel maximum or a recommended
budget. There is no default budget or generic memory-bytes setting.

The only launch integration is:

```python
from aiad.engine.memory import AddressSpaceBudget
from aiad.engine import sandbox

# Caller chooses explicit_address_bytes; no measured default is implied.
process = sandbox.popen(argv, job_dir,
                        address_space=AddressSpaceBudget(explicit_address_bytes))
# Caller owns timeout, pipe draining, bounded termination and reaping.
```

Omitting `address_space`, or passing `None`, follows the existing launch path:
identical argv, sanitized environment, working directory, pipes, closed file
descriptors, session creation and OS policy. It adds no bootstrap, setter,
sampler or native library load. Importing the memory module itself imports
neither `resource`, `ctypes`, libproc, workers nor the CAD kernel.

Only the exact `AddressSpaceBudget` type is accepted by the hook. Unknown or
resident-policy objects raise before launch. Unsupported platforms or missing
`resource.RLIMIT_AS`/get/set APIs also raise before launch. No policy request
selects a weaker backend or an unsandboxed fallback.

With a configured policy, the launcher validates both the original argv and the
wrapper. Inside the **existing** Seatbelt/bwrap boundary it starts the current
interpreter with `-I -S -B` and an absolute, trusted `memory.py` path. The
interpreter and bootstrap must be outside the writable job. Isolation excludes
job-directory/user/PYTHONPATH imports; disabling site excludes `.pth` startup.
The bootstrap imports only trusted standard-library code, installs the limit
before executing the target or importing worker/kernel code, then uses
`execvpe` with the original argv and sanitized environment. It never reads stdin
or creates a subprocess. No filesystem/network/process grant is added.

Both soft and hard `RLIMIT_AS` are set in one call to the smallest of the
requested bytes and any finite inherited soft/hard values. This preserves even
a stricter inherited soft limit as the child's hard ceiling. Exact pair
readback is required before exec. The daemon/parent never changes its limits;
there is no `preexec_fn`. Exec preserves this limit on the tested host. An
unprivileged target cannot raise its hard ceiling; this is not a promise for
privileged targets.

Native/Python get/set/exec exceptions keep their type and diagnostic. A readback
mismatch raises `AddressSpaceSetupError` containing both pairs. Either prevents
target execution. `popen` returns the bootstrap process without a setup handshake;
a failed setup can be observed in its stderr/exit, but there is no authenticated
memory-breach receipt. Raw status alone cannot distinguish setup failure,
source-raised `MemoryError`, native signal, explicit exit or allocation failure.
This package does not classify any of those as `OUT_OF_MEMORY`.

Immediately before target exec, the bootstrap restores the `SIGPIPE`, `SIGXFZ`
and `SIGXFSZ` defaults where those signals exist, matching the launcher's
`Popen(restore_signals=True)` behavior. Python startup had changed these to
ignored dispositions; carrying them into a native target could make a producer
survive a signal that normally terminates it. Other inherited dispositions are
preserved. Compiled native signal tests compare budgeted and ordinary launches;
parent dispositions remain unchanged.

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

The reproduced research (`/tmp/aiad-memory-research.sVoDEX/REPORT.md`, independently
rerun under `/private/tmp/aiad-memory-repro.zvd7s73t`) observed VMS around 420 GB
with RSS in tens of MB. Installing absolute 64 MiB and 2 GiB AS limits failed
before allocations. CPython reported `ValueError: current limit exceeds maximum
limit`; native calls reported `EINVAL`. These are observed setup failures, not
kernel maxima or proof that requested soft exceeded requested hard. A 1 TiB
installation/inheritance/raise test allocates no such amount and supplies no
product default. The installed SDK aliases `RLIMIT_RSS` to `RLIMIT_AS`, supplying
no separate RSS limiter. The research's privileged physical-footprint setter
returned `KERN_NO_ACCESS`; no hard resident/physical mechanism is available in
this tested unprivileged boundary.

Run from the repository with an external test environment to avoid root/lock
changes:

```sh
test_env=$(mktemp -d /tmp/aiad-memory-tests.XXXXXX)
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
skip to obtain green CI. Only Mac-specific accounting/setup/Seatbelt tests are
marked Mac-only. Linux runtime remains untested from this Mac and CI's bwrap
gap remains S2 #52/#50. An actual Linux run is required before claiming Linux
evidence.

On the tested Mac, the focused memory/sandbox run passed 36 tests in 45.79 s.
After adding the import-shadowing regression, the full engine command above
passed all 57 tests in 78.76 s, with no skips or probe timeouts. The native
helpers compiled successfully, allocation controls passed, every memory-test
child was reaped, and each memory-test parent-limit check remained unchanged.
These results cover this host only; no Linux execution or independent PR review
is claimed.
