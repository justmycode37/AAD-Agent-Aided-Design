"""OS command boundaries. Capability failures never select an unsandboxed command.

This module does not import the kernel or execute a model. Callers launch the
returned command with ``cwd=job_dir``, ``env=environment(job_dir)`` and closed
file descriptors. The runner, timeout and memory policy are separate layers.
"""

from __future__ import annotations

import json
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .memory import AddressSpaceBudget


class SandboxUnavailable(RuntimeError):
    """No supported OS boundary could be constructed; do not run the command."""


def popen(args: Sequence[str], job_dir: Path, *, read_paths: Sequence[Path] = (), text: bool = False,
          address_space: AddressSpaceBudget | None = None) -> subprocess.Popen:
    """Launch with the OS policy, sanitized environment, private cwd and pipes.

    There is deliberately no caller-provided environment or shell argument.
    The owner must enforce a timeout and descendant cleanup. address_space is
    an explicit per-process virtual-address ceiling, not a resident budget.
    """
    job = _job_path(job_dir)
    if address_space is not None:
        from .memory import _bootstrap_argv
        args = list(args)
        _validate_argv(args)  # Validate the original as well as the wrapper.
        args = _bootstrap_argv(args, address_space, job)
    argv = command(args, job, read_paths=read_paths)
    return subprocess.Popen(
        argv, cwd=job, env=environment(job), close_fds=True,
        start_new_session=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=text,
    )


def environment(job_dir: Path) -> dict[str, str]:
    """Give the child job-local home/temp/cache paths and no inherited secrets."""
    job = _job_path(job_dir)
    temporary = job / "tmp"
    temporary.mkdir(exist_ok=True)
    return {
        "HOME": str(job),
        "TMPDIR": str(temporary) + os.sep,
        "TMP": str(temporary),
        "TEMP": str(temporary),
        "XDG_CACHE_HOME": str(job / ".cache"),
        "XDG_CONFIG_HOME": str(job / ".config"),
        "PATH": os.defpath,
        "LANG": "en_US.UTF-8" if sys.platform == "darwin" else "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "MPLCONFIGDIR": str(job / ".matplotlib"),
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
    }


def runtime_reads(extra: Sequence[Path] = ()) -> tuple[Path, ...]:
    """Read-only interpreter/runtime paths; never add the user's whole home."""
    paths = [Path(sys.prefix), Path(sys.base_prefix), Path(__file__).parents[2]]
    # uv --with uses an overlay interpreter whose dependencies can live in a
    # separate environment. Include the active package roots, without granting
    # arbitrary sys.path entries such as the working directory or user home.
    paths.extend(Path(p) for p in sys.path if p and Path(p).name in {"site-packages", "dist-packages"})
    paths.extend(Path(p) for p in ("/System", "/usr", "/bin", "/sbin", "/Library/Fonts", "/private/etc"))
    # Editable workspace packages live outside site-packages. Inspect their
    # locations without importing the kernel, renderer or quality evaluator.
    for name in ("aiad_platform", "aiad_render", "aiad_drafting", "aiad_quality"):
        spec = importlib.util.find_spec(name)
        if spec is not None and spec.origin is not None:
            paths.append(Path(spec.origin).parent)
    paths.extend(extra)
    # Keep caller-visible aliases as Linux mount destinations, and their
    # canonical targets for symlinks reached from inside mounted runtimes.
    reads = {candidate for p in paths if p.exists() for candidate in (p.absolute(), p.resolve())}
    return tuple(sorted(reads, key=str))


def command(args: Sequence[str], job_dir: Path, *, read_paths: Sequence[Path] = ()) -> list[str]:
    """Wrap an argv in macOS Seatbelt or Linux bubblewrap, or fail closed.

    ``read_paths`` is a trusted caller's explicit allowlist for dependencies or
    staged assets. The only writable tree is the existing, canonical job dir.
    The macOS policy denies creating subprocesses. Linux permits processes
    inside an isolated PID namespace, which dies with its supervising bwrap.
    """
    _validate_argv(args)
    job = _job_path(job_dir)
    reads = runtime_reads(read_paths)
    if any(path == Path("/") for path in reads):
        raise ValueError("a sandbox read allowlist cannot expose the filesystem root")
    if sys.platform == "darwin":
        executable = shutil.which("sandbox-exec")
        if executable is None:
            raise SandboxUnavailable("macOS sandbox-exec is unavailable; the model was not started")
        return [executable, "-p", macos_profile(job, reads), *args]
    if sys.platform.startswith("linux"):
        executable = shutil.which("bwrap")
        if executable is None:
            raise SandboxUnavailable("Linux bubblewrap (bwrap) is unavailable; the model was not started")
        wrapped = [executable, "--unshare-all", "--die-with-parent", "--new-session", "--cap-drop", "ALL"]
        for path in reads:
            wrapped.extend(["--ro-bind", str(path), str(path)])
        # Restore merged-/usr entry points without exposing their host parents.
        for alias in ("/bin", "/sbin", "/lib", "/lib64"):
            path = Path(alias)
            if path in reads:
                continue
            if path.is_symlink():
                wrapped.extend(["--symlink", os.readlink(path), alias])
            elif path.exists() and path.resolve() not in reads:
                wrapped.extend(["--ro-bind", alias, alias])
        wrapped.extend(["--proc", "/proc", "--dev", "/dev", "--bind", str(job), str(job), "--chdir", str(job), "--", *args])
        return wrapped
    raise SandboxUnavailable(f"No supported filesystem/network sandbox is implemented for {sys.platform}; the model was not started")


def macos_profile(job_dir: Path, read_paths: Sequence[Path]) -> str:
    """Seatbelt policy with resolved paths and escaped literal strings."""
    job = _job_path(job_dir)
    # JSON string escaping covers the SBPL string syntax for these paths.
    quoted = lambda path: json.dumps(str(path), ensure_ascii=False)
    reads = " ".join(f"(subpath {quoted(Path(path).resolve())})" for path in read_paths)
    # FileFinder lists sys.path directories before opening a package. Permit
    # that directory itself, without granting reads of unrelated siblings.
    package_parents = {Path(path).resolve().parent for path in read_paths
                       if (Path(path) / "__init__.py").is_file()}
    search_dirs = " ".join(f"(literal {quoted(path)})" for path in sorted(package_parents, key=str))
    return "\n".join([
        "(version 1)",
        "(deny default)",
        "(allow process-exec)",
        "(allow signal (target self))",
        "(allow sysctl-read)",
        "(allow file-read-metadata)",
        "(allow file-read* (literal \"/\") (literal \"/dev/null\") (literal \"/dev/random\") (literal \"/dev/urandom\"))",
        f"(allow file-read* (subpath {quoted(job)}) {reads} {search_dirs})",
        f"(allow file-write* (subpath {quoted(job)}))",
        # stdin/stdout/stderr are inherited pipes. No network or Mach service
        # access is granted; process-fork is intentionally absent.
    ])


def _validate_argv(args: Sequence[str]) -> None:
    if not args or any(not isinstance(arg, str) or "\0" in arg for arg in args):
        raise ValueError("sandbox command must be a nonempty argv without NUL characters")


def _job_path(job_dir: Path) -> Path:
    job = Path(job_dir).resolve(strict=True)
    if not job.is_dir() or job == Path("/"):
        raise ValueError("job_dir must be an existing directory below the filesystem root")
    return job
