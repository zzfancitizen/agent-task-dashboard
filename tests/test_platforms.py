import importlib.util
import ctypes
import errno
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from taskboard.protocol import ProtocolError


class PlatformTests(unittest.TestCase):
    def platform_module(self):
        self.assertIsNotNone(importlib.util.find_spec('taskboard.platforms'), 'portable platform support is missing')
        from taskboard import platforms
        return platforms

    def test_private_home_uses_native_user_state_directory(self):
        platforms = self.platform_module()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'LOCALAPPDATA': directory}):
            with patch.object(platforms, 'IS_WINDOWS', True):
                self.assertEqual(platforms.application_home(), Path(directory) / 'Taskboard')

    def test_windows_lock_covers_a_real_byte_and_releases_after_exception(self):
        platforms = self.platform_module()
        calls = []
        class WindowsLocks:
            LK_NBLCK, LK_UNLCK = 2, 0
            def locking(self, fd, mode, count):
                calls.append((mode, os.lseek(fd, 0, os.SEEK_CUR), count, os.fstat(fd).st_size))
        with tempfile.TemporaryFile() as stream, patch.object(platforms, 'IS_WINDOWS', True), patch.dict(sys.modules, {'msvcrt': WindowsLocks()}):
            with self.assertRaisesRegex(ValueError, 'inside'):
                with platforms.file_lock(stream.fileno(), blocking=False):
                    raise ValueError('inside')
        self.assertEqual(calls, [(2, 0, 1, 1), (0, 0, 1, 1)])

    def test_windows_batch_arguments_are_not_passed_to_cmd(self):
        platforms = self.platform_module()
        with patch.object(platforms, 'IS_WINDOWS', True), patch.object(platforms.shutil, 'which', return_value='C:/tools/unsafe.cmd'):
            with self.assertRaises(ProtocolError) as caught:
                platforms.executable_argv(['unsafe', '& echo escaped'])
        self.assertEqual(caught.exception.code, 'UNSAFE_EXECUTABLE')

    def test_windows_known_npm_provider_uses_node_and_preserves_literal_argv(self):
        platforms = self.platform_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'node_modules/@openai/codex/bin/codex.js'
            script.parent.mkdir(parents=True)
            script.write_text('')
            paths = {'codex': str(root / 'codex.cmd'), 'node': str(root / 'node.exe')}
            with patch.object(platforms, 'IS_WINDOWS', True), patch.object(platforms.shutil, 'which', side_effect=paths.get):
                self.assertEqual(platforms.executable_argv(['codex', 'exec', '& quoted']), [str(root / 'node.exe'), str(script), 'exec', '& quoted'])

    def test_windows_job_is_assigned_before_suspended_process_can_execute(self):
        platforms = self.platform_module()
        calls = []
        class Function:
            def __init__(self, name, effect):
                self.name, self.effect = name, effect
            def __call__(self, *args):
                calls.append((self.name, args))
                return self.effect(*args)
        kernel = SimpleNamespace()
        responses = {'CreateJobObjectW': lambda *args: 101, 'SetInformationJobObject': lambda handle, kind, limits, size: int(limits._obj.basic.flags == 0x2000), 'AssignProcessToJobObject': lambda *args: 1, 'CreateToolhelp32Snapshot': lambda *args: 102, 'Thread32Next': lambda *args: 0, 'OpenThread': lambda *args: 103, 'ResumeThread': lambda *args: 1, 'CloseHandle': lambda *args: 1}
        def first(snapshot, entry):
            entry._obj.owner, entry._obj.thread = 44, 55
            return 1
        responses['Thread32First'] = first
        for name, response in responses.items():
            setattr(kernel, name, Function(name, response))
        process = SimpleNamespace(pid=44, _handle=66, wait=lambda **kwargs: None, kill=lambda: None)
        with patch.object(platforms, 'IS_WINDOWS', True), patch.object(ctypes, 'WinDLL', return_value=kernel, create=True):
            self.assertTrue(platforms.process_options()['creationflags'] & 4)
            tree = platforms.ProcessTree(process)
            tree.stop()
            tree.stop()
        names = [name for name, args in calls]
        self.assertLess(names.index('AssignProcessToJobObject'), names.index('ResumeThread'))
        self.assertEqual([args for name, args in calls if name == 'AssignProcessToJobObject'], [(101, 66)])
        self.assertEqual([args for name, args in calls if name == 'CloseHandle'].count((101,)), 1)

    def test_windows_failure_before_job_setup_still_stops_suspended_process(self):
        platforms = self.platform_module()
        stopped = []
        process = SimpleNamespace(kill=lambda: stopped.append('killed'), wait=lambda: stopped.append('waited'))
        with patch.object(platforms, 'IS_WINDOWS', True), patch.object(platforms, '_WindowsJob', side_effect=OSError('job denied')):
            with self.assertRaises(ProtocolError):
                platforms.ProcessTree(process)
        self.assertEqual(stopped, ['killed', 'waited'])

    @unittest.skipIf(os.name == 'nt', 'Unix process fixture; Windows Job Object has a native validation gate')
    def test_process_tree_is_stopped_after_parent_exits(self):
        platforms = self.platform_module()
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'escaped'
            code = 'import subprocess,sys;subprocess.Popen([sys.executable,"-c",sys.argv[1]])'
            child = f'import time,pathlib;time.sleep(0.6);pathlib.Path({str(marker)!r}).write_text("bad")'
            process = subprocess.Popen([sys.executable, '-c', code, child], **platforms.process_options())
            tree = platforms.ProcessTree(process)
            process.wait(timeout=5)
            tree.stop()
            subprocess.run([sys.executable, '-c', 'import time;time.sleep(0.8)'], check=True)
            self.assertFalse(marker.exists())

    @unittest.skipUnless(sys.platform == 'darwin', 'Darwin returns EPERM for zombie-only process groups')
    def test_darwin_zombie_only_group_is_reaped_without_a_false_permission_failure(self):
        platforms = self.platform_module()
        process = subprocess.Popen([sys.executable, '-c', 'pass'], **platforms.process_options())
        try:
            deadline = time.monotonic() + 3
            state = ''
            while time.monotonic() < deadline:
                state = subprocess.run(['/bin/ps', '-p', str(process.pid), '-o', 'state='], capture_output=True, text=True, check=True).stdout.strip()
                if state.startswith('Z'):
                    break
                time.sleep(0.01)
            self.assertTrue(state.startswith('Z'), state)
            tree = platforms.ProcessTree(process)
            tree.stop()
            self.assertEqual(process.returncode, 0)
            self.assertTrue(tree.stopped)
        finally:
            process.wait(timeout=5)

    @unittest.skipIf(os.name == 'nt', 'Unix group-signal permission boundary')
    def test_live_group_permission_failure_is_reported_and_cleanup_remains_retryable(self):
        platforms = self.platform_module()
        process = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'], **platforms.process_options())
        tree = platforms.ProcessTree(process)
        try:
            with patch.object(platforms.os, 'killpg', side_effect=PermissionError(errno.EPERM, 'denied')):
                with self.assertRaises(PermissionError):
                    tree.stop()
            self.assertFalse(tree.stopped)
            self.assertIsNone(process.poll())
            tree.stop()
            self.assertIsNotNone(process.returncode)
            self.assertTrue(tree.stopped)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)

    @unittest.skipIf(os.name == 'nt', 'Unix group-signal permission boundary')
    def test_darwin_permission_failure_is_not_masked_by_an_unknown_process_snapshot(self):
        platforms = self.platform_module()
        with patch.object(platforms.sys, 'platform', 'darwin'), patch.object(platforms.os, 'killpg', side_effect=PermissionError(errno.EPERM, 'denied')):
            for output in ('', 'unparseable row with extra fields\n'):
                with self.subTest(output=output), patch.object(platforms.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, output, '')):
                    with self.assertRaises(PermissionError):
                        platforms._signal_group(123456, 9)
            with patch.object(platforms.subprocess, 'run', side_effect=OSError('process inspection unavailable')):
                with self.assertRaises(PermissionError):
                    platforms._signal_group(123456, 9)

    @unittest.skipIf(os.name == 'nt', 'Unix process group fixture')
    def test_term_ignoring_descendant_is_killed_after_leader_exit(self):
        platforms = self.platform_module()
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'escaped'
            child = 'import os,signal,time,pathlib;signal.signal(signal.SIGTERM,signal.SIG_IGN);print(os.getpid(),flush=True);time.sleep(0.6);pathlib.Path(' + repr(str(marker)) + ').write_text("bad")'
            parent = 'import subprocess,sys;subprocess.Popen([sys.executable,"-c",sys.argv[1]])'
            process = subprocess.Popen([sys.executable, '-c', parent, child], stdout=subprocess.PIPE, text=True, **platforms.process_options())
            tree = platforms.ProcessTree(process)
            try:
                self.assertGreater(int(process.stdout.readline()), 0)
                process.wait(timeout=5)
                tree.stop()
                time.sleep(0.8)
                self.assertFalse(marker.exists())
            finally:
                tree.stop()
                process.wait(timeout=5)
                process.stdout.close()


if __name__ == '__main__':
    unittest.main()
