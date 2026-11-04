# OS sandbox command boundary

`aiad.engine.sandbox.popen(argv, job_dir, read_paths=())` constructs the OS policy,
launches with a sanitized environment, closes other file descriptors, and gives
the child only stdin/stdout/stderr pipes. It never invokes a shell. The caller
owns its timeout, memory budget, pipe handling, and process-group cleanup.

This PR supplies the boundary as a separate module. The M0 `run_build` path is
not wired to it yet. Do not describe M0 model execution as sandboxed until the
runner integration lands. S2 post-build operations must use the same boundary.

## macOS protection exercised here

Seatbelt is applied by `/usr/bin/sandbox-exec` with a default-deny profile.
Network and Mach service access have no allow rules. Process creation is denied;
exec of the same process retains the OS policy. Reads cover the interpreter,
installed runtime packages, AIAD package source roots and explicitly granted
assets. The root directory itself may be read, without granting its children;
this was needed for the installed Python to start. Metadata reads are permitted.
Only the canonical job directory is writable. The job has private HOME, temp,
Matplotlib config and XDG paths. No parent environment secrets are inherited.

The Mac tests exercise real denial of outside reads/writes, relative traversal,
symlink and hardlink escapes, native libc open, TCP/UDP/bind, subprocess creation,
and policy injection through a quoted job path. They also check same-process
exec inheritance, read-only assets, secret-free child environment, job writes,
and an actual build123d Box build/BREP export. These are regression tests rather
than a proof against every OS vulnerability. No Python AST filter is treated as
a security boundary.

## Other platforms and limits

Linux argv uses bubblewrap: read-only runtime mounts, one writable job mount,
private PID/network/user and other namespaces, no capabilities, and a supervisor
that dies with its parent. Missing bwrap raises SandboxUnavailable. Kernel/user
namespace restrictions may also reject launch; callers must retain that failure
and never retry the original unsandboxed argv. Linux execution is not verified
by this Mac run; CI runner support is requested in GitHub issue #21.

Windows currently raises SandboxUnavailable. Job Objects alone would not enforce
the filesystem/network boundary; a Windows implementation and runner evidence
remain required. This module does not provide memory limits or worker pooling.
Metadata and explicitly allowed system/runtime data remain readable. A trusted
caller controls the extra read allowlist; only pass needed assets/dependencies.

## Reproduce and measure

```sh
uv run --locked --all-packages --all-extras --with pytest --with pytest-timeout \
  python -m pytest packages/engine/tests/test_sandbox.py -q
```

`evidence/sandbox-macos.json` records three fixed Box-build launches with and
without the OS wrapper, their raw wall times, OS/Python versions and exact source.
Both modes use the sanitized environment. These include interpreter/kernel
imports and are not a warm-pool overhead result or a performance bound.

The recorded median delta is negative and smaller than the observed launch-time spread. This small run does not resolve the policy overhead; no speedup is claimed.
