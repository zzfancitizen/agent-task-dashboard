"""Small standard-library boundaries for private state and owned processes."""
from __future__ import annotations

from contextlib import contextmanager
import errno
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

from .protocol import ProtocolError

IS_WINDOWS = os.name == 'nt'


def application_home() -> Path:
    if IS_WINDOWS:
        return Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData' / 'Local'))) / 'Taskboard'
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'Taskboard'
    return Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local' / 'share'))) / 'taskboard'


@contextmanager
def file_lock(fd: int, *, blocking: bool):
    acquired = False
    if IS_WINDOWS:
        import msvcrt
        # msvcrt locks bytes at the current file position, including on an
        # otherwise empty file. Keep one real byte for all participating opens.
        if not os.fstat(fd).st_size:
            os.write(fd, b'\0')
        try:
            while True:
                os.lseek(fd, 0, os.SEEK_SET)
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    acquired = True
                    break
                except OSError as exc:
                    if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                        raise
                    if not blocking:
                        raise ProtocolError('LOCAL_BUSY', 'Another local Taskboard process holds this operation or session lock.') from exc
                    time.sleep(0.05)
            yield
        finally:
            if acquired:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError as exc:
            raise ProtocolError('LOCAL_BUSY', 'Another local Taskboard process holds this operation or session lock.') from exc
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)


def fsync_directory(directory: Path) -> None:
    # Windows does not expose fsync-able directory descriptors. File data is
    # flushed before replace; state uses the ACL inherited from the user home.
    if not IS_WINDOWS:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def executable_argv(argv: list[str]) -> list[str]:
    """Avoid implicit cmd.exe interpretation of Windows batch shims."""
    if not IS_WINDOWS:
        return argv
    executable = shutil.which(argv[0]) or argv[0]
    if Path(executable).suffix.lower() not in {'.cmd', '.bat'}:
        return [executable, *argv[1:]]
    entries = {'codex': '@openai/codex/bin/codex.js', 'claude': '@anthropic-ai/claude-code/cli.js', 'npm': 'npm/bin/npm-cli.js'}
    entry = entries.get(Path(executable).stem.lower())
    script = Path(executable).parent / 'node_modules' / entry if entry else None
    node = shutil.which('node')
    if script and script.is_file() and node and Path(node).suffix.lower() == '.exe':
        return [node, str(script), *argv[1:]]
    raise ProtocolError('UNSAFE_EXECUTABLE', 'Windows 批处理启动器不能安全接收任务参数。请安装原生 CLI，或使用对应的 Node CLI 安装。')


def process_options() -> dict:
    if IS_WINDOWS:
        # Suspend before assigning the Job Object, so a child cannot escape in
        # the interval between CreateProcess and AssignProcessToJobObject.
        return {'creationflags': 0x00000004 | 0x00000200}
    return {'start_new_session': True}


class _WindowsJob:
    """Kill-on-close Job Object, with the suspended primary thread resumed last."""
    def __init__(self, process):
        import ctypes
        from ctypes import wintypes

        class Limits(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong), ('flags', wintypes.DWORD), ('minimum', ctypes.c_size_t), ('maximum', ctypes.c_size_t), ('active', wintypes.DWORD), ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]

        class Counters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [('basic', Limits), ('io', Counters), ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]

        class ThreadEntry(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('usage', wintypes.DWORD), ('thread', wintypes.DWORD), ('owner', wintypes.DWORD), ('priority', wintypes.LONG), ('delta', wintypes.LONG), ('flags', wintypes.DWORD)]

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'CreateJobObjectW': ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            'SetInformationJobObject': ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            'AssignProcessToJobObject': ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            'CloseHandle': ([wintypes.HANDLE], wintypes.BOOL),
            'CreateToolhelp32Snapshot': ([wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
            'Thread32First': ([wintypes.HANDLE, ctypes.POINTER(ThreadEntry)], wintypes.BOOL),
            'Thread32Next': ([wintypes.HANDLE, ctypes.POINTER(ThreadEntry)], wintypes.BOOL),
            'OpenThread': ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            'ResumeThread': ([wintypes.HANDLE], wintypes.DWORD),
        }
        for name, (args, result) in signatures.items():
            getattr(kernel, name).argtypes, getattr(kernel, name).restype = args, result
        self.kernel, self.handle = kernel, kernel.CreateJobObjectW(None, None)
        try:
            limits = ExtendedLimits()
            limits.basic.flags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not self.handle or not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
                raise ctypes.WinError(ctypes.get_last_error())
            snapshot = kernel.CreateToolhelp32Snapshot(0x00000004, 0)
            if snapshot == ctypes.c_void_p(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            resumed = False
            try:
                entry = ThreadEntry()
                entry.size = ctypes.sizeof(entry)
                found = kernel.Thread32First(snapshot, ctypes.byref(entry))
                while found:
                    if entry.owner == process.pid:
                        thread = kernel.OpenThread(0x0002, False, entry.thread)
                        if not thread:
                            raise ctypes.WinError(ctypes.get_last_error())
                        try:
                            if kernel.ResumeThread(thread) == 0xFFFFFFFF:
                                raise ctypes.WinError(ctypes.get_last_error())
                            resumed = True
                        finally:
                            kernel.CloseHandle(thread)
                        break
                    found = kernel.Thread32Next(snapshot, ctypes.byref(entry))
            finally:
                kernel.CloseHandle(snapshot)
            if not resumed:
                raise OSError('Cannot find the suspended provider thread.')
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _group_has_exited(pgid: int) -> bool:
    """Confirm a Darwin EPERM refers only to vanished or zombie members."""
    try:
        result = subprocess.run(['/bin/ps', '-axo', 'pgid=,stat='], stdin=subprocess.DEVNULL, capture_output=True, encoding='ascii', check=True, timeout=5)
        if not result.stdout.strip() or result.stderr:
            return False
        for line in result.stdout.splitlines():
            group, state = line.split()
            if int(group) == pgid and not state.startswith('Z'):
                return False
        return True
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def _signal_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        pass
    except PermissionError as exc:
        # Darwin killpg1 skips SZOMB members and returns EPERM when none can
        # receive the signal. Do not suppress genuine or unverified denials.
        if sys.platform != 'darwin' or exc.errno != errno.EPERM or not _group_has_exited(pgid):
            raise


class ProcessTree:
    def __init__(self, process: subprocess.Popen):
        self.process, self.stopped = process, False
        try:
            self.job = _WindowsJob(process) if IS_WINDOWS else None
        except BaseException as exc:
            # Even failures before a Job handle exists must not leave a
            # suspended child behind. The child has not executed yet.
            process.kill()
            process.wait()
            if isinstance(exc, OSError):
                raise ProtocolError('PROCESS_OWNERSHIP_FAILED', '无法建立受控进程树；执行已停止。') from exc
            raise

    def stop(self) -> None:
        if self.stopped:
            return
        process = self.process
        if self.job:
            self.job.close()
        else:
            _signal_group(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
            # Children can still hold stdout even after the leader exits.
            _signal_group(process.pid, signal.SIGKILL)
        process.wait()
        self.stopped = True
