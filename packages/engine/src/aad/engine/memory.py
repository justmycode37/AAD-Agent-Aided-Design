"""Internal address-space policy and optional per-process Mac observations.

No kernel, resource setter or native accounting library is loaded on import.
The private limit installer is for a fresh sandboxed bootstrap, never a parent.
See ``packages/engine/memory.md`` for guarantees and tested-host limitations.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
import os
from pathlib import Path
import re
import sys
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import subprocess


@dataclass(frozen=True, slots=True)
class AddressSpaceBudget:
    """Explicit per-process virtual-address ceiling in bytes, not RAM/RSS."""

    bytes: int

    def __post_init__(self) -> None:
        if type(self.bytes) is not int:
            raise TypeError("address-space bytes must be an integer, not bool")
        # Stay in the finite signed platform-integer range. In particular,
        # sys.maxsize is RLIM_INFINITY on the tested 64-bit Mac, not a cap.
        if not 0 < self.bytes < sys.maxsize:
            raise ValueError(f"address-space bytes must be between 1 and {sys.maxsize - 1}")


_GIB = 1024 ** 3
_TIB = 1024 ** 4
SETUP_FAILURE_MARKER = b"AAD_MEMORY_SETUP_FAILURE:"
_SIZE_PATTERN = re.compile(r"[ \t]*([0-9]+)[ \t]*(B|KiB|MiB|GiB|TiB)?[ \t]*", re.IGNORECASE)
_SIZE_MULTIPLIERS = {"b": 1, "kib": 1024, "mib": 1024 ** 2, "gib": _GIB, "tib": _TIB}


def default_memory_budget_bytes() -> int:
    """Return a per-worker virtual-address growth budget for large CAD jobs."""
    if sys.platform == "darwin":
        return 16 * _GIB
    if sys.platform.startswith("linux"):
        return 16 * _GIB
    raise AddressSpaceUnavailable(f"Address-space policy is unsupported on {sys.platform}")


def memory_budget_bytes(value: str | int | None = None) -> int:
    """Resolve ``auto`` or a byte count/size setting to additional VAS bytes."""
    if value is None or value == "auto":
        return default_memory_budget_bytes()
    if type(value) is int:
        AddressSpaceBudget(value)
        return value
    if not isinstance(value, str):
        raise TypeError("memory limit must be 'auto', an integer byte count, or a byte-size string")
    match = _SIZE_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError("memory limit must be 'auto', a positive byte count, or a positive size using B, KiB, MiB, GiB or TiB")
    amount = int(match.group(1))
    unit = (match.group(2) or "B").lower()
    result = amount * _SIZE_MULTIPLIERS[unit]
    AddressSpaceBudget(result)
    return result


def bootstrap_address_space_budget(memory_limit_bytes: int) -> AddressSpaceBudget:
    """Return a temporary absolute ceiling high enough to start a worker Python."""
    AddressSpaceBudget(memory_limit_bytes)
    return AddressSpaceBudget(min(sys.maxsize - 1, max(2 * _TIB, memory_limit_bytes + _TIB)))


def install_worker_memory_budget(memory_limit_bytes: int) -> None:
    """Install a budget relative to the started worker's existing VMS.

    This must run inside the worker after Python starts and before model or
    operation code. Relative VMS accounting lets macOS use useful limits even
    though its Python process maps hundreds of GB before loading CAD code.
    """
    try:
        AddressSpaceBudget(memory_limit_bytes)
        resource = _address_resource()
        current_virtual = _process_virtual_bytes()
        ceiling = min(sys.maxsize - 1, current_virtual + memory_limit_bytes)
        inherited = resource.getrlimit(resource.RLIMIT_AS)
        finite_inherited = [value for value in inherited if value != resource.RLIM_INFINITY]
        if finite_inherited:
            ceiling = min(ceiling, *finite_inherited)
        if ceiling <= current_virtual:
            raise AddressSpaceUnavailable(
                f"The inherited address-space ceiling {ceiling} bytes is not above the worker's current VMS {current_virtual} bytes"
            )
        expected = (ceiling, ceiling)
        resource.setrlimit(resource.RLIMIT_AS, expected)
        actual = resource.getrlimit(resource.RLIMIT_AS)
        if actual != expected:
            raise AddressSpaceSetupError(f"RLIMIT_AS readback: expected {expected!r}, got {actual!r}")
    except BaseException as exc:
        _write_setup_failure_marker(exc)
        raise


def _process_virtual_bytes() -> int:
    if sys.platform == "darwin":
        return _read_mac_memory(os.getpid())[1]
    if sys.platform.startswith("linux"):
        fields = Path("/proc/self/statm").read_text(encoding="ascii").split()
        if not fields:
            raise AddressSpaceUnavailable("/proc/self/statm returned no virtual-size field")
        return int(fields[0]) * int(os.sysconf("SC_PAGE_SIZE"))
    raise AddressSpaceUnavailable(f"Address-space accounting is unsupported on {sys.platform}")


def _write_setup_failure_marker(exc: BaseException) -> None:
    diagnostic = f"{type(exc).__name__}: {exc}".encode("utf-8", errors="backslashreplace")
    try:
        os.write(1, SETUP_FAILURE_MARKER + diagnostic + b"\n")
    except OSError:
        pass


def is_memory_exhaustion(exc: BaseException) -> bool:
    """Identify explicit allocation failures without guessing from process exit."""
    import errno

    pending = [exc]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        if isinstance(current, MemoryError):
            return True
        if isinstance(current, OSError) and current.errno == errno.ENOMEM:
            return True
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return False


def is_memory_exhaustion_output(output: bytes | str) -> bool:
    """Recognize exception lines that identify an actual allocation failure.

    A killed worker with no such diagnostic remains WORKER_CRASH; signal-only
    exits are not enough to claim OOM.
    """
    import errno

    text = output.decode(errors="replace") if isinstance(output, bytes) else output
    for line in text.splitlines():
        candidate = line.strip()
        if re.fullmatch(r"MemoryError(?::.*)?", candidate):
            return True
        match = re.match(r"OSError:\s*\[Errno ([0-9]+)\](?:\s|$)", candidate)
        if match is not None and int(match.group(1)) == errno.ENOMEM:
            return True
    return False


def has_setup_failure_marker(output: bytes | str) -> bool:
    data = output.encode() if isinstance(output, str) else output
    return any(line.startswith(SETUP_FAILURE_MARKER) for line in data.splitlines())


class AddressSpaceUnavailable(RuntimeError):
    """This backend cannot install the requested address-space policy."""


class AddressSpaceSetupError(RuntimeError):
    """The installed address-space pair did not match its expected readback."""


def _address_resource():
    if sys.platform != "darwin" and not sys.platform.startswith("linux"):
        raise AddressSpaceUnavailable(f"Address-space policy is unsupported on {sys.platform}")
    try:
        import resource
    except ImportError as exc:
        raise AddressSpaceUnavailable("Address-space policy requires the resource module") from exc
    if (not hasattr(resource, "RLIMIT_AS") or not hasattr(resource, "RLIM_INFINITY")
            or not callable(getattr(resource, "getrlimit", None))
            or not callable(getattr(resource, "setrlimit", None))):
        raise AddressSpaceUnavailable("Address-space policy requires RLIMIT_AS, getrlimit and setrlimit")
    return resource


def _bootstrap_argv(args: list[str], budget: AddressSpaceBudget, job: Path) -> list[str]:
    if type(budget) is not AddressSpaceBudget:
        raise TypeError("address_space accepts only AddressSpaceBudget or None")
    AddressSpaceBudget(budget.bytes)
    _address_resource()  # Capability check only; never get/set parent limits.
    bootstrap = Path(__file__).resolve(strict=True)
    interpreter = Path(sys.executable).resolve(strict=True)
    if bootstrap.is_relative_to(job) or interpreter.is_relative_to(job):
        raise AddressSpaceUnavailable("The address-space bootstrap and interpreter must be outside the writable job")
    # -I excludes cwd, PYTHONPATH and user site; -S excludes site/.pth startup.
    # Execute this trusted file directly, without importing aad or job modules.
    return [str(interpreter), "-I", "-S", "-B", str(bootstrap), str(budget.bytes), *args]


def _exec_address_space(budget: AddressSpaceBudget, args: list[str]) -> None:
    """CHILD ONLY: install both limits, verify, then replace this process."""
    if not args:
        raise ValueError("the address-space bootstrap requires a target argv")
    import signal  # Load trusted stdlib code before installing a tight cap.

    resource = _address_resource()
    inherited = resource.getrlimit(resource.RLIMIT_AS)
    ceiling = min([budget.bytes, *(value for value in inherited if value != resource.RLIM_INFINITY)])
    expected = (ceiling, ceiling)
    # Leave native/Python exceptions intact, including EINVAL's generic
    # ValueError. Failure at either read or the setter must never reach exec.
    resource.setrlimit(resource.RLIMIT_AS, expected)
    actual = resource.getrlimit(resource.RLIMIT_AS)
    if actual != expected:
        raise AddressSpaceSetupError(f"RLIMIT_AS readback: expected {expected!r}, got {actual!r}")
    _restore_exec_signals(signal)
    os.execvpe(args[0], args, os.environ)


def _restore_exec_signals(signal) -> None:
    # Match Popen(restore_signals=True) after this intermediate Python image
    # changed defaults to SIG_IGN. Other inherited dispositions stay intact.
    for name in ("SIGPIPE", "SIGXFZ", "SIGXFSZ"):
        number = getattr(signal, name, None)
        if number is not None:
            signal.signal(number, signal.SIG_DFL)


@dataclass(frozen=True, slots=True)
class ProcessMemorySample:
    """One PID's current bytes; timestamp_ns is monotonic, taken after read."""

    pid: int
    timestamp_ns: int
    resident_bytes: int
    virtual_bytes: int


