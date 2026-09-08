import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


PROJECT = Path(__file__).resolve().parents[1]


class DownloadAssetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def builder(self):
        self.assertIsNotNone(importlib.util.find_spec('taskboard.download_assets'), 'runtime builder is missing')
        from taskboard.download_assets import build_download_assets
        return build_download_assets

    def bootstrap(self):
        path = PROJECT / 'launchers/bootstrap.py'
        self.assertTrue(path.is_file(), 'standalone bootstrap is missing')
        spec = importlib.util.spec_from_file_location('download_bootstrap', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def runtime(self, files=None):
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, data in (files or {'taskboard/__init__.py': '', 'taskboard/wizard.py': ''}).items():
                archive.writestr(name, data)
        path = self.root / 'runtime.zip'
        path.write_bytes(output.getvalue())
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def request(self, digest):
        request = {'schema_version': 1, 'hostname': 'github.com', 'repository': 'company/tasks', 'issue': 4, 'revision': 1, 'task_digest': 'a' * 64, 'runtime_sha256': digest, 'action': 'run'}
        path = self.root / 'request.json'
        path.write_text(json.dumps(request))
        return path

    def test_build_runtime_is_deterministic_and_contains_real_source_assets_only(self):
        build = self.builder()
        one, two = self.root / 'one', self.root / 'two'
        manifest = build(one, PROJECT)
        self.assertEqual(build(two, PROJECT), manifest)
        runtime = one / 'downloads/taskboard-runtime.zip'
        self.assertEqual(runtime.read_bytes(), (two / 'downloads/taskboard-runtime.zip').read_bytes())
        self.assertEqual(manifest['sha256'], hashlib.sha256(runtime.read_bytes()).hexdigest())
        self.assertEqual(manifest['size'], runtime.stat().st_size)
        self.assertEqual(json.loads((one / 'downloads/runtime.json').read_text()), manifest)
        with zipfile.ZipFile(runtime) as archive:
            names = set(archive.namelist())
            self.assertTrue({'taskboard/wizard.py', 'bin/taskboard', 'docs/examples/task-v1.json', 'resources/publisher-instructions.md'} <= names)
            self.assertFalse(any(part.startswith('.') or part == '__pycache__' for name in names for part in name.split('/')))
            self.assertEqual(archive.getinfo('bin/taskboard').external_attr >> 16 & 0o777, 0o755)

    def test_bootstrap_refuses_changed_runtime_before_creating_cache(self):
        bootstrap = self.bootstrap()
        runtime, digest = self.runtime()
        runtime.write_bytes(runtime.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            bootstrap.extract_runtime(runtime, digest, self.root / 'cache')
        self.assertFalse((self.root / 'cache').exists())

    def test_safe_extraction_rejects_traversals_symlinks_duplicate_and_windows_paths(self):
        bootstrap = self.bootstrap()
        for filename in ('../outside', '/absolute', 'C:/bad', 'taskboard\\bad.py', 'taskboard/CON', 'taskboard/file:stream', 'taskboard/./bad'):
            with self.subTest(filename=filename):
                runtime, digest = self.runtime({'taskboard/__init__.py': '', filename: 'bad'})
                with self.assertRaises(ValueError):
                    bootstrap.extract_runtime(runtime, digest, self.root / 'cache')
        runtime, _ = self.runtime()
        with zipfile.ZipFile(runtime, 'a') as archive:
            info = zipfile.ZipInfo('taskboard/link')
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, 'elsewhere')
        with self.assertRaises(ValueError):
            bootstrap.extract_runtime(runtime, hashlib.sha256(runtime.read_bytes()).hexdigest(), self.root / 'cache')
        runtime, _ = self.runtime({'taskboard/__init__.py': '', 'taskboard/WIZARD.py': '', 'taskboard/wizard.py': ''})
        with self.assertRaises(ValueError):
            bootstrap.extract_runtime(runtime, hashlib.sha256(runtime.read_bytes()).hexdigest(), self.root / 'cache')
        self.assertFalse((self.root / 'outside').exists())

    def test_cached_runtime_tampering_is_rejected_without_running_it(self):
        bootstrap = self.bootstrap()
        runtime, digest = self.runtime()
        installed = bootstrap.extract_runtime(runtime, digest, self.root / 'cache')
        (installed / 'taskboard/wizard.py').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'cache|缓存'):
            bootstrap.extract_runtime(runtime, digest, self.root / 'cache')

    def test_real_bootstrap_dispatches_json_path_using_verified_extracted_runtime(self):
        bootstrap = self.bootstrap()
        result = self.root / 'result.json'
        wizard = 'import json,sys,pathlib\nrequest=pathlib.Path(sys.argv[1])\nrequest.with_name("result.json").write_text(json.dumps({"request":json.loads(request.read_text()),"runtime":str(pathlib.Path(__file__).parent),"utf8_mode":sys.flags.utf8_mode}))\n'
        _, digest = self.runtime({'taskboard/__init__.py': '', 'taskboard/wizard.py': wizard})
        request = self.request(digest)
        with patch.object(bootstrap, 'cache_home', return_value=self.root / 'cache'), patch.dict(os.environ, {'PYTHONUTF8': '0'}):
            self.assertEqual(bootstrap.run(request), 0)
        captured = json.loads(result.read_text())
        self.assertEqual(captured['request']['issue'], 4)
        self.assertEqual(Path(captured['runtime']), self.root / 'cache' / digest / 'taskboard')
        self.assertEqual(captured['utf8_mode'], 1)

    def test_unix_starter_handles_spaces_and_preserves_bootstrap_exit_code(self):
        self.bootstrap()
        package = self.root / '任务 folder'
        package.mkdir()
        marker = package / 'ran'
        (package / 'bootstrap.py').write_text('from pathlib import Path\nPath(__file__).with_name("ran").write_text("yes")\nraise SystemExit(7)\n')
        shutil.copyfile(PROJECT / 'launchers/Start-Taskboard.sh', package / 'Start-Taskboard.sh')
        run = subprocess.run(['/bin/sh', str(package / 'Start-Taskboard.sh')], input='\n', text=True, capture_output=True, timeout=10, env={**os.environ, 'PATH': str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH']})
        self.assertEqual(run.returncode, 7, run.stderr)
        self.assertEqual(marker.read_text(), 'yes')

    def test_starter_isolates_bootstrap_imports_from_download_folder_and_pythonpath(self):
        package = self.root / 'download'
        package.mkdir()
        marker = package / 'shadowed'
        (package / 'json.py').write_text('from pathlib import Path\nPath(__file__).with_name("shadowed").write_text("bad")\n')
        (package / 'bootstrap.py').write_text('import json\nraise SystemExit(0)\n')
        shutil.copyfile(PROJECT / 'launchers/Start-Taskboard.sh', package / 'Start-Taskboard.sh')
        result = subprocess.run(['/bin/sh', str(package / 'Start-Taskboard.sh')], input='\n', text=True, capture_output=True, timeout=10, env={**os.environ, 'TASKBOARD_TERMINAL_OPENED': '1', 'PYTHONPATH': str(package)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists(), 'bootstrap imported a file from the unverified download directory')

    def test_unix_starters_enable_utf8_for_bootstrap_input_and_output(self):
        for starter in ('Start-Taskboard.sh', 'Start-Taskboard.command'):
            with self.subTest(starter=starter):
                package = self.root / starter
                package.mkdir()
                script = 'import json,sys\nfrom pathlib import Path\nvalue=sys.stdin.readline().strip()\nPath(__file__).with_name("encoding.json").write_text(json.dumps({"utf8_mode":sys.flags.utf8_mode,"input":value}),encoding="utf-8")\nprint("项目已选择 ✓")\n'
                (package / 'bootstrap.py').write_text(script, encoding='utf-8')
                shutil.copyfile(PROJECT / 'launchers' / starter, package / starter)
                result = subprocess.run(['/bin/sh', str(package / starter)], input='中文项目 ✓\n\n', encoding='utf-8', capture_output=True, timeout=10, env={**os.environ, 'TASKBOARD_TERMINAL_OPENED': '1', 'PYTHONUTF8': '0'})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads((package / 'encoding.json').read_text(encoding='utf-8')), {'utf8_mode': 1, 'input': '中文项目 ✓'})
                self.assertIn('项目已选择 ✓', result.stdout)


if __name__ == '__main__':
    unittest.main()
