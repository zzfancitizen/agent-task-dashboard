from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from taskboard.local import LocalStore
from taskboard.protocol import ProtocolError, task_digest
from taskboard.state import expire_task
from tests import test_cli


class WizardTests(unittest.TestCase):
    def wizard(self):
        self.assertIsNotNone(importlib.util.find_spec('taskboard.wizard'), 'first-run wizard is missing')
        from taskboard import wizard
        return wizard

    def harness(self):
        harness = test_cli.CLITests('runTest')
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        harness.publish()
        return harness

    def request(self, harness, **changes):
        descriptor = {'schema_version': 1, 'hostname': 'git.corp.example', 'repository': 'company/tasks', 'issue': 1, 'revision': 1, 'task_digest': task_digest(harness.spec), 'runtime_sha256': 'b' * 64, 'action': 'run', **changes}
        path = harness.root / 'request.json'
        path.write_text(json.dumps(descriptor))
        return path

    def invoke(self, wizard, harness, request, choices=('1', '1')):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {'TASKBOARD_HOME': str(harness.root / 'wizard-state')}), patch('builtins.input', side_effect=choices), patch.object(wizard, '_native_command', side_effect=lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, '', '')), patch.object(wizard.webbrowser, 'open', return_value=True), redirect_stdout(out), redirect_stderr(err):
            code = wizard.run_request(request)
        return code, out.getvalue(), err.getvalue()

    def test_stale_download_cannot_claim_or_create_a_workspace(self):
        wizard = self.wizard()
        harness = self.harness()
        result = self.invoke(wizard, harness, self.request(harness, task_digest='a' * 64), choices=('0',))
        self.assertNotEqual(result[0], 0, result)
        self.assertEqual(harness.gateway.state['tasks']['1']['status'], 'open')
        self.assertFalse((harness.root / 'wizard-state/runs').exists())
        self.assertIn('STALE_DOWNLOAD', result[2])

    def test_real_wizard_initializes_task_sources_runs_provider_and_confirms_submission(self):
        wizard = self.wizard()
        harness = self.harness()
        harness.gateway.permission = 'read'
        result = self.invoke(wizard, harness, self.request(harness))
        self.assertEqual(result[0], 0, result)
        self.assertEqual(harness.gateway.state['tasks']['1']['status'], 'submitted')
        store = LocalStore(harness.root / 'wizard-state')
        self.assertEqual(set(store.config()['allowed_repos']), {'company/tasks', 'company/source'})
        saved = next(iter(store.data()['runs'].values()))
        self.assertEqual(saved['phase'], 'submitted')
        self.assertTrue(Path(saved['result_file']).is_file())

    def test_queued_submission_is_confirmed_without_a_second_provider_execution(self):
        wizard = self.wizard()
        harness = self.harness()
        harness.gateway.delay_op = 'submit_bundle'
        calls = harness.root / 'provider-calls'
        def confirm_after_wait(_):
            harness.gateway.delay_op = None
            harness.gateway.process_pending(1)
        with patch.object(wizard, 'CLI_WAIT_SECONDS', 0), patch.object(wizard.time, 'sleep', side_effect=confirm_after_wait), patch.dict(os.environ, {'CLI_TEST_CALLS': str(calls)}):
            result = self.invoke(wizard, harness, self.request(harness))
        self.assertEqual(result[0], 0, result)
        self.assertEqual(calls.read_text().splitlines(), ['call'])
        self.assertEqual(harness.gateway.state['tasks']['1']['status'], 'submitted')

    def test_failed_worker_is_not_automatically_replayed_or_reported_as_success(self):
        wizard = self.wizard()
        harness = self.harness()
        calls = harness.root / 'provider-calls'
        with patch.dict(os.environ, {'CLI_TEST_CALLS': str(calls), 'CLI_TEST_MODE': 'nonzero'}):
            result = self.invoke(wizard, harness, self.request(harness))
        self.assertNotEqual(result[0], 0, result)
        self.assertEqual(calls.read_text().splitlines(), ['call'])
        self.assertIsNone(harness.gateway.state['tasks']['1']['result'])

    def test_pending_result_stops_when_lease_expires_without_reclaiming(self):
        wizard = self.wizard()
        harness = self.harness()
        harness.gateway.delay_op = 'submit_bundle'
        calls = harness.root / 'provider-calls'
        def expire_after_wait(_):
            current = harness.gateway.state['tasks']['1']
            harness.gateway.state['tasks']['1'] = expire_task(current, current['attempt']['expires_at'] + 1)
        with patch.object(wizard, 'CLI_WAIT_SECONDS', 0), patch.object(wizard.time, 'sleep', side_effect=expire_after_wait), patch.dict(os.environ, {'CLI_TEST_CALLS': str(calls)}):
            result = self.invoke(wizard, harness, self.request(harness))
        self.assertNotEqual(result[0], 0, result)
        self.assertIn('ATTEMPT_CHANGED', result[2])
        self.assertEqual(calls.read_text().splitlines(), ['call'])
        self.assertEqual(harness.gateway.state['tasks']['1']['attempt_count'], 1)

    def test_retry_is_fenced_when_lease_expires_between_wizard_and_cli_reads(self):
        wizard = self.wizard()
        harness = self.harness()
        harness.gateway.delay_op = 'submit_bundle'
        calls = harness.root / 'provider-calls'
        read_after_wait = [0]
        original = harness.gateway.run
        def race(argv, **kwargs):
            if read_after_wait[0] and any('contents/state.json' in value for value in argv):
                read_after_wait[0] += 1
                if read_after_wait[0] == 3:
                    current = harness.gateway.state['tasks']['1']
                    harness.gateway.state['tasks']['1'] = expire_task(current, current['attempt']['expires_at'] + 1)
            return original(argv, **kwargs)
        def enable_race(_):
            read_after_wait[0] = 1
        with patch.object(wizard, 'CLI_WAIT_SECONDS', 0), patch.object(wizard.time, 'sleep', side_effect=enable_race), patch.dict(os.environ, {'CLI_TEST_CALLS': str(calls)}), patch('taskboard.github.GitHub._run', side_effect=race):
            result = self.invoke(wizard, harness, self.request(harness))
        self.assertNotEqual(result[0], 0, result)
        self.assertEqual(calls.read_text().splitlines(), ['call'])
        self.assertEqual(harness.gateway.state['tasks']['1']['attempt_count'], 1)

    def test_publisher_setup_installs_in_selected_project_without_publishing(self):
        wizard = self.wizard()
        harness = self.harness()
        request = harness.root / 'publisher.json'
        request.write_text(json.dumps({'schema_version': 1, 'hostname': 'git.corp.example', 'repository': 'company/tasks', 'runtime_sha256': 'b' * 64, 'action': 'install-publisher'}))
        out = io.StringIO()
        def native(argv, **kwargs):
            if argv[:2] == ['git', '-C']:
                return subprocess.run(argv, capture_output=True, text=True)
            return subprocess.CompletedProcess(argv, 0, '', '')
        with patch.dict(os.environ, {'TASKBOARD_HOME': str(harness.root / 'publisher-state')}), patch.object(wizard, '_native_command', side_effect=native), patch('builtins.input', side_effect=('1', '2', str(harness.source), '1')), redirect_stdout(out):
            code = wizard.run_request(request)
        self.assertEqual(code, 0, out.getvalue())
        self.assertTrue((harness.source / '.agents/skills/taskboard-publish/SKILL.md').is_file())
        self.assertTrue((harness.source / '.codex/hooks.json').is_file())
        self.assertEqual(len(harness.gateway.issue_rows), 1)
        self.assertEqual(LocalStore(harness.root / 'publisher-state').config()['repo'], 'company/tasks')

    def test_descriptor_rejects_shell_metadata_boolean_ids_and_unknown_fields(self):
        wizard = self.wizard()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'request.json'
            base = {'schema_version': 1, 'hostname': 'github.com', 'repository': 'company/tasks', 'runtime_sha256': 'a' * 64, 'action': 'install-publisher'}
            for change in ({'hostname': 'github.com;echo bad'}, {'repository': 'company/repo$(bad)'}, {'issue': True, 'revision': 1, 'task_digest': 'a' * 64}, {'prompt': 'run arbitrary prompt'}, {'runtime_sha256': '../cache'}):
                path.write_text(json.dumps({**base, **change}))
                with self.subTest(change=change), self.assertRaises(ProtocolError):
                    wizard.read_request(path)

    def test_missing_tool_opens_official_guidance_only_after_choice_and_can_retry(self):
        wizard = self.wizard()
        available = [False]
        opened = []
        def official(url):
            opened.append(url)
            available[0] = True
            return True
        def probe(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0 if available[0] else 127, '', '')
        with patch.object(wizard, '_native_command', side_effect=probe), patch.object(wizard, 'installation_argv', return_value=None), patch('builtins.input', side_effect=('1',)), patch.object(wizard.webbrowser, 'open', side_effect=official):
            wizard.ensure_tool('gh')
        self.assertEqual(opened, ['https://cli.github.com/'])
        opened.clear()
        available[0] = False
        with patch.object(wizard, '_native_command', side_effect=probe), patch.object(wizard, 'installation_argv', return_value=None), patch('builtins.input', side_effect=('0',)), patch.object(wizard.webbrowser, 'open', side_effect=official), self.assertRaises(ProtocolError):
            wizard.ensure_tool('gh')
        self.assertEqual(opened, [])

    def test_native_login_is_explicit_and_uses_enterprise_browser_auth(self):
        wizard = self.wizard()
        launched = []
        def command(argv, **kwargs):
            if argv[1:3] == ['auth', 'login']:
                launched.append(argv)
                return subprocess.CompletedProcess(argv, 0, '', '')
            return subprocess.CompletedProcess(argv, 0 if launched else 1, '', '')
        with patch.object(wizard, '_native_command', side_effect=command), patch('builtins.input', side_effect=('1',)):
            wizard.ensure_login('gh', 'git.corp.example')
        self.assertEqual(launched, [['gh', 'auth', 'login', '--hostname', 'git.corp.example', '--git-protocol', 'https', '--web']])

    def test_native_output_remains_utf8_under_a_cp936_default(self):
        wizard = self.wizard()
        output = 'C:/Users/测试/中文项目\n✓ Logged in\n'
        script = 'import sys;sys.stdout.buffer.write(' + repr(output.encode('utf-8')) + ')'
        # Simulate the Windows ANSI fallback at the subprocess boundary;
        # the command and its UTF-8 bytes are produced by a real process.
        with patch('subprocess._text_encoding', return_value='cp936'):
            result = wizard._native_command([sys.executable, '-c', script])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, output)

    def test_invalid_native_encoding_is_a_failed_guidance_check(self):
        wizard = self.wizard()
        script = 'import sys;sys.stdout.buffer.write(bytes([255]))'
        result = wizard._native_command([sys.executable, '-c', script])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    unittest.main()
