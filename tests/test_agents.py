import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from taskboard.agents import provider_argv, run_agent, run_checks
from taskboard.protocol import ProtocolError


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        self.bindir = self.root / 'bin'
        self.bindir.mkdir()
        fixture = Path(__file__).parent / 'fixtures' / 'cli_fake_provider.py'
        for name in ('codex', 'claude'):
            shutil.copyfile(fixture, self.bindir / name)
            (self.bindir / name).chmod(0o755)
        self.environment = patch.dict(os.environ, {'PATH': str(self.bindir) + os.pathsep + os.environ['PATH'], 'CLI_TEST_MODE': 'success'})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_codex_prompt_stays_on_stdin_and_real_session_is_captured(self):
        capture = self.root / 'args.json'
        prompt = 'Untrusted $(touch /tmp/nope) `shell` prompt'
        with patch.dict(os.environ, {'CLI_TEST_ARGV': str(capture)}):
            result = run_agent('codex', prompt, self.workspace, self.root / 'output', 5)
        data = json.loads(capture.read_text())
        self.assertEqual(data['prompt'], prompt)
        self.assertNotIn(prompt, data['argv'])
        self.assertEqual(data['argv'][-1], '-')
        self.assertIn('workspace-write', data['argv'])
        self.assertEqual(result.session_id, 'cf20e605-88b8-443f-87a1-50c027e984e1')
        self.assertEqual(result.summary, 'Implemented task.')
        self.assertEqual(result.usage['input_tokens'], 4)

    def test_claude_uses_bounded_tools_and_captures_terminal_result(self):
        result = run_agent('claude', 'Do task', self.workspace, self.root / 'output', 5)
        argv = provider_argv('claude')
        self.assertIn('stream-json', argv)
        self.assertIn('--restricted', argv)
        self.assertNotIn('Bash', argv[argv.index('--tools') + 1].split(','))
        self.assertFalse(any('dangerously' in item or item == 'bypassPermissions' for item in argv))
        self.assertEqual(result.summary, 'Implemented task.')

    def test_resume_uses_only_the_bound_uuid_and_never_latest_session(self):
        session = 'cf20e605-88b8-443f-87a1-50c027e984e1'
        for agent in ('codex', 'claude'):
            argv = provider_argv(agent, session)
            self.assertIn(session, argv)
            self.assertNotIn('--last', argv)
            self.assertNotIn('--continue', argv)
            with self.assertRaises(ProtocolError):
                provider_argv(agent, '--last')

    def test_process_failure_or_missing_terminal_event_cannot_claim_success(self):
        for mode in ('nonzero', 'error', 'silent'):
            with self.subTest(mode=mode), patch.dict(os.environ, {'CLI_TEST_MODE': mode}), self.assertRaises(ProtocolError):
                run_agent('codex', 'task', self.workspace, self.root / mode, 5)

    def test_resumed_provider_must_report_the_exact_bound_session(self):
        with patch.dict(os.environ, {'CLI_TEST_SESSION': '19391baa-9786-46f1-a5dd-ef21a419bb55'}), self.assertRaises(ProtocolError) as caught:
            run_agent('claude', 'result', self.workspace, self.root / 'output', 5, session='cf20e605-88b8-443f-87a1-50c027e984e1')
        self.assertEqual(caught.exception.code, 'SESSION_MISMATCH')

    def test_timeout_stops_child_process_group(self):
        with patch.dict(os.environ, {'CLI_TEST_MODE': 'timeout'}), self.assertRaises(ProtocolError) as caught:
            run_agent('codex', 'task', self.workspace, self.root / 'output', 0.2)
        self.assertEqual(caught.exception.code, 'AGENT_TIMEOUT')
        time.sleep(0.9)
        self.assertFalse((self.workspace / 'escaped-child').exists())

    def test_managed_claude_can_read_explicit_schema_and_inbox_directories(self):
        home = self.root / 'taskboard-home'
        schema = self.root / 'example-schema'
        home.mkdir()
        schema.mkdir()
        capture = self.root / 'claude-args.json'
        managed = {'home': home, 'command': Path('/usr/local/bin/taskboard'), 'schema_dir': schema}
        with patch.dict(os.environ, {'CLI_TEST_ARGV': str(capture)}):
            run_agent('claude', 'origin', self.workspace, self.root / 'origin-output', 5, managed=managed)
        argv = json.loads(capture.read_text())['argv']
        granted = [argv[index + 1] for index, value in enumerate(argv) if value == '--add-dir']
        self.assertEqual(set(granted), {str(home), str(schema)})
        self.assertIn('--restricted', argv)
        self.assertIn('Bash(/usr/local/bin/taskboard *)', argv)
        with patch.dict(os.environ, {'CLI_TEST_ARGV': str(capture)}):
            run_agent('claude', 'returned', self.workspace, self.root / 'resume-output', 5, session='cf20e605-88b8-443f-87a1-50c027e984e1', managed=managed)
        argv = json.loads(capture.read_text())['argv']
        self.assertEqual(argv[argv.index('--resume') + 1], 'cf20e605-88b8-443f-87a1-50c027e984e1')
        self.assertIn(str(home), argv)
        self.assertIn(str(schema), argv)

    def test_managed_claude_adds_specific_git_and_hash_permission_rules(self):
        capture = self.root / 'permission-args.json'
        managed = {'home': self.root, 'command': Path('/usr/local/bin/taskboard')}
        with patch.dict(os.environ, {'CLI_TEST_ARGV': str(capture)}):
            run_agent('claude', 'package a pinned task', self.workspace, self.root / 'output', 5, managed=managed)
        argv = json.loads(capture.read_text())['argv']
        rules = argv[argv.index('--allowedTools') + 1:argv.index('--permission-prompts')]
        self.assertEqual(set(rules), {
            'Bash(/usr/local/bin/taskboard *)',
            'Bash(git status)', 'Bash(git status *)',
            'Bash(git rev-parse)', 'Bash(git rev-parse *)',
            'Bash(git ls-files)', 'Bash(git ls-files *)',
            'Bash(git show)', 'Bash(git show *)',
            'Bash(git diff)', 'Bash(git diff *)',
            'Bash(git log)', 'Bash(git log *)',
            'Bash(git cat-file)', 'Bash(git cat-file *)',
            'Bash(shasum)', 'Bash(shasum *)',
            'Bash(sha256sum)', 'Bash(sha256sum *)',
        })
        self.assertIn('--restricted', argv)
        executor = provider_argv('claude')
        self.assertNotIn('--allowedTools', executor)
        self.assertNotIn('Bash', executor[executor.index('--tools') + 1].split(','))

    def test_session_event_is_observed_while_provider_is_still_running(self):
        marker = self.root / 'observed'
        def event_received(event):
            if event.get('type') == 'thread.started':
                marker.write_text(event['thread_id'])
        with patch.dict(os.environ, {'CLI_TEST_MODE': 'watch', 'CLI_TEST_MARKER': str(marker)}):
            run_agent('codex', 'task', self.workspace, self.root / 'output', 5, on_event=event_received)
        self.assertEqual(marker.read_text(), 'cf20e605-88b8-443f-87a1-50c027e984e1')

    def test_verification_records_real_exit_codes_without_shell_interpolation(self):
        import sys
        commands = [[sys.executable, '-c', 'import sys;print(sys.argv[1]);sys.exit(3)', '$(touch should-not-exist)']]
        rows = run_checks(commands, self.workspace, self.root / 'checks', 5)
        self.assertEqual(rows[0]['exit_code'], 3)
        self.assertIn('$(touch should-not-exist)', rows[0]['stdout'])
        self.assertFalse((self.workspace / 'should-not-exist').exists())


if __name__ == '__main__':
    unittest.main()
