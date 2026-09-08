import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

from taskboard.local import LocalStore, atomic_json
from taskboard.protocol import ProtocolError


class LocalStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = LocalStore(self.root / 'local')

    def test_initialization_persists_enterprise_repo_and_rejects_retargeting(self):
        self.store.initialize('company/tasks', 'git.corp.example', ['company/source'], 'claude')
        config = LocalStore(self.root / 'local').config()
        self.assertEqual(config['repo'], 'company/tasks')
        self.assertEqual(config['hostname'], 'git.corp.example')
        self.assertEqual(config['allowed_repos'], ['company/tasks', 'company/source'])
        self.assertEqual(config['agent'], 'claude')
        with self.assertRaises(ProtocolError):
            self.store.initialize('other/tasks', 'git.corp.example', [], 'codex')
        self.assertEqual(self.store.config()['repo'], 'company/tasks')

    def test_configuration_rejects_url_injection_before_writing(self):
        for repo, host in [('company/tasks;open', 'git.corp.example'), ('company/tasks', 'git.corp.example:443'), ('../tasks', 'git.corp.example'), ('company/tasks', '-option')]:
            with self.subTest(repo=repo, host=host), self.assertRaises(ProtocolError):
                self.store.initialize(repo, host, [], 'codex')
        self.assertFalse((self.root / 'local' / 'config.json').exists())

    def test_binding_requires_real_explicit_session_and_preserves_workspace(self):
        self.store.initialize('company/tasks', 'git.corp.example', [], 'codex')
        workspace = self.root / 'project'
        workspace.mkdir()
        self.store.bind(9, 'codex', 'cf20e605-88b8-443f-87a1-50c027e984e1', workspace, 'a' * 64)
        binding = LocalStore(self.store.home).binding(9)
        self.assertEqual(binding['session_id'], 'cf20e605-88b8-443f-87a1-50c027e984e1')
        self.assertEqual(binding['workspace'], str(workspace))
        with self.assertRaises(ProtocolError):
            self.store.bind(10, 'codex', '--last', workspace, 'a' * 64)
        self.assertIsNone(self.store.binding(10))

    def test_exclusive_session_lock_prevents_a_second_delivery(self):
        with self.store.exclusive('session:abc'):
            with self.assertRaises(ProtocolError) as caught:
                with LocalStore(self.store.home).exclusive('session:abc'):
                    self.fail('second process acquired a held session lock')
        self.assertEqual(caught.exception.code, 'LOCAL_BUSY')
        with self.store.exclusive('session:abc'):
            pass

    def test_exclusive_lock_fences_a_real_second_process(self):
        script = 'import sys\nfrom taskboard.local import LocalStore\nfrom taskboard.protocol import ProtocolError\ntry:\n with LocalStore(sys.argv[1]).exclusive("session:process"):\n  pass\nexcept ProtocolError as e:\n print(e.code)\n raise SystemExit(5)\n'
        command = [sys.executable, '-c', script, str(self.store.home)]
        with self.store.exclusive('session:process'):
            blocked = subprocess.run(command, text=True, capture_output=True, timeout=5)
        released = subprocess.run(command, text=True, capture_output=True, timeout=5)
        self.assertEqual(blocked.returncode, 5, blocked.stderr)
        self.assertEqual(blocked.stdout.strip(), 'LOCAL_BUSY')
        self.assertEqual(released.returncode, 0, released.stderr)

    def test_windows_atomic_json_keeps_replace_without_opening_directory_fd(self):
        from taskboard import platforms
        destination = self.root / 'windows-state.json'
        with patch.object(platforms, 'IS_WINDOWS', True):
            atomic_json(destination, {'version': 1})
            atomic_json(destination, {'version': 2})
        self.assertEqual(json.loads(destination.read_text()), {'version': 2})
        self.assertEqual(list(self.root.glob('.windows-state.json.*')), [])

    def test_interrupted_delivery_becomes_unknown_and_is_never_replayed(self):
        self.store.remember_result(9, 'a3514b4c-cd2a-4ff0-8e39-73c9c38cdd01', {'summary': 'ready'})
        self.assertTrue(self.store.begin_delivery('a3514b4c-cd2a-4ff0-8e39-73c9c38cdd01'))
        recovered = LocalStore(self.store.home)
        self.assertFalse(recovered.begin_delivery('a3514b4c-cd2a-4ff0-8e39-73c9c38cdd01'))
        self.assertEqual(recovered.data()['inbox']['a3514b4c-cd2a-4ff0-8e39-73c9c38cdd01']['delivery'], 'unknown')
        self.assertFalse(recovered.begin_delivery('a3514b4c-cd2a-4ff0-8e39-73c9c38cdd01'))

    def test_result_deduplication_does_not_reset_delivered_state(self):
        result_id = 'a3514b4c-cd2a-4ff0-8e39-73c9c38cdd01'
        self.store.remember_result(9, result_id, {'summary': 'ready'})
        self.store.begin_delivery(result_id)
        self.store.finish_delivery(result_id, True)
        self.store.remember_result(9, result_id, {'summary': 'ready'})
        self.assertFalse(self.store.begin_delivery(result_id))
        self.assertEqual(len(self.store.data()['inbox']), 1)

    def test_atomic_store_rejects_symlink_state_without_touching_target(self):
        self.store.home.mkdir(parents=True)
        victim = self.root / 'victim.json'
        victim.write_text('{"untouched":true}')
        (self.store.home / 'local.json').symlink_to(victim)
        with self.assertRaises(ProtocolError):
            self.store.update(lambda state: state.update({'pwned': True}))
        self.assertEqual(json.loads(victim.read_text()), {'untouched': True})

    @unittest.skipIf(os.name == 'nt', 'Windows private files inherit the user profile ACL, not POSIX mode bits')
    def test_private_files_have_owner_only_permissions(self):
        self.store.initialize('company/tasks', 'git.corp.example', [], 'codex')
        self.store.update(lambda state: state.update({'secret': 'local only'}))
        self.assertEqual(os.stat(self.store.home / 'config.json').st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.store.home / 'local.json').st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
