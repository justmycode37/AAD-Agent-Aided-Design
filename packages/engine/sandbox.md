# OS sandbox and public build runner

`aiad.engine.sandbox.popen(argv, job_dir, read_paths=())` constructs the OS policy,
launches with a sanitized environment, closes other file descriptors, and gives
the child only stdin/stdout/stderr pipes. It never invokes a shell. The caller
owns its timeout, memory budget, pipe handling, and process-group cleanup.

Public `aiad.engine.jobs.run_build` always uses this coupled launcher. There is
no request opt-out, caller environment override, or unsandboxed retry. Missing
or unsupported boundaries and OS launch errors become structured
`Failure(code="SANDBOX_DENIED", phase="sandbox")` results with the observed
reason. Native `sandbox-exec:`/`bwrap:` launch diagnostics are retained in
`kernel_message` and `stderr`. An unexplained worker exit remains `WORKER_CRASH`;
an exit status or signal alone is not evidence of OOM or a kernel cause.

The runner retains request validation, the wall-clock timeout, process-group
kill and bounded pipe draining. It refuses an existing `part.brep`, requires
private nonempty regular job artifacts, canonicalizes admitted paths, validates
decoded checks, and retains diagnostics when rejecting evidence. Model-authored
proof and scene badges cannot attest verification: they stay draft. A separate
trusted geometry validator with evidence bound to inputs/artifacts is still
required. This integration does not add a warm pool or memory budgets.

Model source must not do I/O (`CONTRACTS.md` section 3). Runtime reads and
job-local worker output are allowed by the OS policy, but `project_dir` does
not grant project-file access. A staged `asset(...)` API remains required and
is not implemented here. S2's trusted post-build operations must also adopt
this launcher under [issue #21](https://github.com/justmycode37/AIAD-AI-Aided-Design-/issues/21);
the public model runner does not protect those separate processes.

## macOS protection exercised here

Seatbelt is applied by `/usr/bin/sandbox-exec` with a default-deny profile.
Network and Mach service access have no allow rules. Process creation is denied;
exec of the same process retains the OS policy. Reads cover the interpreter,
installed runtime packages, AIAD package source roots and explicitly granted
assets. Active `site-packages`/`dist-packages` roots are included for tools such
as uv that separate an overlay interpreter from its dependencies; arbitrary
Python search paths are not added. The root directory itself may be read, without granting its children;
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

The public `run_build` integration test also denies outside reads, overwrites,
new files, relative traversal and child creation while a real Box completes;
the parent reloads its BREP and checks validity, solid count and volume. It
checks the private cwd/HOME/temp paths and absent inherited secrets. The
existing 16 standalone policy/boundary cases remain. Cleanup fixtures use
explicitly trusted test `Popen` processes outside the model launcher to exercise
descendants after leader exit and orphaned pipes from a detached descendant;
production isolation is not relaxed to run those fixtures.

### Observed editable dependency limit

On macOS 26.6.2 / Python 3.12.10, a public Box build with `preview=True` and all
workspace packages installed returned a draft BREP but no preview:
`render_unavailable` / `ModuleNotFoundError: No module named 'aiad_render'`.
The parent resolved the editable renderer package, while a sandboxed import
probe returned no module. A directory-read probe got `PermissionError:
[Errno 1] Operation not permitted` for `packages/render/src`; the enclosed
`aiad_render` package directory was readable. The launcher's editable
optional-package discovery needs a follow-up. This probe does not establish
the behavior of installed wheels, and no project-wide read grant is added here.

## Other platforms and limits

Linux argv uses bubblewrap: read-only runtime mounts at supplied aliases and
canonical targets, one writable job mount, private PID/network/user and other
namespaces, no capabilities, and a supervisor that dies with its parent.
Missing bwrap raises `SandboxUnavailable`, which `run_build` converts to the
structured sandbox failure above. A native launch can also be rejected; its
actual diagnostic must be retained without guessing which host configuration
caused it. Linux execution is not verified by this Mac run or by simulated
native-error tests. S2 must install the `bubblewrap` package (providing `bwrap`)
in both Linux package and e2e CI jobs under
[issue #50](https://github.com/justmycode37/AIAD-AI-Aided-Design-/issues/50).
The public functional build tests remain enabled on Linux and will fail if the
boundary cannot launch; passing argv tests or skipped Mac cases do not replace
that evidence.

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
