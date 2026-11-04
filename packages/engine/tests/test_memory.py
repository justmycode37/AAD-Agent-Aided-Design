from __future__ import annotations

import ctypes as C
import errno
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from aiad.engine import memory, sandbox

MIB = 1024 * 1024
TIB = 1024 ** 4  # Installation/inheritance test only; no such allocation.


class AddressSpacePolicyTests(unittest.TestCase):
    def test_bytes_are_positive_finite_representable_integers(self) -> None:
        for value in (True, False, 1.0, '1024', None, object()):
            with self.subTest(value=value), self.assertRaises(TypeError):
                memory.AddressSpaceBudget(value)
        for value in (-1, 0, sys.maxsize, sys.maxsize + 1, 1 << 100):
            with self.subTest(value=value), self.assertRaises(ValueError):
                memory.AddressSpaceBudget(value)
        for value in (1, TIB, sys.maxsize - 1):
            self.assertEqual(memory.AddressSpaceBudget(value).bytes, value)

    def test_unsupported_platform_or_resource_api_prevents_launch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(sandbox.subprocess, 'Popen') as launch:
            job = Path(temporary)
            policy = memory.AddressSpaceBudget(TIB)
            with patch.object(memory.sys, 'platform', 'win32'), self.assertRaises(memory.AddressSpaceUnavailable):
                sandbox.popen([sys.executable], job, address_space=policy)
            with patch.dict(sys.modules, {'resource': None}), self.assertRaises(memory.AddressSpaceUnavailable) as caught:
                sandbox.popen([sys.executable], job, address_space=policy)
            self.assertIsInstance(caught.exception.__cause__, ImportError)
            for missing in ('RLIMIT_AS', 'RLIM_INFINITY', 'getrlimit', 'setrlimit'):
                api = dict(RLIMIT_AS=5, RLIM_INFINITY=-1, getrlimit=Mock(), setrlimit=Mock())
                del api[missing]
                with self.subTest(missing=missing), patch.dict(sys.modules, {'resource': SimpleNamespace(**api)}):
                    with self.assertRaises(memory.AddressSpaceUnavailable):
                        sandbox.popen([sys.executable], job, address_space=policy)
            launch.assert_not_called()

    def test_bootstrap_must_be_outside_the_writable_job(self) -> None:
        with patch.object(memory, '_address_resource'):
            with self.assertRaises(memory.AddressSpaceUnavailable):
                memory._bootstrap_argv([sys.executable], memory.AddressSpaceBudget(TIB), Path(memory.__file__).resolve().parent)

    def test_installer_clamps_both_limits_and_execs_only_after_exact_readback(self) -> None:
        for inherited, expected in (((-1, -1), (100, 100)), ((80, 90), (80, 80)), ((-1, 70), (70, 70))):
            with self.subTest(inherited=inherited):
                api = SimpleNamespace(RLIMIT_AS=5, RLIM_INFINITY=-1,
                                      getrlimit=Mock(), setrlimit=Mock())
                events = []
                api.getrlimit.side_effect = lambda _: (events.append('read') or (inherited if len(events) == 1 else expected))
                api.setrlimit.side_effect = lambda *_: events.append('set')
                with patch.object(memory, '_address_resource', return_value=api), \
                        patch.object(memory.os, 'execvpe', side_effect=lambda *_: events.append('exec')) as execute:
                    memory._exec_address_space(memory.AddressSpaceBudget(100), ['target', 'argument'])
                api.setrlimit.assert_called_once_with(5, expected)
                execute.assert_called_once_with('target', ['target', 'argument'], os.environ)
                self.assertEqual(events, ['read', 'set', 'read', 'exec'])

    def test_install_and_read_errors_preserve_exception_and_never_execute(self) -> None:
        for phase in ('initial-read', 'set', 'readback'):
            for error in (ValueError('current limit exceeds maximum limit'), OSError(errno.EINVAL, 'native diagnostic'), OverflowError('representation')):
                with self.subTest(phase=phase, error=type(error).__name__):
                    api = SimpleNamespace(RLIMIT_AS=5, RLIM_INFINITY=-1,
                                          getrlimit=Mock(side_effect=[(-1, -1), (100, 100)]), setrlimit=Mock())
                    if phase == 'initial-read':
                        api.getrlimit.side_effect = error
                    elif phase == 'set':
                        api.setrlimit.side_effect = error
                    else:
                        api.getrlimit.side_effect = [(-1, -1), error]
                    with patch.object(memory, '_address_resource', return_value=api), patch.object(memory.os, 'execvpe') as execute:
                        with self.assertRaises(type(error)) as caught:
                            memory._exec_address_space(memory.AddressSpaceBudget(100), ['target'])
                        self.assertIs(caught.exception, error)
                        execute.assert_not_called()
        api = SimpleNamespace(RLIMIT_AS=5, RLIM_INFINITY=-1,
                              getrlimit=Mock(side_effect=[(-1, -1), (100, 101)]), setrlimit=Mock())
        with patch.object(memory, '_address_resource', return_value=api), patch.object(memory.os, 'execvpe') as execute:
            with self.assertRaisesRegex(memory.AddressSpaceSetupError, r'expected \(100, 100\), got \(100, 101\)'):
                memory._exec_address_space(memory.AddressSpaceBudget(100), ['target'])
            execute.assert_not_called()

    def test_import_and_default_launch_preparation_load_no_kernel_resource_or_ctypes(self) -> None:
        source_root = Path(memory.__file__).resolve().parents[2]
        code = f'''
import builtins, sys, tempfile
from pathlib import Path
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {{'build123d', 'OCP', 'ctypes', 'resource'}}:
        raise AssertionError('unexpected import: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
sys.path.insert(0, {str(source_root)!r})
from aiad.engine import memory, sandbox
assert 'aiad.engine._worker' not in sys.modules
assert 'aiad.engine.jobs' not in sys.modules
with tempfile.TemporaryDirectory() as job:
    sandbox.subprocess.Popen = lambda *args, **kwargs: (args, kwargs)
    args, options = sandbox.popen([sys.executable, '-c', 'pass'], Path(job))
    assert options['stdin'] == sandbox.subprocess.PIPE
print('kernel-free; no resource or ctypes import')
'''
        result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', code], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'kernel-free; no resource or ctypes import')