class MemoryAccountingUnavailable(RuntimeError):
    """No supported per-process accounting backend is available."""


class MemoryAccountingReadError(OSError):
    """An owned live process could not be sampled; this is not OOM evidence."""

    def __init__(self, pid: int, returned_bytes: int, error: int):
        self.pid = pid
        self.returned_bytes = returned_bytes
        super().__init__(error, f"proc_pidinfo({pid}, PROC_PIDTASKINFO) returned {returned_bytes} bytes, expected 96; {os.strerror(error)}")


@cache
def _mac_taskinfo():
    if sys.platform != "darwin":
        raise MemoryAccountingUnavailable(f"Per-process accounting is unsupported on {sys.platform}")
    import ctypes as C

    # macosx26.5 SDK sys/proc_info.h; checked against compiled C sizeof,
    # alignment and offsetof. libproc is a private, changeable interface.
    class ProcTaskInfo(C.Structure):
        _fields_ = [(name, C.c_uint64) for name in (
            "pti_virtual_size", "pti_resident_size", "pti_total_user",
            "pti_total_system", "pti_threads_user", "pti_threads_system",
        )] + [(name, C.c_int32) for name in (
            "pti_policy", "pti_faults", "pti_pageins", "pti_cow_faults",
            "pti_messages_sent", "pti_messages_received", "pti_syscalls_mach",
            "pti_syscalls_unix", "pti_csw", "pti_threadnum", "pti_numrunning", "pti_priority",
        )]

    if (C.sizeof(ProcTaskInfo) != 96 or C.alignment(ProcTaskInfo) != 8
            or tuple(getattr(ProcTaskInfo, field).offset for field in (
                "pti_virtual_size", "pti_resident_size", "pti_policy", "pti_priority",
            )) != (0, 8, 48, 92)):
        raise MemoryAccountingUnavailable("Unsupported proc_taskinfo ABI")
    try:
        library = C.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        reader = library.proc_pidinfo
    except (OSError, AttributeError) as exc:
        raise MemoryAccountingUnavailable("libproc PROC_PIDTASKINFO is unavailable") from exc
    reader.argtypes = [C.c_int, C.c_int, C.c_uint64, C.c_void_p, C.c_int]
    reader.restype = C.c_int
    return C, ProcTaskInfo, reader


