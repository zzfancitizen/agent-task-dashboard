import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from taskboard.protocol import ProtocolError
from taskboard.workspace import prepare_workspace, export_patch, checked_path, repository_url


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.git('init', '-q', cwd=self.source)
        self.git('config', 'user.email', 'tests@example.invalid', cwd=self.source)
        self.git('config', 'user.name', 'Tests', cwd=self.source)
        (self.source / 'src').mkdir()
        (self.source / 'src' / 'main.py').write_text('value = 1\n')
        (self.source / 'rules.md').write_text('Expected rules\n')
        self.git('add', '.', cwd=self.source)
        self.git('commit', '-qm', 'fixture', cwd=self.source)
        self.commit = self.git('rev-parse', 'HEAD', cwd=self.source).stdout.strip()
        self.spec = {
            'source': {'repository': 'https://git.corp.example/company/source', 'base_commit': self.commit, 'workspace_patch': None, 'write_paths': ['src/']},
            'resources': [{'type': 'git_file', 'repository': 'https://git.corp.example/company/source', 'commit': self.commit, 'path': 'rules.md', 'destination': 'resources/rules.md', 'sha256': hashlib.sha256(b'Expected rules\n').hexdigest()}],
        }
        self.config = {'hostname': 'git.corp.example', 'allowed_repos': ['company/source']}
        original_run = subprocess.run
        def external_boundary(argv, **kwargs):
            if argv[:3] == ['gh', 'repo', 'clone']:
                self.assertEqual(argv[3], 'https://git.corp.example/company/source')
                destination = argv[4]
                return original_run(['git', 'clone', '--no-checkout', str(self.source), destination], **kwargs)
            return original_run(argv, **kwargs)
        self.boundary = patch('taskboard.workspace.subprocess.run', side_effect=external_boundary)
        self.boundary.start()
        self.addCleanup(self.boundary.stop)

    @staticmethod
    def git(*argv, cwd):
        return subprocess.run(['git', *argv], cwd=cwd, check=True, capture_output=True, text=True)

    def prepare(self):
        return prepare_workspace(self.spec, self.config, self.root / 'run')

    def test_pins_source_and_checks_materialized_resource_hash(self):
        (self.source / 'src' / 'main.py').write_text('value = 2\n')
        self.git('commit', '-qam', 'later', cwd=self.source)
        workspace = self.prepare()
        self.assertEqual((workspace / 'src' / 'main.py').read_text(), 'value = 1\n')
        self.assertEqual((workspace / 'resources' / 'rules.md').read_text(), 'Expected rules\n')
        self.assertEqual(self.git('rev-parse', 'HEAD', cwd=workspace).stdout.strip(), self.commit)

    def test_uppercase_resource_hash_is_checked_without_rewriting_spec(self):
        self.spec['resources'][0]['sha256'] = self.spec['resources'][0]['sha256'].upper()
        original = self.spec['resources'][0]['sha256']
        workspace = self.prepare()
        export_patch(self.spec, workspace, self.root / 'changes.patch')
        self.assertEqual((workspace / 'resources/rules.md').read_text(), 'Expected rules\n')
        self.assertEqual(self.spec['resources'][0]['sha256'], original)

    def test_disallowed_repo_and_wrong_host_fail_before_checkout(self):
        for value in ['https://git.corp.example/other/source', 'https://github.com/company/source', 'https://u:p@git.corp.example/company/source', 'https://git.corp.example/company/source?evil=1']:
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                repository_url(value, self.config)
        self.assertFalse((self.root / 'run').exists())

    def test_resource_hash_mismatch_fails_before_returning_workspace(self):
        self.spec['resources'][0]['sha256'] = '0' * 64
        with self.assertRaises(ProtocolError) as caught:
            self.prepare()
        self.assertEqual(caught.exception.code, 'RESOURCE_HASH_MISMATCH')

    def test_resource_cannot_overwrite_source(self):
        self.spec['resources'][0]['destination'] = 'src/main.py'
        with self.assertRaises(ProtocolError):
            self.prepare()

    def test_rejects_path_traversal_backslashes_git_metadata_and_symlink_parents(self):
        for value in ['../out', '/tmp/out', 'src/../out', 'src\\out', '.git/config', 'src/.git/config', 'src/\nfile']:
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                checked_path(self.root, value)
        (self.root / 'linked').symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(ProtocolError):
            checked_path(self.root, 'linked/rules.md')

    def test_tracked_repository_symlink_is_rejected(self):
        (self.source / 'escape').symlink_to('/etc')
        self.git('add', 'escape', cwd=self.source)
        self.git('commit', '-qm', 'symlink', cwd=self.source)
        self.spec['source']['base_commit'] = self.git('rev-parse', 'HEAD', cwd=self.source).stdout.strip()
        with self.assertRaises(ProtocolError) as caught:
            self.prepare()
        self.assertEqual(caught.exception.code, 'UNSAFE_PATH')

    def test_patch_contains_new_allowed_files_and_applies_to_exact_base(self):
        workspace = self.prepare()
        (workspace / 'src' / 'main.py').write_text('value = 3\n')
        (workspace / 'src' / 'new.py').write_text('new = True\n')
        output = self.root / 'changes.patch'
        export_patch(self.spec, workspace, output)
        self.assertIn('value = 3', output.read_text())
        self.assertIn('new = True', output.read_text())
        self.assertNotIn('resources/rules.md', output.read_text())
        self.git('apply', '--check', str(output), cwd=self.source)

    def test_out_of_scope_edits_and_modified_resources_are_rejected(self):
        workspace = self.prepare()
        (workspace / 'outside.txt').write_text('forbidden')
        with self.assertRaises(ProtocolError) as caught:
            export_patch(self.spec, workspace, self.root / 'changes.patch')
        self.assertEqual(caught.exception.code, 'WRITE_SCOPE_VIOLATION')
        (workspace / 'outside.txt').unlink()
        (workspace / 'resources' / 'rules.md').chmod(0o644)
        (workspace / 'resources' / 'rules.md').write_text('tampered')
        with self.assertRaises(ProtocolError) as caught:
            export_patch(self.spec, workspace, self.root / 'changes.patch')
        self.assertEqual(caught.exception.code, 'RESOURCE_MODIFIED')


if __name__ == '__main__':
    unittest.main()