class AccountingTests(unittest.TestCase):
    def setUp(self) -> None:
        # Build the real SDK-derived layout with a fake library, even on Linux.
        with patch.object(memory.sys, 'platform', 'darwin'), patch.object(C, 'CDLL', return_value=SimpleNamespace(proc_pidinfo=Mock())):
            self.api = memory._mac_taskinfo.__wrapped__()

    def test_valid_read_returns_explicit_byte_fields_and_monotonic_timestamp(self) -> None:
        _, info_type, _ = self.api

        def reader(pid, flavor, arg, buffer, size):
            self.assertEqual((pid, flavor, arg, size), (1234, 4, 0, 96))
            info = C.cast(buffer, C.POINTER(info_type)).contents
            info.pti_resident_size, info.pti_virtual_size = 12 * MIB, 420_000_000_000
            return 96

        process = SimpleNamespace(pid=1234, poll=Mock(return_value=None))
        before = time.monotonic_ns()
        with patch.object(memory.sys, 'platform', 'darwin'), patch.object(memory, '_mac_taskinfo', return_value=(C, info_type, reader)):
            sample = memory.sample_process_memory(process)
        self.assertEqual((sample.pid, sample.resident_bytes, sample.virtual_bytes), (1234, 12 * MIB, 420_000_000_000))
        self.assertLessEqual(before, sample.timestamp_ns)
        self.assertLessEqual(sample.timestamp_ns, time.monotonic_ns())

    def test_short_and_error_reads_never_become_zero_samples_and_keep_errno(self) -> None:
        _, info_type, _ = self.api
        for returned, error in ((0, errno.ENOMEM), (95, 0), (-1, errno.EACCES), (104, 0), (0, errno.ESRCH)):
            with self.subTest(returned=returned, error=error):
                def reader(*_):
                    C.set_errno(error)
                    return returned

                def poll():
                    C.set_errno(errno.EPERM)  # Cannot overwrite the captured native error.
                    return None

                process = SimpleNamespace(pid=1234, poll=poll)
                with patch.object(memory.sys, 'platform', 'darwin'), patch.object(memory, '_mac_taskinfo', return_value=(C, info_type, reader)):
                    with self.assertRaises(memory.MemoryAccountingReadError) as caught:
                        memory.sample_process_memory(process)
                self.assertEqual((caught.exception.pid, caught.exception.returned_bytes, caught.exception.errno), (1234, returned, error))

    def test_exited_child_and_exit_read_race_are_no_sample(self) -> None:
        process = SimpleNamespace(pid=1234, poll=Mock(return_value=0))
        with patch.object(memory.sys, 'platform', 'darwin'), patch.object(memory, '_read_mac_memory') as read:
            self.assertIsNone(memory.sample_process_memory(process))
            read.assert_not_called()
        process.poll = Mock(side_effect=[None, 0])
        with patch.object(memory.sys, 'platform', 'darwin'), \
                patch.object(memory, '_read_mac_memory', side_effect=memory.MemoryAccountingReadError(1234, 0, errno.ESRCH)):
            self.assertIsNone(memory.sample_process_memory(process))

    def test_linux_and_other_backends_are_refused_without_querying_the_pid(self) -> None:
        process = SimpleNamespace(pid=1234, poll=Mock())  # Could be a bwrap monitor.
        for platform in ('linux', 'win32'):
            with self.subTest(platform=platform), patch.object(memory.sys, 'platform', platform), patch.object(memory, '_read_mac_memory') as read:
                with self.assertRaises(memory.MemoryAccountingUnavailable):
                    memory.sample_process_memory(process)
                process.poll.assert_not_called()
                read.assert_not_called()

    def test_missing_library_symbol_and_abi_fail_explicitly(self) -> None:
        native_error = OSError(errno.ENOENT, 'libproc load failed')
        with patch.object(memory.sys, 'platform', 'darwin'):
            with patch.object(C, 'CDLL', side_effect=native_error):
                with self.assertRaises(memory.MemoryAccountingUnavailable) as caught:
                    memory._mac_taskinfo.__wrapped__()
                self.assertIs(caught.exception.__cause__, native_error)
            with patch.object(C, 'CDLL', return_value=SimpleNamespace()):
                with self.assertRaises(memory.MemoryAccountingUnavailable) as caught:
                    memory._mac_taskinfo.__wrapped__()
                self.assertIsInstance(caught.exception.__cause__, AttributeError)
            with patch.object(C, 'sizeof', return_value=95), patch.object(C, 'CDLL') as load:
                with self.assertRaisesRegex(memory.MemoryAccountingUnavailable, 'ABI'):
                    memory._mac_taskinfo.__wrapped__()
                load.assert_not_called()