def _read_mac_memory(pid: int) -> tuple[int, int]:
    C, info_type, reader = _mac_taskinfo()
    info = info_type()
    C.set_errno(0)
    returned = reader(pid, 4, 0, C.byref(info), 96)
    error = C.get_errno()  # Preserve immediately, before clocks/poll/formatting.
    if returned != 96:
        raise MemoryAccountingReadError(pid, returned, error)
    return int(info.pti_resident_size), int(info.pti_virtual_size)


def sample_process_memory(process: subprocess.Popen) -> ProcessMemorySample | None:
    """Observe an exclusively owned, unreaped Mac child; never supervise it.

    The caller must serialize sampling with waiting/reaping. None means the
    child exited (including an exit/read race), never a zero-byte reading.
    A failed read while poll() still reports live raises with the native errno.
    Linux is refused: a bwrap monitor PID does not represent a whole job.
    """
    if sys.platform != "darwin":
        raise MemoryAccountingUnavailable(f"Per-process accounting is unsupported on {sys.platform}")
    if process.poll() is not None:
        return None
    try:
        resident, virtual = _read_mac_memory(process.pid)
    except MemoryAccountingReadError:
        if process.poll() is not None:
            return None
        raise
    return ProcessMemorySample(process.pid, time.monotonic_ns(), resident, virtual)


if __name__ == "__main__":
    try:
        _exec_address_space(AddressSpaceBudget(int(sys.argv[1])), sys.argv[2:])
    except BaseException as exc:
        # The parent can distinguish a failed policy install from a model
        # crash; this trusted bootstrap writes before any model code runs.
        _write_setup_failure_marker(exc)
        raise
