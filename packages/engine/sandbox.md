# OS sandbox and public build runner

`aad.engine.sandbox.popen(argv, job_dir, read_paths=(), address_space=None)`
constructs the OS policy, launches with a sanitized environment, closes other
file descriptors, and gives the child only stdin/stdout/stderr pipes. It never
invokes a shell. An explicit `AddressSpaceBudget` installs a child-only
`RLIMIT_AS`; timeout, pipe handling and process-group cleanup remain with the
caller.

Public `aad.engine.jobs.run_build` always uses this coupled launcher and a
platform-default virtual-address growth budget when one is not supplied. The
platform passes the configured `engine.memory` budget. The bootstrap starts
Python under a temporary absolute ceiling, then the worker sets the configured
growth budget relative to its starting VMS before loading the model. There is
no request opt-out, caller environment override, or unsandboxed retry. Missing
or unsupported boundaries and OS launch errors become structured
`Failure(code="SANDBOX_DENIED", phase="sandbox")` results with the observed
reason. Native `sandbox-exec:`/`bwrap:` launch diagnostics are retained in
`kernel_message` and `stderr`. Explicit `MemoryError`/`ENOMEM` results become
`OUT_OF_MEMORY`; an unexplained worker exit remains `WORKER_CRASH`, because an
exit status or signal alone is not evidence of OOM or a kernel cause.

The runner retains request validation, the wall-clock timeout, process-group
kill and bounded pipe draining. It refuses an existing `part.brep`, requires
private nonempty regular job artifacts, canonicalizes admitted paths, validates
decoded checks, and retains diagnostics when rejecting evidence. Model-authored
proof and scene badges cannot attest verification: they stay draft. A separate
trusted geometry validator with evidence bound to inputs/artifacts is still
required. A warm pool and aggregate resident-memory budget remain unimplemented.
The default growth budget is 16 GiB on Linux and macOS. It limits virtual
address growth rather than RSS, and Linux child processes each receive their
own budget.

The platform applies the configured memory growth budget to trusted post-build
operations too. The optional preview and fallback scene tessellator receive the
same budget. The graphics-dependent render operation uses the trusted memory
bootstrap without Seatbelt because that profile denies VTK graphics.

Model source must not do I/O (`CONTRACTS.md` section 3). Runtime reads and
job-local worker output are allowed by the OS policy, but `project_dir` does
not grant general project-file access. For a `matches_reference` requirement,
the trusted parent resolves only the declared supported-format file, rejects
parent traversal and symlinks, and adds that exact file to the read-only
allowlist. The model and checker still share one process, so this grant does
not hide the reference from model code; the geometric-only honesty rule still
requires that the reference was not traced into the model. General project
assets reach model code through `asset(name)`: when the source calls it, the
trusted parent copies regular files from `<project>/imports/` into
`<job>/assets/` before launch, skipping symlinks and files over 256 MiB. The
sandbox grants no other project read. The copies are removed before the job
becomes a revision, so a large import does not grow with every build. Trusted
post-build operations use the same configured address-space policy; rendering
keeps its existing graphics-specific launch path.

## macOS protection exercised here

Seatbelt is applied by `/usr/bin/sandbox-exec` with a default-deny profile.
Network and Mach service access have no allow rules. Process creation is denied;
exec of the same process retains the OS policy. Reads cover the interpreter,
installed runtime packages, AAD package source roots and explicitly granted
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

### Editable packages and bounded optional previews

Python's package finder must list the directory containing an editable package.
The Mac policy permits reading that directory itself, while retaining the
subtree grant only for the package. A native regression imports a fixture package
and confirms that a sibling private file remains unreadable. No project-wide
read grant is added.

The model worker does not import the native renderer. After the model exits,
the parent creates a fresh private preview directory and starts another process
through `sandbox.popen`. That process reloads only the BREP and context metadata;
it does not execute `model.py` or import Python modules from the model job. Its
read grant covers the BREP, and its only writable directory is the preview job.
The optional preview gets at most 10 seconds, further bounded by the remaining
build timeout. This is a configured optional-work budget, not a measured renderer
limit. A timeout, native exit, missing hook, malformed output or launch failure
becomes a warning and cannot discard geometry, checks or the original failure.
Accepted preview files must be private regular files with a PNG signature.
Preview results cannot promote a build's status or prove requirements.

On macOS 26.6.2 / Python 3.12.10, an import trace reached the native extension
`vtkmodules.vtkIOXdmf2` without returning before the diagnostic budget expired.
An earlier public probe timed out during renderer import and retained its BREP.
The cause of that native hang is unknown. S2 separately observed a native render
abort in [issue #56](https://github.com/justmycode37/AAD-Agent-Aided-Design/issues/56).
No graphics, network or Mach exception is added in response to either observation.
The separate preview worker bounds their effect; successful native preview
rendering inside this Mac policy remains unproven. Scene generation still runs
inside the model worker and has no separate optional-step timeout yet.

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
[issue #50](https://github.com/justmycode37/AAD-Agent-Aided-Design/issues/50).
The public functional build tests remain enabled on Linux and will fail if the
boundary cannot launch; passing argv tests or skipped Mac cases do not replace
that evidence.

Windows currently raises SandboxUnavailable. Job Objects alone would not enforce
the filesystem/network boundary; a Windows implementation and runner evidence
remain required. A warm worker pool remains unimplemented.
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