# Kept here so the independent C/SDK check and allocations are reproducible
# without fixtures or a repository binary. All output/build products are in tmp.
NATIVE_PROBE = r'''
#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <unistd.h>
#ifdef __APPLE__
#include <libproc.h>
#endif
#define MIB (1024ULL * 1024ULL)
static struct rlimit limits(void) {
    struct rlimit r;
    if (getrlimit(RLIMIT_AS, &r)) { perror("getrlimit"); exit(3); }
    return r;
}
static uint64_t virtual_bytes(void) {
#ifdef __APPLE__
    struct proc_taskinfo info;
    if (proc_pidinfo(getpid(), PROC_PIDTASKINFO, 0, &info, sizeof(info)) != 96) exit(3);
    return info.pti_virtual_size;
#else
    unsigned long long pages = 0;
    FILE *f = fopen("/proc/self/statm", "r");
    if (!f || fscanf(f, "%llu", &pages) != 1) exit(3);
    fclose(f);
    return pages * (uint64_t)sysconf(_SC_PAGESIZE);
#endif
}
static void show(const char *stage) {
    struct rlimit r = limits();
    printf("{\"stage\":\"%s\",\"pid\":%ld,\"soft\":%llu,\"hard\":%llu}\n",
           stage, (long)getpid(), (unsigned long long)r.rlim_cur, (unsigned long long)r.rlim_max);
    fflush(stdout);
}
static void *allocation(size_t bytes) {
    errno = 0;
    void *p = mmap(NULL, bytes, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    int error = errno;
    printf("{\"stage\":\"mmap\",\"bytes\":%zu,\"ok\":%s,\"errno\":%d}\n", bytes, p == MAP_FAILED ? "false" : "true", error);
    fflush(stdout);
    if (p != MAP_FAILED) {
        volatile char *data = p;
        for (size_t i = 0; i < bytes; i += (size_t)sysconf(_SC_PAGESIZE)) data[i] = 1;
    }
    return p;
}
int main(int argc, char **argv) {
    if (argc != 2) return 2;
#ifdef __APPLE__
    if (!strcmp(argv[1], "abi")) {
        printf("[%zu,%zu,%zu,%zu,%zu,%zu,%d]\n", sizeof(struct proc_taskinfo), _Alignof(struct proc_taskinfo),
               offsetof(struct proc_taskinfo, pti_virtual_size), offsetof(struct proc_taskinfo, pti_resident_size),
               offsetof(struct proc_taskinfo, pti_policy), offsetof(struct proc_taskinfo, pti_priority), PROC_PIDTASKINFO);
        return 0;
    }
#endif
    if (!strcmp(argv[1], "exec")) {
        struct rlimit inherited = limits();
        rlim_t cap = (rlim_t)(virtual_bytes() + 32 * MIB); /* Explicit probe setup only. */
        if (inherited.rlim_cur != RLIM_INFINITY && inherited.rlim_cur < cap) cap = inherited.rlim_cur;
        if (inherited.rlim_max != RLIM_INFINITY && inherited.rlim_max < cap) cap = inherited.rlim_max;
        struct rlimit configured = {cap, cap};
        if (setrlimit(RLIMIT_AS, &configured)) { perror("setrlimit"); return 4; }
        struct rlimit readback = limits();
        if (readback.rlim_cur != cap || readback.rlim_max != cap) return 5;
        show("before-exec");
        execl(argv[0], argv[0], "inherited", NULL);
        perror("exec"); return 6;
    }
    show(argv[1]); /* inherited: no setter before these allocation attempts. */
    if (!strcmp(argv[1], "inherited")) {
        struct rlimit r = limits(); r.rlim_cur++; r.rlim_max++;
        errno = 0;
        int rc = setrlimit(RLIMIT_AS, &r); int error = errno;
        printf("{\"stage\":\"raise-hard\",\"rc\":%d,\"errno\":%d}\n", rc, error);
    }
    void *small = allocation(8 * MIB), *large = allocation(96 * MIB);
    if (small != MAP_FAILED) munmap(small, 8 * MIB);
    if (large != MAP_FAILED) munmap(large, 96 * MIB);
    return 0;
}
'''


