from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from taskboard.cli import main, parse_launch_url, install_launcher
from taskboard.local import LocalStore
from taskboard.protocol import ProtocolError, parse_task_issue
from tests.fixtures.cli_gateway import Gateway


class CLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / 'local'
        self.source = self.root / 'source'
        self.source.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Tests')
        self.git('config', 'user.email', 'tests@example.invalid')
        (self.source / 'src').mkdir()
        (self.source / 'src' / 'main.py').write_text('value = 1\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'fixture')
        commit = self.git('rev-parse', 'HEAD').stdout.strip()
        self.spec = json.loads((Path(__file__).parents[1] / 'docs/examples/task-v1.json').read_text())
        self.spec['source'] = {'repository': 'https://git.corp.example/company/source', 'base_commit': commit, 'workspace_patch': None, 'write_paths': ['src/']}
        self.spec['resources'] = []
        self.spec['execution']['timeout_seconds'] = 10
        self.spec['acceptance']['commands'] = [[sys.executable, '-c', 'import pathlib;assert "value = 2" in pathlib.Path("src/main.py").read_text()']]
        self.file = self.root / 'task.json'
        self.file.write_text(json.dumps(self.spec))
        self.gateway = Gateway()
        self.transport = patch('taskboard.github.GitHub._run', side_effect=self.gateway.run)
        self.transport.start()
        self.addCleanup(self.transport.stop)
        original_run = subprocess.run
        def gh_clone(argv, **kwargs):
            if argv[:3] == ['gh', 'repo', 'clone']:
                return original_run(['git', 'clone', '--no-checkout', str(self.source), argv[4]], **kwargs)
            return original_run(argv, **kwargs)
        self.clone = patch('taskboard.workspace.subprocess.run', side_effect=gh_clone)
        self.clone.start()
        self.addCleanup(self.clone.stop)
        self.bindir = self.root / 'bin'
        self.bindir.mkdir()
        for name in ('codex', 'claude'):
            shutil.copyfile(Path(__file__).parent / 'fixtures/cli_fake_provider.py', self.bindir / name)
            (self.bindir / name).chmod(0o755)
        self.environment = patch.dict(os.environ, {'PATH': str(self.bindir) + os.pathsep + os.environ['PATH'], 'CLI_TEST_MODE': 'success', 'CLI_TEST_EDIT': '1'})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.assertEqual(self.command('init', '--repo', 'company/tasks', '--hostname', 'git.corp.example', '--allow-repo', 'company/source')[0], 0)

    def git(self, *argv):
        return subprocess.run(['git', *argv], cwd=self.source, check=True, capture_output=True, text=True)

    def command(self, *argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(['--home', str(self.home), *argv])
        return code, stdout.getvalue(), stderr.getvalue()

    def publish(self, *extra):
        response = self.command('publish', str(self.file), *extra)
        self.assertEqual(response[0], 0, response)
        return response

    def test_validate_works_without_configuration_and_rejects_oversized_payload(self):
        self.assertEqual(main(['--home', str(self.root / 'new-home'), 'validate', str(self.file)]), 0)
        self.file.write_text(' ' * (49 * 1024))
        self.assertEqual(self.command('validate', str(self.file))[0], 1)

    def test_publish_recovers_lost_create_response_without_duplicate_issue(self):
        self.gateway.lose_create = True
        self.assertEqual(self.command('publish', str(self.file))[0], 1)
        self.publish()
        self.publish()
        self.assertEqual(len(self.gateway.issue_rows), 1)
        self.assertEqual(parse_task_issue(self.gateway.issue_rows[0]['body']), self.spec)

    def test_publish_binding_is_only_local_and_conflicting_payload_is_rejected(self):
        session = 'cf20e605-88b8-443f-87a1-50c027e984e1'
        self.publish('--session', session, '--agent', 'codex', '--workspace', str(self.source))
        self.assertEqual(LocalStore(self.home).binding(1)['session_id'], session)
        self.assertNotIn(session, self.gateway.issue_rows[0]['body'])
        self.spec['prompt'] += ' changed'
        self.file.write_text(json.dumps(self.spec))
        self.assertEqual(self.command('publish', str(self.file))[0], 1)
        self.assertEqual(len(self.gateway.issue_rows), 1)

    def test_proposal_cli_prepares_locally_then_requires_explicit_publish_consent(self):
        from taskboard import publishing
        self.git('remote', 'add', 'origin', 'https://git.corp.example/company/source.git')
        goal = self.root / 'goal.json'
        goal.write_text(json.dumps({'goal': 'Update the value', 'context': 'A separate part of the Python source.', 'acceptance': ['The source value is 2.'], 'delegation_reason': 'Can be executed independently.'}))
        original = publishing._git
        def advertised_refs(root, *argv, **kwargs):
            if argv[0] == 'ls-remote':
                return subprocess.CompletedProcess(argv, 0, f'{self.spec["source"]["base_commit"]}\trefs/heads/main\n'.encode(), b'')
            return original(root, *argv, **kwargs)
        with patch('taskboard.publishing._git', side_effect=advertised_refs):
            response = self.command('propose', '--workspace', str(self.source), '--goal-file', str(goal), '--title', 'Update the value', '--provider', 'codex', '--session', 'cf20e605-88b8-443f-87a1-50c027e984e1', '--resource', 'src/main.py', '--write-path', 'src/', '--command-json', json.dumps([sys.executable, '-m', 'unittest']))
        self.assertEqual(response[0], 0, response)
        proposal = json.loads(response[1])
        self.assertFalse(proposal['approved'])
        self.assertEqual(self.gateway.operations, [])
        rejected = self.command('publish-proposal', proposal['id'])
        self.assertEqual(rejected[0], 1)
        self.assertIn('PUBLISH_APPROVAL_REQUIRED', rejected[2])
        self.assertEqual(self.gateway.operations, [])
        self.gateway.permission = 'read'
        for _ in range(2):
            response = self.command('publish-proposal', proposal['id'], '--approved')
            self.assertEqual(response[0], 0, response)
        self.assertEqual(len(self.gateway.issue_rows), 1)
        self.assertEqual(LocalStore(self.home).binding(1)['session_id'], 'cf20e605-88b8-443f-87a1-50c027e984e1')

    def test_pending_claim_does_not_start_model_or_checkout(self):
        self.publish()
        self.gateway.delay = True
        code, _, errors = self.command('run', '1', '--agent', 'codex', '--wait', '0')
        self.assertEqual(code, 1)
        self.assertIn('PENDING', errors)
        self.assertFalse((self.home / 'runs').exists())

    def test_download_retry_fence_rejects_stale_digest_and_new_claim_generation(self):
        from taskboard.protocol import task_digest
        self.publish()
        response = self.command('run', '1', '--agent', 'codex', '--wait', '0', '--expected-task-digest', '0' * 64, '--expected-attempt-count', '1')
        self.assertEqual(response[0], 1, response)
        self.assertEqual(self.gateway.state['tasks']['1']['attempt_count'], 0)
        self.assertEqual(self.command('claim', '1', '--wait', '0')[0], 0)
        self.assertEqual(self.command('release', '1', '--reason', 'Stop this run')[0], 0)
        response = self.command('run', '1', '--agent', 'codex', '--wait', '0', '--expected-task-digest', task_digest(self.spec), '--expected-attempt-count', '1')
        self.assertEqual(response[0], 1, response)
        self.assertEqual(self.gateway.state['tasks']['1']['attempt_count'], 1)
        self.assertFalse((self.home / 'runs').exists())

    def test_start_ack_for_old_attempt_cannot_launch_after_same_actor_reclaims(self):
        import time
        import uuid
        from taskboard.state import apply_command
        self.publish()
        original = self.gateway.run
        switched = False
        def changed_state(argv, **kwargs):
            nonlocal switched
            record = self.gateway.state['tasks']['1']
            if any('/contents/state.json' in part for part in argv) and record['status'] == 'running' and not switched:
                switched = True
                for operation in ({'op': 'release', 'attempt_id': record['attempt']['id'], 'reason': 'Stopped elsewhere'}, {'op': 'claim'}):
                    command = {**operation, 'request_id': str(uuid.uuid4()), 'revision': 1}
                    record = apply_command(record, command, actor='alice', comment_id=900, now=int(time.time()))
                self.gateway.state['tasks']['1'] = record
            return original(argv, **kwargs)
        calls = self.root / 'calls'
        with patch('taskboard.github.GitHub._run', side_effect=changed_state), patch.dict(os.environ, {'CLI_TEST_CALLS': str(calls)}):
            response = self.command('run', '1', '--agent', 'codex', '--wait', '0', '--expected-attempt-count', '1')
        self.assertEqual(response[0], 1, response)
        self.assertEqual(self.gateway.state['tasks']['1']['attempt_count'], 2)
        self.assertFalse(calls.exists())

    def test_lost_claim_response_is_recovered_with_same_request_id(self):
        self.publish()
        self.gateway.lose_comment = True
        self.assertEqual(self.command('claim', '1', '--wait', '0')[0], 1)
        self.assertEqual(self.command('claim', '1', '--wait', '0')[0], 0)
        self.assertEqual(self.gateway.state['tasks']['1']['attempt_count'], 1)

    def test_run_uploads_real_patch_and_verification_before_submit_then_sync_dedupes(self):
        self.publish('--session', 'cf20e605-88b8-443f-87a1-50c027e984e1', '--agent', 'codex', '--workspace', str(self.source))
        response = self.command('run', '1', '--agent', 'codex', '--wait', '0')
        self.assertEqual(response[0], 0, response)
        record = self.gateway.state['tasks']['1']
        self.assertEqual(record['status'], 'submitted')
        manifest = record['result']['manifest']
        self.assertEqual({artifact['name'] for artifact in manifest['artifacts']}, {'changes.patch', 'summary.md', 'verification.json'})
        self.assertEqual(manifest['verification'][0]['exit_code'], 0)
        patch_url = next(artifact['uri'] for artifact in manifest['artifacts'] if artifact['name'] == 'changes.patch')
        patch_asset = next(asset for release in self.gateway.releases.values() for asset in release['assets'] if asset['browser_download_url'] == patch_url)
        patch_file = self.root / 'returned.patch'
        patch_file.write_bytes(self.gateway.assets[patch_asset['id']])
        self.git('apply', '--check', str(patch_file))
        self.assertNotIn('cf20e605-88b8-443f-87a1-50c027e984e1', json.dumps(manifest))
        for _ in range(2):
            self.assertEqual(self.command('sync')[0], 0)
        inbox = LocalStore(self.home).data()['inbox']
        self.assertEqual(len(inbox), 1)
        item = next(iter(inbox.values()))
        self.assertTrue(item['downloaded'])
        self.assertEqual(item['delivery'], 'pending')
        self.assertEqual(self.command('sync', '--resume')[0], 0)
        self.assertEqual(LocalStore(self.home).data()['inbox'][item['result_id']]['delivery'], 'delivered')
        self.assertEqual(self.command('sync', '--resume')[0], 0)

    def test_failed_verification_cannot_upload_or_submit_success(self):
        self.spec['acceptance']['commands'] = [[sys.executable, '-c', 'raise SystemExit(4)']]
        self.file.write_text(json.dumps(self.spec))
        self.publish()
        response = self.command('run', '1', '--agent', 'codex', '--wait', '0')
        self.assertEqual(response[0], 1)
        self.assertEqual(self.gateway.uploads, 0)
        self.assertIsNone(self.gateway.state['tasks']['1']['result'])
        self.assertTrue(list((self.home / 'runs').rglob('verification.json')))

    def test_run_never_replays_a_crashed_local_attempt(self):
        self.publish()
        self.command('claim', '1', '--wait', '0')
        attempt = self.gateway.state['tasks']['1']['attempt']['id']
        LocalStore(self.home).update(lambda data: data['runs'].update({attempt: {'phase': 'running', 'issue': 1}}))
        response = self.command('run', '1', '--agent', 'codex', '--wait', '0')
        self.assertEqual(response[0], 1)
        self.assertIn('UNKNOWN', response[2])
        self.assertFalse((self.home / 'runs').exists())

    def test_result_id_is_required_for_accept_and_reject_and_release_reopens(self):
        self.publish()
        self.command('claim', '1', '--wait', '0')
        self.assertEqual(self.command('release', '1', '--reason', 'operator stopped worker')[0], 0)
        self.assertEqual(self.gateway.state['tasks']['1']['status'], 'open')
        self.assertEqual(self.command('run', '1', '--agent', 'codex', '--wait', '0')[0], 0)
        result_id = self.gateway.state['tasks']['1']['result']['id']
        self.assertEqual(self.command('accept', '1', '--result', '714c5869-f9ea-4b3b-aaeb-37e4b1036075')[0], 1)
        self.assertEqual(self.command('accept', '1', '--result', result_id)[0], 0)
        self.assertEqual(self.gateway.state['tasks']['1']['status'], 'accepted')

    def test_pending_start_is_confirmed_later_without_releasing_or_replaying(self):
        import time
        from taskboard.protocol import parse_command
        from taskboard.state import apply_command
        self.publish()
        self.gateway.delay_op = 'start'
        calls = self.root / 'calls'
        with patch.dict(os.environ, {'CLI_TEST_CALLS': str(calls)}):
            self.assertEqual(self.command('run', '1', '--agent', 'codex', '--wait', '0')[0], 1)
            self.assertFalse(calls.exists())
            command = self.gateway.comments[-1]
            self.assertEqual(parse_command(command['body'])['op'], 'start')
            self.gateway.state['tasks']['1'] = apply_command(self.gateway.state['tasks']['1'], parse_command(command['body']), actor='alice', comment_id=command['id'], now=int(time.time()))
            self.gateway.delay_op = None
            response = self.command('run', '1', '--agent', 'codex', '--wait', '0')
        self.assertEqual(response[0], 0, response)
        self.assertEqual(calls.read_text().splitlines(), ['call'])
        self.assertEqual(self.gateway.state['tasks']['1']['attempt_count'], 1)

    def test_partial_upload_retry_reuses_artifacts_without_a_second_model_call(self):
        self.publish()
        calls = self.root / 'calls'
        with patch.dict(os.environ, {'CLI_TEST_CALLS': str(calls)}):
            self.gateway.fail_upload_at = 2
            self.assertEqual(self.command('run', '1', '--agent', 'codex', '--wait', '0')[0], 1)
            response = self.command('run', '1', '--agent', 'codex', '--wait', '0')
        self.assertEqual(response[0], 0, response)
        self.assertEqual(calls.read_text().splitlines(), ['call'])
        self.assertEqual(self.gateway.state['tasks']['1']['status'], 'submitted')
        self.assertEqual(len(next(iter(self.gateway.releases.values()))['assets']), 3)

    def test_lost_submit_acknowledgment_is_recovered_without_rerunning(self):
        self.publish()
        calls = self.root / 'calls'
        self.gateway.lose_op = 'submit_bundle'
        with patch.dict(os.environ, {'CLI_TEST_CALLS': str(calls)}):
            self.assertEqual(self.command('run', '1', '--agent', 'codex', '--wait', '0')[0], 1)
            response = self.command('run', '1', '--agent', 'codex', '--wait', '0')
        self.assertEqual(response[0], 0, response)
        self.assertEqual(calls.read_text().splitlines(), ['call'])
        self.assertEqual(self.gateway.state['tasks']['1']['status'], 'submitted')

    def test_unknown_delivery_is_not_replayed_by_a_later_sync(self):
        self.publish('--session', 'cf20e605-88b8-443f-87a1-50c027e984e1', '--agent', 'codex', '--workspace', str(self.source))
        self.assertEqual(self.command('run', '1', '--agent', 'codex', '--wait', '0')[0], 0)
        calls = self.root / 'calls'
        with patch.dict(os.environ, {'CLI_TEST_MODE': 'error', 'CLI_TEST_CALLS': str(calls)}):
            self.assertEqual(self.command('sync', '--resume')[0], 1)
        with patch.dict(os.environ, {'CLI_TEST_CALLS': str(calls)}):
            self.assertEqual(self.command('sync', '--resume')[0], 0)
        self.assertEqual(calls.read_text().splitlines(), ['call'])
        self.assertEqual(next(iter(LocalStore(self.home).data()['inbox'].values()))['delivery'], 'unknown')

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS compiler required')
    def test_launcher_compiles_a_real_app_with_protocol_registration_metadata(self):
        import plistlib
        original_run = subprocess.run
        def os_boundary(argv, **kwargs):
            if str(argv[0]).endswith('/lsregister'):
                return subprocess.CompletedProcess(argv, 0, '', '')
            return original_run(argv, **kwargs)
        with patch('taskboard.cli.subprocess.run', side_effect=os_boundary):
            app = install_launcher(LocalStore(self.home), Path(__file__).parents[1] / 'bin/taskboard', applications=self.root / 'Applications')
        with (app / 'Contents/Info.plist').open('rb') as stream:
            metadata = plistlib.load(stream)
        self.assertEqual(metadata['CFBundleURLTypes'][0]['CFBundleURLSchemes'], ['taskboard'])
        self.assertTrue((app / 'Contents/Resources/Scripts/main.scpt').is_file())

    def test_managed_start_publishes_and_binds_the_observed_session_without_flags(self):
        project = Path(__file__).parents[1]
        shutil.copyfile(project / 'tests/fixtures/cli_fake_gh.py', self.bindir / 'gh')
        (self.bindir / 'gh').chmod(0o755)
        remote = self.root / 'remote.json'
        remote.write_text(json.dumps(self.gateway.__dict__))
        prompt = self.root / 'origin-prompt.txt'
        prompt.write_text('Consider independent delegation for this work.')
        capture = self.root / 'origin-args.json'
        with patch.dict(os.environ, {'CLI_TEST_MODE': 'managed', 'CLI_TEST_TASK': str(self.file), 'CLI_TEST_PROJECT': str(project), 'CLI_TEST_GATEWAY': str(remote), 'CLI_TEST_ARGV': str(capture)}):
            result = self.command('start', '--agent', 'codex', '--prompt', str(prompt), '--workspace', str(self.source))
        self.assertEqual(result[0], 0, result)
        data = LocalStore(self.home).data()
        binding = data['bindings']['1']
        self.assertEqual(binding['session_id'], 'cf20e605-88b8-443f-87a1-50c027e984e1')
        self.assertEqual(data['origins'][binding['managed_run_id']]['phase'], 'stopped')
        published = json.loads(remote.read_text())['issue_rows']
        self.assertEqual(len(published), 1)
        self.assertNotIn(binding['session_id'], published[0]['body'])
        argv = json.loads(capture.read_text())['argv']
        self.assertIn('--add-dir', argv)
        self.assertEqual(argv[argv.index('--add-dir') + 1], str(self.home))
        self.assertIn('sandbox_workspace_write.network_access=true', argv)

    def test_explicit_resume_keeps_automatic_binding_for_new_delegation(self):
        self.publish('--session', 'cf20e605-88b8-443f-87a1-50c027e984e1', '--agent', 'codex', '--workspace', str(self.source))
        self.assertEqual(self.command('run', '1', '--agent', 'codex', '--wait', '0')[0], 0)
        project = Path(__file__).parents[1]
        shutil.copyfile(project / 'tests/fixtures/cli_fake_gh.py', self.bindir / 'gh')
        (self.bindir / 'gh').chmod(0o755)
        remote = self.root / 'remote.json'
        snapshot = dict(self.gateway.__dict__)
        snapshot['assets'] = {}
        remote.write_text(json.dumps(snapshot))
        self.spec['task_id'] = '714c5869-f9ea-4b3b-aaeb-37e4b1036075'
        self.file.write_text(json.dumps(self.spec))
        with patch.dict(os.environ, {'CLI_TEST_MODE': 'managed', 'CLI_TEST_TASK': str(self.file), 'CLI_TEST_PROJECT': str(project), 'CLI_TEST_GATEWAY': str(remote)}):
            response = self.command('sync', '--resume')
        self.assertEqual(response[0], 0, response)
        binding = LocalStore(self.home).binding(2)
        self.assertEqual(binding['session_id'], 'cf20e605-88b8-443f-87a1-50c027e984e1')
        self.assertEqual(LocalStore(self.home).data()['origins'][binding['managed_run_id']]['phase'], 'stopped')

    def test_unrecognized_origin_environment_cannot_guess_a_session(self):
        with patch.dict(os.environ, {'TASKBOARD_ORIGIN_RUN_ID': 'a3514b4c-cd2a-4ff0-8e39-73c9c38cdd01'}):
            response = self.command('publish', str(self.file))
        self.assertEqual(response[0], 1)
        self.assertEqual(len(self.gateway.issue_rows), 0)

    def test_incompatible_provider_is_rejected_without_consuming_an_attempt(self):
        self.spec['execution']['compatible_agents'] = ['claude']
        self.file.write_text(json.dumps(self.spec))
        self.publish()
        self.assertEqual(self.command('run', '1', '--agent', 'codex', '--wait', '0')[0], 1)
        self.assertEqual(self.gateway.state['tasks']['1']['attempt_count'], 0)

    def test_read_only_account_can_publish_run_and_return_artifacts_via_actions(self):
        self.gateway.permission = 'read'
        self.publish()
        calls = self.root / 'calls'
        with patch.dict(os.environ, {'CLI_TEST_CALLS': str(calls)}):
            response = self.command('run', '1', '--agent', 'codex', '--wait', '0')
        self.assertEqual(response[0], 0, response)
        self.assertEqual(self.gateway.state['tasks']['1']['status'], 'submitted')
        self.assertEqual(calls.read_text().splitlines(), ['call'])
        self.assertFalse(any('/collaborators/' in part for argv in self.gateway.operations for part in argv))
        self.assertTrue(any('taskboard:artifact:v2' in row['body'] for row in self.gateway.comments))
        self.assertEqual(self.command('sync')[0], 0)

    def test_delayed_bundle_confirmation_never_starts_a_second_model(self):
        self.publish()
        self.gateway.delay_op = 'submit_bundle'
        calls = self.root / 'calls'
        with patch.dict(os.environ, {'CLI_TEST_CALLS': str(calls)}):
            response = self.command('run', '1', '--agent', 'codex', '--wait', '0')
            self.assertEqual(response[0], 0, response)
            self.assertEqual(self.gateway.state['tasks']['1']['status'], 'running')
            self.gateway.process_pending(1)
            response = self.command('run', '1', '--agent', 'codex', '--wait', '0')
        self.assertEqual(response[0], 0, response)
        self.assertEqual(calls.read_text().splitlines(), ['call'])
        job = next(iter(LocalStore(self.home).data()['runs'].values()))
        self.assertEqual(job['phase'], 'submitted')
        self.assertEqual(json.loads(Path(job['result_file']).read_text()), self.gateway.state['tasks']['1']['result']['manifest'])

    def test_read_only_account_can_still_download_its_returned_results(self):
        self.publish()
        self.assertEqual(self.command('run', '1', '--agent', 'codex', '--wait', '0')[0], 0)
        self.gateway.permission = 'read'
        response = self.command('sync')
        self.assertEqual(response[0], 0, response)

    def test_structured_executor_assumptions_and_unresolved_items_are_preserved(self):
        report = {'summary': 'Implemented with a stated limitation.', 'assumptions': ['The pinned input is authoritative.'], 'unresolved': ['The enterprise backend was unavailable.']}
        self.publish()
        with patch.dict(os.environ, {'CLI_TEST_REPORT': json.dumps(report)}):
            response = self.command('run', '1', '--agent', 'codex', '--wait', '0')
        self.assertEqual(response[0], 0, response)
        result = self.gateway.state['tasks']['1']['result']['manifest']
        self.assertEqual(result['summary'], report['summary'])
        self.assertEqual(result['assumptions'], report['assumptions'])
        self.assertEqual(result['unresolved'], report['unresolved'])

    def test_unstructured_executor_report_is_retained_and_explicitly_flagged(self):
        report = 'Completed the change.\nAssumption: source input is current.\nUnresolved: backend not checked.'
        self.publish()
        with patch.dict(os.environ, {'CLI_TEST_REPORT': report}):
            self.assertEqual(self.command('run', '1', '--agent', 'codex', '--wait', '0')[0], 0)
        manifest = self.gateway.state['tasks']['1']['result']['manifest']
        self.assertEqual(manifest['summary'], report)
        self.assertTrue(manifest['unresolved'])
        summary_url = next(artifact['uri'] for artifact in manifest['artifacts'] if artifact['name'] == 'summary.md')
        summary_asset = next(asset for release in self.gateway.releases.values() for asset in release['assets'] if asset['browser_download_url'] == summary_url)
        self.assertIn(report, self.gateway.assets[summary_asset['id']].decode())

    def test_only_author_can_cancel_and_rejected_actor_does_not_poison_owner_request(self):
        self.publish()
        self.gateway.actor = 'bob'
        self.assertEqual(self.command('cancel', '1')[0], 1)
        self.assertEqual(self.gateway.state['tasks']['1']['status'], 'open')
        self.gateway.actor = 'alice'
        self.assertEqual(self.command('cancel', '1')[0], 0)
        self.assertEqual(self.gateway.state['tasks']['1']['status'], 'cancelled')

    def test_returned_artifact_names_cannot_collide_with_manifest_or_delivery_logs(self):
        extra = {'result.json': '{"user":"artifact"}', 'delivery/events.jsonl': 'user events\n'}
        self.spec['source']['write_paths'].extend(['result.json', 'delivery/'])
        self.spec['acceptance']['required_outputs'].extend(extra)
        self.file.write_text(json.dumps(self.spec))
        self.publish('--session', 'cf20e605-88b8-443f-87a1-50c027e984e1', '--agent', 'codex', '--workspace', str(self.source))
        with patch.dict(os.environ, {'CLI_TEST_OUTPUTS': json.dumps(extra)}):
            self.assertEqual(self.command('run', '1', '--agent', 'codex', '--wait', '0')[0], 0)
        self.assertEqual(self.command('sync', '--resume')[0], 0)
        item = next(iter(LocalStore(self.home).data()['inbox'].values()))
        directory = Path(item['directory'])
        for artifact in item['manifest']['artifacts']:
            file = directory / 'artifacts' / artifact['name']
            self.assertTrue(file.is_file(), str(file))
            self.assertEqual(hashlib.sha256(file.read_bytes()).hexdigest(), artifact['sha256'])
        self.assertEqual(json.loads((directory / 'result.json').read_text())['task_id'], self.spec['task_id'])
        self.assertIn('thread.started', (directory / 'delivery/events.jsonl').read_text())

    def test_sync_accepts_uppercase_protocol_hashes(self):
        self.publish()
        self.assertEqual(self.command('run', '1', '--agent', 'codex', '--wait', '0')[0], 0)
        for artifact in self.gateway.state['tasks']['1']['result']['manifest']['artifacts']:
            artifact['sha256'] = artifact['sha256'].upper()
        response = self.command('sync')
        self.assertEqual(response[0], 0, response)
        self.assertTrue(next(iter(LocalStore(self.home).data()['inbox'].values()))['downloaded'])

    def test_launch_url_requires_exact_configured_board_and_safe_fields(self):
        config = LocalStore(self.home).config()
        valid = 'taskboard://run?repo=company%2Ftasks&issue=42&hostname=git.corp.example'
        self.assertEqual(parse_launch_url(valid, config), 42)
        self.assertEqual(parse_launch_url(valid + '&agent=claude', config), 42)
        for value in [valid + '&issue=9', valid + '&agent=unknown', valid.replace('issue=42', 'issue=0'), valid.replace('company%2Ftasks', 'other%2Ftasks'), valid.replace('git.corp.example', 'github.com'), valid + '#fragment', valid.replace('issue=42', 'issue=%24%28open%29')]:
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                parse_launch_url(value, config)


if __name__ == '__main__':
    unittest.main()
