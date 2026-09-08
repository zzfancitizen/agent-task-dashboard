import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
import uuid

from taskboard.local import LocalStore
from taskboard.protocol import ProtocolError


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('taskboard.integration'), 'Publisher integration is missing')
        from taskboard import integration
        self.integration = integration
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.project = self.root / 'a project'
        self.project.mkdir()
        self.store = LocalStore(self.root / 'private')
        self.store.initialize('company/tasks', 'git.corp.example', [])
        self.session = str(uuid.uuid4())

    def event(self, **changes):
        value = {'session_id': self.session, 'cwd': str(self.project), 'hook_event_name': 'SessionStart', 'transcript_path': '/private/transcript-never-read.jsonl', 'source': 'startup'}
        value.update(changes)
        return value

    def test_install_preserves_unrelated_hooks_settings_and_rules_and_is_idempotent(self):
        for provider, filename, rules in [('codex', '.codex/hooks.json', 'AGENTS.md'), ('claude', '.claude/settings.json', 'CLAUDE.md')]:
            with self.subTest(provider=provider):
                config = self.project / filename
                config.parent.mkdir(exist_ok=True)
                unrelated = {'matcher': 'startup', 'hooks': [{'type': 'command', 'command': 'existing-command'}]}
                config.write_text(json.dumps({'customSetting': True, 'hooks': {'SessionStart': [unrelated], 'Stop': [{'hooks': [{'type': 'command', 'command': 'stop-command'}]}]}}))
                rule = self.project / rules
                rule.write_text('Keep these project instructions.\n')
                first = self.integration.install_integration(provider, self.project, self.store)
                second = self.integration.install_integration(provider, self.project, self.store)
                result = json.loads(config.read_text())
                self.assertTrue(result['customSetting'])
                self.assertIn(unrelated, result['hooks']['SessionStart'])
                self.assertEqual(len(result['hooks']['SessionStart']), 2)
                self.assertIn('stop-command', json.dumps(result['hooks']['Stop']))
                self.assertTrue(rule.read_text().startswith('Keep these project instructions.\n'))
                self.assertEqual(rule.read_text().count('<!-- taskboard-publish:start -->'), 1)
                self.assertEqual(first['skill'], second['skill'])
                self.assertTrue(Path(first['skill']).is_file())
                self.assertTrue(Path(first['runtime']).is_dir())

    def test_exact_hook_session_callback_is_local_and_never_looks_up_latest(self):
        self.integration.install_integration('codex', self.project, self.store)
        result = self.integration.handle_hook('codex', self.project, self.store, self.event())
        context = result['hookSpecificOutput']['additionalContext']
        self.assertEqual(result['hookSpecificOutput']['hookEventName'], 'SessionStart')
        callbacks = self.store.data()['callbacks']
        self.assertEqual(len(callbacks), 1)
        callback_id, callback = next(iter(callbacks.items()))
        self.assertEqual(callback['session_id'], self.session)
        self.assertEqual(callback['workspace'], str(self.project))
        self.assertIn(callback_id, context)
        self.assertNotIn('transcript_path', json.dumps(callback))
        self.assertNotIn('/private/transcript', json.dumps(self.store.data()))
        self.integration.handle_hook('codex', self.project, self.store, self.event(session_id=str(uuid.uuid4())))
        self.assertEqual(len(self.store.data()['callbacks']), 2)
        for event in (self.event(session_id='latest'), self.event(session_id=None), self.event(cwd=str(self.root)), self.event(agent_id='child')):
            with self.subTest(event=event), self.assertRaises(ProtocolError):
                self.integration.handle_hook('codex', self.project, self.store, event)

    def test_installed_hook_and_helper_run_outside_runtime_checkout(self):
        installed = self.integration.install_integration('codex', self.project, self.store)
        config = json.loads((self.project / '.codex/hooks.json').read_text())
        command = config['hooks']['SessionStart'][0]['hooks'][0]['command']
        clean_env = {key: value for key, value in os.environ.items() if key != 'PYTHONPATH'}
        result = subprocess.run(shlex.split(command), input=json.dumps(self.event()), text=True, capture_output=True, cwd=self.root, env=clean_env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['hookSpecificOutput']['hookEventName'], 'SessionStart')
        callback_id = next(iter(self.store.data()['callbacks']))
        script = Path(installed['skill']).parent / 'scripts/taskboard-publish.py'
        status = subprocess.run([sys.executable, str(script), 'context', '--callback', callback_id], capture_output=True, text=True, cwd=self.root, env=clean_env)
        self.assertEqual(status.returncode, 0, status.stderr)
        locator = json.loads(status.stdout)
        self.assertEqual(locator['provider'], 'codex')
        self.assertEqual(locator['workspace'], str(self.project))
        self.assertTrue(Path(locator['goal_directory']).is_dir())
        self.assertEqual(locator['python_executable'], sys.executable)
        self.assertIn(str(self.store.home), locator['taskboard_argv'])
        help_result = subprocess.run([*locator['taskboard_argv'], '--help'], capture_output=True, text=True, cwd=self.root, env=clean_env)
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn('sync', help_result.stdout)

    def test_user_prompt_only_reports_pending_results_for_this_exact_binding_once(self):
        self.integration.install_integration('claude', self.project, self.store)
        self.integration.handle_hook('claude', self.project, self.store, self.event())
        self.store.bind(5, 'claude', self.session, self.project, 'a' * 64)
        self.store.bind(6, 'claude', str(uuid.uuid4()), self.project, 'b' * 64)
        result_id = str(uuid.uuid4())
        self.store.remember_result(5, result_id, {'summary': 'Untrusted result text must not become hook instructions.'})
        self.store.remember_result(6, str(uuid.uuid4()), {'summary': 'Other session'})
        result = self.integration.handle_hook('claude', self.project, self.store, self.event(hook_event_name='UserPromptSubmit', prompt='do work'))
        text = result['hookSpecificOutput']['additionalContext']
        self.assertIn(result_id, text)
        self.assertIn('#5', text)
        self.assertNotIn('#6', text)
        self.assertNotIn('Untrusted result text', text)
        self.assertEqual(self.integration.handle_hook('claude', self.project, self.store, self.event(hook_event_name='UserPromptSubmit')), {})
        self.assertEqual(self.store.data()['inbox'][result_id]['delivery'], 'pending')

    def test_generated_commands_force_utf8_for_non_ascii_windows_sessions(self):
        project = self.root / '中文项目-📘'
        project.mkdir()
        environment = {**os.environ, 'PYTHONIOENCODING': 'cp936', 'PYTHONUTF8': '0', 'LANG': 'C', 'LC_ALL': 'C'}
        for provider in ('codex', 'claude'):
            with self.subTest(provider=provider):
                installed = self.integration.install_integration(provider, project, self.store)
                settings = json.loads(Path(installed['hooks']).read_text(encoding='utf-8'))
                handler = settings['hooks']['SessionStart'][0]['hooks'][0]
                hook_argv = shlex.split(handler['command'])
                self.assertEqual(hook_argv[1:4], ['-I', '-X', 'utf8'])
                if provider == 'codex':
                    self.assertIn('-X utf8', handler['commandWindows'])
                event = self.event(cwd=str(project))
                result = subprocess.run(hook_argv, input=json.dumps(event, ensure_ascii=False).encode('utf-8'), capture_output=True, cwd=self.root, env=environment)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('中文项目-📘', json.loads(result.stdout.decode('utf-8'))['hookSpecificOutput']['additionalContext'])
                callback_id = next(key for key, value in self.store.data()['callbacks'].items() if value['provider'] == provider)
                locator = self.integration.callback_context(self.store, callback_id, provider=provider, project=project)
                for name in ('helper_argv', 'taskboard_argv'):
                    self.assertEqual(locator[name][1:4], ['-I', '-X', 'utf8'])
                    probe = subprocess.run([*locator[name][:4], '-c', 'import json, sys; print(json.dumps({"mode": sys.flags.utf8_mode, "text": "中文📘"}, ensure_ascii=False))'], capture_output=True, cwd=self.root, env=environment)
                    self.assertEqual(probe.returncode, 0, probe.stderr)
                    self.assertEqual(json.loads(probe.stdout.decode('utf-8')), {'mode': 1, 'text': '中文📘'})

    def test_invalid_configuration_fails_without_overwriting_it(self):
        path = self.project / '.claude/settings.json'
        path.parent.mkdir()
        path.write_text('{broken')
        with self.assertRaises(ProtocolError):
            self.integration.install_integration('claude', self.project, self.store)
        self.assertEqual(path.read_text(), '{broken')

    def test_first_user_prompt_can_supply_exact_callback_after_hook_reload(self):
        self.integration.install_integration('codex', self.project, self.store)
        result = self.integration.handle_hook('codex', self.project, self.store, self.event(hook_event_name='UserPromptSubmit'))
        callback_id = next(iter(self.store.data()['callbacks']))
        self.assertIn('hookSpecificOutput', result)
        self.assertIn(callback_id, result['hookSpecificOutput']['additionalContext'])
        self.assertEqual(self.integration.handle_hook('codex', self.project, self.store, self.event(hook_event_name='UserPromptSubmit')), {})

    def test_hook_refuses_oversized_local_state_before_reading_it(self):
        self.integration.install_integration('codex', self.project, self.store)
        path = self.store.home / 'local.json'
        with path.open('wb') as stream:
            stream.truncate(4 * 1024 * 1024 + 1)
        with self.assertRaises(ProtocolError) as caught:
            self.integration.handle_hook('codex', self.project, self.store, self.event())
        self.assertEqual(caught.exception.code, 'HOOK_STATE_TOO_LARGE')


if __name__ == '__main__':
    unittest.main()