@unittest.skipUnless(sys.platform == 'darwin' or sys.platform.startswith('linux'), 'requires a supported OS sandbox')
class SandboxedMemoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.native_directory = tempfile.TemporaryDirectory(prefix='aiad-memory-native-')
        cls.addClassCleanup(cls.native_directory.cleanup)
        root = Path(cls.native_directory.name).resolve()
        source = root / 'probe.c'
        source.write_text(NATIVE_PROBE)
        cls.native = root / 'probe'
        compiler = ['xcrun', 'clang', '-fno-modules'] if sys.platform == 'darwin' else ['cc', '-D_DEFAULT_SOURCE']
        subprocess.run([*compiler, '-std=c11', '-Wall', '-Wextra', '-Werror', str(source), '-o', str(cls.native)],
                       check=True, capture_output=True, timeout=30,
                       env={**os.environ, 'TMPDIR': str(root), 'CLANG_MODULE_CACHE_PATH': str(root / 'clang-cache')})

    def setUp(self) -> None:
        import resource
        self.resource = resource
        self.parent_limits = resource.getrlimit(resource.RLIMIT_AS)
        self.temporary = tempfile.TemporaryDirectory(prefix='aiad-memory-test-')
        self.root = Path(self.temporary.name).resolve()
        self.job = self.root / 'job'
        self.job.mkdir()
        self.children = []

    def tearDown(self) -> None:
        try:
            for process in self.children:
                if process.poll() is None:
                    self.kill(process)
                    process.communicate(timeout=2)
                self.assertIsNotNone(process.returncode)
            self.assertEqual(self.resource.getrlimit(self.resource.RLIMIT_AS), self.parent_limits)
        finally:
            self.temporary.cleanup()

    @staticmethod
    def kill(process) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def launch(self, args, policy=memory.AddressSpaceBudget(TIB), reads=()):
        # Linux must actually launch through bwrap. Missing bwrap is a failure,
        # never a skip or a switch to unsandboxed functional tests.
        process = sandbox.popen(args, self.job, address_space=policy, read_paths=reads)
        self.children.append(process)
        return process

    def finish(self, process, data=None):
        try:
            out, err = process.communicate(data, timeout=10)
        except BaseException:
            self.kill(process)
            process.communicate(timeout=2)
            raise
        return subprocess.CompletedProcess(process.args, process.returncode, out.decode(), err.decode())

    def python(self, source, policy=memory.AddressSpaceBudget(TIB), data=None, args=()):
        return self.finish(self.launch([sys.executable, '-I', '-S', '-B', '-c', source, *args], policy), data)

    def records(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        return [json.loads(line) for line in result.stdout.splitlines()]

    def test_bootstrap_ignores_job_authored_imports_and_keeps_path_exec(self) -> None:
        malicious = "from pathlib import Path; Path('bootstrap-injected').write_text('bad'); raise AssertionError('job import')"
        for name in ('resource.py', 'sitecustomize.py', 'dataclasses.py', 'memory.py'):
            (self.job / name).write_text(malicious)
        fake_package = self.job / 'aiad' / 'engine'
        fake_package.mkdir(parents=True)
        (fake_package / 'memory.py').write_text(malicious)
        result = self.finish(self.launch(['true']))  # Original argv keeps PATH lookup.
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.job / 'bootstrap-injected').exists())

    def test_real_bootstrap_same_pid_exec_inheritance_stdin_argv_and_hard_raise_denial(self) -> None:
        after = '''import json, os, resource, sys
cap = resource.getrlimit(resource.RLIMIT_AS)
failures = []
for pair in [(cap[1]+1, cap[1]+1), (resource.RLIM_INFINITY, resource.RLIM_INFINITY)]:
    try: resource.setrlimit(resource.RLIMIT_AS, pair)
    except (ValueError, OSError) as exc: failures.append([type(exc).__name__, str(exc)])
    else: raise AssertionError('unprivileged target raised its hard limit')
assert 'AIAD_TEST_SECRET' not in os.environ
assert not any(n == 'build123d' or n == 'OCP' or n.startswith('OCP.') for n in sys.modules)
print(json.dumps(dict(pid=os.getpid(), limits=cap, stdin=sys.stdin.read(), failures=failures)))
'''
        before = f'''import json, os, resource, sys
print(json.dumps(dict(pid=os.getpid(), limits=resource.getrlimit(resource.RLIMIT_AS), argv=sys.argv)), flush=True)
os.execv(sys.executable, [sys.executable, '-I', '-S', '-B', '-c', {after!r}])
'''
        with patch.dict(os.environ, {'AIAD_TEST_SECRET': 'fixture'}):
            process = self.launch([sys.executable, '-I', '-S', '-B', '-c', before, 'quotes " and unicode ä'])
        records = self.records(self.finish(process, b'unchanged stdin\n'))
        expected = min([TIB, *(n for n in self.parent_limits if n != self.resource.RLIM_INFINITY)])
        self.assertEqual(records[0]['limits'], [expected, expected])
        self.assertEqual(records[1]['limits'], records[0]['limits'])
        self.assertEqual(records[1]['pid'], records[0]['pid'])
        if sys.platform == 'darwin':
            self.assertEqual(records[0]['pid'], process.pid)
        self.assertEqual(records[0]['argv'], ['-c', 'quotes " and unicode ä'])
        self.assertEqual(records[1]['stdin'], 'unchanged stdin\n')
        self.assertEqual(len(records[1]['failures']), 2)

    def test_real_stricter_inherited_soft_limit_is_preserved_as_hard_cap(self) -> None:
        cap = min([3 * TIB // 4, *(n for n in self.parent_limits if n != self.resource.RLIM_INFINITY)])
        target = 'import json, resource; print(json.dumps(resource.getrlimit(resource.RLIMIT_AS)))'
        wrapped = [sys.executable, '-I', '-S', '-B', str(Path(memory.__file__).resolve()), str(TIB),
                   sys.executable, '-I', '-S', '-B', '-c', target]
        source = f'''import os, resource
resource.setrlimit(resource.RLIMIT_AS, ({cap}, resource.getrlimit(resource.RLIMIT_AS)[1]))
os.execv({sys.executable!r}, {wrapped!r})
'''
        self.assertEqual(self.records(self.python(source)), [[cap, cap]])

    @unittest.skipUnless(sys.platform == 'darwin', 'observed small AS installation failures are Mac-specific')
    def test_small_mac_caps_fail_setup_before_target_execution(self) -> None:
        for cap in (64 * MIB, 2 * 1024 * MIB):
            with self.subTest(cap=cap):
                result = self.python("from pathlib import Path; Path('target-entered').write_text('unsafe')", memory.AddressSpaceBudget(cap))
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.job / 'target-entered').exists())
                self.assertIn('ValueError: current limit exceeds maximum limit', result.stderr)
                self.assertEqual(result.stdout, '')

    def test_native_capped_mmap_survives_exec_and_bounded_no_cap_control(self) -> None:
        capped = self.records(self.finish(self.launch([str(self.native), 'exec'], reads=(self.native,))))
        self.assertEqual(capped[0]['stage'], 'before-exec')
        self.assertEqual(capped[1]['stage'], 'inherited')
        self.assertEqual((capped[0]['pid'], capped[0]['soft'], capped[0]['hard']),
                         (capped[1]['pid'], capped[1]['soft'], capped[1]['hard']))
        self.assertEqual(capped[2], dict(stage='raise-hard', rc=-1, errno=errno.EPERM))
        self.assertEqual([(r['bytes'], r['ok'], r['errno']) for r in capped[3:]],
                         [(8 * MIB, True, 0), (96 * MIB, False, errno.ENOMEM)])
        control = self.records(self.finish(self.launch([str(self.native), 'control'], None, (self.native,))))
        self.assertEqual([(r['bytes'], r['ok']) for r in control[1:]], [(8 * MIB, True), (96 * MIB, True)])

    def test_python_capped_mmap_and_bytearray_have_bounded_no_cap_controls(self) -> None:
        for kind in ('mmap', 'bytearray'):
            for capped in (True, False):
                with self.subTest(kind=kind, capped=capped):
                    source = f'''import json, mmap, os, resource, runpy, sys
if {capped!r}:
    if sys.platform == 'darwin':
        api = runpy.run_path({str(Path(memory.__file__).resolve())!r})
        virtual = api['_read_mac_memory'](os.getpid())[1]
    else:
        with open('/proc/self/statm') as f: virtual = int(f.read().split()[0]) * os.sysconf('SC_PAGE_SIZE')
    # Diagnostic setup, never a product resident-budget calculation.
    cap = min(virtual + {32 * MIB}, resource.getrlimit(resource.RLIMIT_AS)[1])
    resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
    assert resource.getrlimit(resource.RLIMIT_AS) == (cap, cap)
held = []
for size in ({8 * MIB}, {96 * MIB}):
    try:
        data = bytearray(size) if {kind!r} == 'bytearray' else mmap.mmap(-1, size, flags=mmap.MAP_PRIVATE | mmap.MAP_ANON)
    except (MemoryError, OSError) as exc:
        print(json.dumps(dict(bytes=size, ok=False, exception=type(exc).__name__, errno=getattr(exc, 'errno', None))))
    else:
        for offset in range(0, size, 4096): data[offset] = 1
        held.append(data)
        print(json.dumps(dict(bytes=size, ok=True)))
'''
                    records = self.records(self.python(source, memory.AddressSpaceBudget(TIB) if capped else None))
                    self.assertEqual(records[0], dict(bytes=8 * MIB, ok=True))
                    if capped:
                        self.assertEqual(records[1], dict(bytes=96 * MIB, ok=False,
                            exception='MemoryError' if kind == 'bytearray' else 'OSError',
                            errno=None if kind == 'bytearray' else errno.ENOMEM))
                    else:
                        self.assertEqual(records[1], dict(bytes=96 * MIB, ok=True))

    @unittest.skipUnless(sys.platform == 'darwin', 'native libproc accounting and C ABI are Mac-only')
    def test_mac_sdk_abi_raw_errors_and_current_memory_sampling(self) -> None:
        abi = self.finish(self.launch([str(self.native), 'abi'], None, (self.native,)))
        self.assertEqual(self.records(abi), [[96, 8, 0, 8, 48, 92, 4]])
        _, info_type, _ = memory._mac_taskinfo()
        self.assertEqual([C.sizeof(info_type), C.alignment(info_type), *(getattr(info_type, f).offset for f in (
            'pti_virtual_size', 'pti_resident_size', 'pti_policy', 'pti_priority'))], [96, 8, 0, 8, 48, 92])
        source = f'''import mmap, sys
print('ready', flush=True); sys.stdin.buffer.read(1)
data = mmap.mmap(-1, {64 * MIB}, flags=mmap.MAP_PRIVATE | mmap.MAP_ANON)
for i in range(0, {64 * MIB}, 4096): data[i] = 1
print('touched', flush=True); sys.stdin.buffer.read(1)
data.close(); print('unmapped', flush=True); sys.stdin.buffer.read(1)
'''
        process = self.launch([sys.executable, '-I', '-S', '-B', '-c', source], None)

        def stage(expected):
            self.assertTrue(select.select([process.stdout], [], [], 5)[0], 'bounded stage wait expired')
            self.assertEqual(process.stdout.readline().decode().strip(), expected)
            sample = memory.sample_process_memory(process)
            self.assertEqual(sample.pid, process.pid)
            self.assertGreater(sample.resident_bytes, 0)
            self.assertGreater(sample.virtual_bytes, sample.resident_bytes)
            return sample

        baseline = stage('ready')
        _, info_type, reader = memory._mac_taskinfo()
        for pid, flavor, size, expected_error in ((process.pid, 4, 95, errno.ENOMEM),
                                                  (process.pid, 999999, 96, errno.EINVAL),
                                                  (-1, 4, 96, errno.ESRCH)):
            info = info_type()
            C.set_errno(0)
            returned = reader(pid, flavor, 0, C.byref(info), size)
            error = C.get_errno()
            self.assertEqual((returned, error), (0, expected_error))
        process.stdin.write(b't'); process.stdin.flush()
        touched = stage('touched')
        self.assertGreaterEqual(touched.virtual_bytes - baseline.virtual_bytes, 64 * MIB)
        self.assertGreater(touched.resident_bytes - baseline.resident_bytes, 48 * MIB)
        process.stdin.write(b'u'); process.stdin.flush()
        unmapped = stage('unmapped')
        self.assertGreater(touched.resident_bytes - unmapped.resident_bytes, 48 * MIB)
        self.assertLess(baseline.timestamp_ns, touched.timestamp_ns)
        self.assertLess(touched.timestamp_ns, unmapped.timestamp_ns)
        self.records(self.finish(process, b'x'))  # No more output; reap it.
        with patch.object(memory, '_read_mac_memory', side_effect=AssertionError('PID lifetime ended')):
            self.assertIsNone(memory.sample_process_memory(process))

    @unittest.skipUnless(sys.platform == 'darwin', 'Seatbelt no-fork boundary')
    def test_configured_address_space_keeps_filesystem_network_and_fork_denials(self) -> None:
        secret = self.root / 'outside.txt'
        secret.write_text('outside fixture')
        (self.job / 'link').symlink_to(secret)
        source = f'''import ctypes, os, socket, subprocess
from pathlib import Path
for action in [lambda: Path({str(secret)!r}).read_text(),
               lambda: Path({str(secret)!r}).write_text('changed'),
               lambda: Path('link').read_text(),
               lambda: socket.create_connection(('127.0.0.1', 9), timeout=1),
               lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b'x', ('127.0.0.1', 9)),
               lambda: socket.socket().bind(('127.0.0.1', 0)),
               lambda: subprocess.run(['/usr/bin/true'], check=True)]:
    try: action()
    except PermissionError: pass
    else: raise AssertionError('sandbox grant changed')
libc = ctypes.CDLL(None, use_errno=True)
assert libc.open({os.fsencode(secret)!r}, os.O_RDONLY) == -1
Path('allowed.txt').write_text('job write')
print('denials preserved')
'''
        result = self.python(source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'denials preserved')
        self.assertEqual(secret.read_text(), 'outside fixture')
        self.assertEqual((self.job / 'allowed.txt').read_text(), 'job write')
