import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import uuid

from taskboard.local import LocalStore
from taskboard.protocol import ProtocolError, parse_task_issue, task_digest


class PublishingTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('taskboard.publishing'), 'Approval-gated publishing is missing')
        from taskboard import publishing
        self.publisher = publishing
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.workspace = self.root / 'source'
        self.workspace.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        (self.workspace / 'src').mkdir()
        (self.workspace / 'src' / 'app.py').write_text('answer = 42\n')
        (self.workspace / 'context.md').write_text('Committed input\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'Initial fixture')
        self.commit = self.git('rev-parse', 'HEAD').strip()
        self.git('remote', 'add', 'origin', 'git@git.corp.example:company/source.git')
        self.store = LocalStore(self.root / 'private')
        self.store.initialize('company/tasks', 'git.corp.example', [])
        self.session = str(uuid.uuid4())
        self.goal = self.root / 'goal.json'
        self.goal_value = {
            'goal': 'Add a version endpoint',
            'context': 'The source is a small Python service; return the existing version value.',
            'acceptance': ['The endpoint returns the version without mutating state.'],
            'delegation_reason': 'This endpoint is independent of the active UI work.',
            'handoff': {
                'non_goals': ['Do not change the UI or deploy the service.'],
                'constraints': ['Preserve the existing answer value.'],
                'assumptions': [],
                'environment': 'Use Python 3 and Git; this task needs no external service or credentials.',
                'stop_conditions': ['Stop and report missing source inputs instead of guessing their contents.'],
                'review': {
                    'first_step': 'Read src/app.py to find the current answer value.',
                    'inputs': 'Use src/app.py from the pinned source and taskboard-inputs/context.md for supporting context.',
                    'completion': 'Run python -m unittest and return changes.patch, summary.md, and verification.json.',
                    'blocking_questions': [],
                },
            },
        }
        self.goal.write_text(json.dumps(self.goal_value))
        real_git = publishing._git
        def git_edge(root, *argv, **kwargs):
            if argv[0] == 'ls-remote':
                return subprocess.CompletedProcess(argv, 0, f'{self.commit}\trefs/heads/main\n'.encode(), b'')
            return real_git(root, *argv, **kwargs)
        self.addCleanup(patch.stopall)
        patch('taskboard.publishing._git', side_effect=git_edge).start()

    def git(self, *argv):
        result = subprocess.run(['git', '-C', str(self.workspace), *argv], check=True, capture_output=True, text=True)
        return result.stdout

    def prepare(self, **changes):
        values = dict(workspace=self.workspace, goal_file=self.goal, title='Version endpoint', provider='codex', session=self.session, resource_paths=['context.md'], write_paths=['src/'], commands=[['python', '-m', 'unittest']])
        values.update(changes)
        return self.publisher.prepare_proposal(self.store, **values)

    def test_preparation_builds_complete_pinned_task_and_stable_local_proposal(self):
        proposal = self.prepare()
        task = proposal['task']
        self.assertFalse(proposal['approved'])
        self.assertEqual(task['source']['repository'], 'https://git.corp.example/company/source')
        self.assertEqual(task['source']['base_commit'], self.commit)
        self.assertEqual(task['resources'][0]['sha256'], hashlib.sha256(b'Committed input\n').hexdigest())
        self.assertEqual(task['resources'][0]['destination'], 'taskboard-inputs/context.md')
        self.assertIn('small Python service', task['prompt'])
        self.assertIn('without mutating state', task['acceptance']['review_notes'])
        self.assertEqual(task['handoff'], self.goal_value['handoff'])
        for value in (self.commit, 'taskboard-inputs/context.md', 'Preserve the existing answer value.', 'Stop and report missing source inputs', 'changes.patch'):
            self.assertIn(value, proposal['execution_prompt'])
        self.assertEqual(proposal['digest'], task_digest(task))
        repeated = self.prepare()
        self.assertEqual(repeated['id'], proposal['id'])
        self.assertEqual(repeated['execution_prompt'], proposal['execution_prompt'])
        self.assertNotIn('execution_prompt', self.store.data()['proposals'][proposal['id']])
        proposal['execution_prompt'] = 'caller-only preview change'
        self.assertEqual(self.prepare()['execution_prompt'], repeated['execution_prompt'])
        public = json.dumps(task)
        self.assertNotIn(self.session, public)
        self.assertNotIn(str(self.workspace), public)

    def test_missing_acceptance_or_context_is_rejected(self):
        for field in ('acceptance', 'context', 'delegation_reason'):
            value = copy.deepcopy(self.goal_value)
            value.pop(field)
            self.goal.write_text(json.dumps(value))
            with self.subTest(field=field), self.assertRaises(ProtocolError) as caught:
                self.prepare()
            self.assertEqual(caught.exception.code, 'GOAL_INCOMPLETE')

    def test_missing_handoff_is_rejected_before_git_or_remote_work(self):
        value = copy.deepcopy(self.goal_value)
        value.pop('handoff')
        self.goal.write_text(json.dumps(value))
        with patch('taskboard.publishing._git', side_effect=AssertionError('Incomplete handoff reached Git')), self.assertRaises(ProtocolError) as caught:
            self.prepare()
        self.assertEqual(caught.exception.code, 'PROMPT_CLOSURE_REQUIRED')
        self.assertNotIn('proposals', self.store.data())

    def test_missing_or_empty_handoff_notes_and_review_are_rejected_before_git(self):
        cases = []
        for field in ('non_goals', 'constraints', 'assumptions', 'environment', 'stop_conditions', 'review'):
            value = copy.deepcopy(self.goal_value)
            value['handoff'].pop(field)
            cases.append((f'missing {field}', value))
        for field in ('first_step', 'inputs', 'completion', 'blocking_questions'):
            value = copy.deepcopy(self.goal_value)
            value['handoff']['review'].pop(field)
            cases.append((f'missing review {field}', value))
        for field in ('first_step', 'inputs', 'completion'):
            value = copy.deepcopy(self.goal_value)
            value['handoff']['review'][field] = ' '
            cases.append((f'blank review {field}', value))
        for field, empty in (('environment', ''), ('stop_conditions', []), ('constraints', [''])):
            value = copy.deepcopy(self.goal_value)
            value['handoff'][field] = empty
            cases.append((f'empty {field}', value))
        for label, value in cases:
            with self.subTest(label=label):
                self.goal.write_text(json.dumps(value))
                with patch('taskboard.publishing._git', side_effect=AssertionError('Incomplete handoff reached Git')), self.assertRaises(ProtocolError) as caught:
                    self.prepare()
                self.assertEqual(caught.exception.code, 'INVALID_PAYLOAD')

    def test_declared_blockers_are_rejected_before_git_or_remote_work(self):
        value = copy.deepcopy(self.goal_value)
        value['handoff']['review']['blocking_questions'] = ['Where is the version source of truth?']
        self.goal.write_text(json.dumps(value))
        with patch('taskboard.publishing._git', side_effect=AssertionError('Blocked handoff reached Git')), self.assertRaises(ProtocolError) as caught:
            self.prepare()
        self.assertEqual(caught.exception.code, 'PROMPT_CLOSURE_BLOCKED')
        self.assertNotIn('proposals', self.store.data())

    def test_explicitly_empty_optional_notes_do_not_invent_assumptions(self):
        value = copy.deepcopy(self.goal_value)
        for field in ('non_goals', 'constraints', 'assumptions'):
            value['handoff'][field] = []
        self.goal.write_text(json.dumps(value))
        self.assertEqual(self.prepare()['task']['handoff'], value['handoff'])

    def test_dirty_tracked_staged_and_untracked_source_are_not_silently_dropped(self):
        for state in ('tracked', 'staged', 'untracked'):
            target = self.workspace / ('new.txt' if state == 'untracked' else 'src/app.py')
            target.write_text('needed unpublished input\n')
            if state == 'staged':
                self.git('add', '.')
            with self.subTest(state=state), self.assertRaises(ProtocolError) as caught:
                self.prepare()
            self.assertEqual(caught.exception.code, 'DIRTY_SOURCE')
            self.git('reset', '--hard', '-q', 'HEAD')
            if state == 'untracked':
                target.unlink()

    def test_goal_inside_source_requires_explicit_safe_helper_exclusion(self):
        self.goal = self.workspace / 'proposal-goal.json'
        self.goal.write_text(json.dumps(self.goal_value))
        with self.assertRaises(ProtocolError):
            self.prepare()
        value = json.loads(self.goal.read_text())
        value['excluded_paths'] = ['proposal-goal.json']
        self.goal.write_text(json.dumps(value))
        proposal = self.prepare()
        self.assertIn('proposal-goal.json', proposal['excluded_paths'])

    def test_exclusion_cannot_hide_dirty_source(self):
        (self.workspace / 'src/app.py').write_text('secret work\n')
        value = json.loads(self.goal.read_text())
        value['excluded_paths'] = ['src/app.py']
        self.goal.write_text(json.dumps(value))
        with self.assertRaises(ProtocolError) as caught:
            self.prepare()
        self.assertEqual(caught.exception.code, 'UNSAFE_EXCLUSION')

    def test_unpushed_commit_and_symlink_resource_are_rejected(self):
        self.git('commit', '--allow-empty', '-qm', 'Unpushed work')
        with self.assertRaises(ProtocolError) as caught:
            self.prepare()
        self.assertEqual(caught.exception.code, 'SOURCE_NOT_FETCHABLE')
        self.git('reset', '--hard', '-q', self.commit)
        (self.workspace / 'linked.md').symlink_to('context.md')
        self.git('add', '.')
        self.git('commit', '-qm', 'Symlink fixture')
        self.commit = self.git('rev-parse', 'HEAD').strip()
        with self.assertRaises(ProtocolError) as caught:
            self.prepare(resource_paths=['linked.md'])
        self.assertIn(caught.exception.code, ('UNSAFE_PATH', 'RESOURCE_NOT_FILE'))

    def test_private_remote_credentials_are_rejected_before_remote_read(self):
        self.git('remote', 'set-url', 'origin', 'https://secret:token@git.corp.example/company/source.git')
        with self.assertRaises(ProtocolError) as caught:
            self.prepare()
        self.assertEqual(caught.exception.code, 'INVALID_REPOSITORY')
        self.assertNotIn('secret', str(caught.exception))

    def test_absent_or_false_consent_does_not_contact_github(self):
        proposal = self.prepare()
        class NoNetwork:
            def __getattr__(self, name):
                raise AssertionError(f'Consent refusal reached GitHub: {name}')
        for value in (False, None, 'yes', 1):
            with self.subTest(value=value), self.assertRaises(ProtocolError) as caught:
                self.publisher.publish_proposal(self.store, NoNetwork(), proposal['id'], approved=value)
            self.assertEqual(caught.exception.code, 'PUBLISH_APPROVAL_REQUIRED')
        self.assertFalse(self.store.data()['proposals'][proposal['id']]['approved'])

    def test_approved_publish_binds_exact_session_and_retries_same_issue(self):
        proposal = self.prepare()
        client = FakeIssues()
        first = self.publisher.publish_proposal(self.store, client, proposal['id'], approved=True)
        second = self.publisher.publish_proposal(self.store, client, proposal['id'], approved=True)
        self.assertEqual(first['number'], second['number'])
        self.assertEqual(len(client.created), 1)
        self.assertEqual(self.store.binding(first['number'])['session_id'], self.session)
        self.assertEqual(parse_task_issue(client.created[0]['body']), proposal['task'])

    def test_mutated_proposal_digest_is_rejected_before_publication(self):
        proposal = self.prepare()
        self.store.update(lambda data: data['proposals'][proposal['id']]['task'].update({'prompt': 'changed after user review'}))
        with self.assertRaises(ProtocolError) as caught:
            self.publisher.publish_proposal(self.store, FakeIssues(), proposal['id'], approved=True)
        self.assertEqual(caught.exception.code, 'PROPOSAL_CHANGED')

    def test_mutated_reviewed_handoff_is_rejected_without_contacting_github(self):
        proposal = self.prepare()
        self.store.update(lambda data: data['proposals'][proposal['id']]['task']['handoff']['constraints'].append('An unreviewed new constraint.'))
        client = FakeIssues()
        with patch.object(client, 'user', side_effect=AssertionError('Changed proposal reached GitHub')), self.assertRaises(ProtocolError) as caught:
            self.publisher.publish_proposal(self.store, client, proposal['id'], approved=True)
        self.assertEqual(caught.exception.code, 'PROPOSAL_CHANGED')
        self.assertFalse(self.store.data()['proposals'][proposal['id']]['approved'])

    def test_old_or_blocked_local_proposal_cannot_publish_even_with_matching_digest(self):
        for variant, code in (('legacy', 'PROMPT_CLOSURE_REQUIRED'), ('blocked', 'PROMPT_CLOSURE_BLOCKED')):
            with self.subTest(variant=variant):
                proposal = self.prepare()
                def change(data):
                    saved = data['proposals'][proposal['id']]
                    if variant == 'legacy':
                        saved['task'].pop('handoff')
                    else:
                        saved['task']['handoff']['review']['blocking_questions'] = ['Missing resource location.']
                    saved['digest'] = task_digest(saved['task'])
                    saved['fingerprint'] = self.publisher._fingerprint(saved['task'], saved['workspace'], saved['provider'], saved['session'], saved['board'])
                self.store.update(change)
                client = FakeIssues()
                with patch.object(client, 'user', side_effect=AssertionError('Unready proposal reached GitHub')), self.assertRaises(ProtocolError) as caught:
                    self.publisher.publish_proposal(self.store, client, proposal['id'], approved=True)
                self.assertEqual(caught.exception.code, code)
                self.assertFalse(self.store.data()['proposals'][proposal['id']]['approved'])
                self.assertEqual(self.store.data()['publications'], {})

    def test_uncertain_creation_reconciles_without_creating_duplicate(self):
        proposal = self.prepare()
        client = FakeIssues()
        client.uncertain = True
        with self.assertRaises(OSError):
            self.publisher.publish_proposal(self.store, client, proposal['id'], approved=True)
        result = self.publisher.publish_proposal(self.store, client, proposal['id'], approved=True)
        self.assertEqual(result['number'], 1)
        self.assertEqual(len(client.created), 1)

    def test_exact_installer_exclusions_allow_preparation_without_hiding_other_edits(self):
        from taskboard.integration import callback_context, handle_hook, install_integration
        install_integration('codex', self.workspace, self.store)
        handle_hook('codex', self.workspace, self.store, {'hook_event_name': 'SessionStart', 'session_id': self.session, 'cwd': str(self.workspace)})
        callback_id = next(iter(self.store.data()['callbacks']))
        callback = callback_context(self.store, callback_id, provider='codex', project=self.workspace)
        goal = json.loads(self.goal.read_text())
        goal['excluded_paths'] = callback['excluded_paths']
        self.goal.write_text(json.dumps(goal))
        self.assertFalse(self.prepare()['approved'])
        with (self.workspace / 'AGENTS.md').open('a') as stream:
            stream.write('An unrelated local instruction change.\n')
        with self.assertRaises(ProtocolError) as caught:
            self.prepare()
        self.assertEqual(caught.exception.code, 'UNSAFE_EXCLUSION')

    def test_installer_exclusions_cannot_hide_preexisting_dirty_rules(self):
        from taskboard.integration import callback_context, handle_hook, install_integration
        (self.workspace / 'AGENTS.md').write_text('Uncommitted source instructions\n')
        install_integration('codex', self.workspace, self.store)
        handle_hook('codex', self.workspace, self.store, {'hook_event_name': 'SessionStart', 'session_id': self.session, 'cwd': str(self.workspace)})
        callback_id = next(iter(self.store.data()['callbacks']))
        goal = json.loads(self.goal.read_text())
        goal['excluded_paths'] = callback_context(self.store, callback_id, provider='codex', project=self.workspace)['excluded_paths']
        self.goal.write_text(json.dumps(goal))
        with self.assertRaises(ProtocolError) as caught:
            self.prepare()
        self.assertEqual(caught.exception.code, 'UNSAFE_EXCLUSION')

    def test_committed_integrations_and_both_providers_keep_preparation_usable(self):
        from taskboard.integration import callback_context, handle_hook, install_integration
        install_integration('codex', self.workspace, self.store)
        install_integration('claude', self.workspace, self.store)
        handle_hook('codex', self.workspace, self.store, {'hook_event_name': 'SessionStart', 'session_id': self.session, 'cwd': str(self.workspace)})
        callback_id = next(iter(self.store.data()['callbacks']))
        goal = json.loads(self.goal.read_text())
        goal['excluded_paths'] = callback_context(self.store, callback_id, provider='codex', project=self.workspace)['excluded_paths']
        self.goal.write_text(json.dumps(goal))
        self.assertFalse(self.prepare()['approved'])
        self.git('add', '.')
        self.git('commit', '-qm', 'Reviewed publisher integration')
        self.commit = self.git('rev-parse', 'HEAD').strip()
        self.assertFalse(self.prepare()['approved'])


class FakeIssues:
    repo = 'company/tasks'
    hostname = 'git.corp.example'

    def __init__(self):
        self.created = []
        self.uncertain = False

    def user(self):
        return 'publisher'

    def issues(self):
        return copy.deepcopy(self.created)

    def create_issue(self, title, body):
        item = {'number': len(self.created) + 1, 'html_url': 'https://git.corp.example/company/tasks/issues/1', 'title': title, 'body': body, 'user': {'login': 'publisher'}}
        self.created.append(item)
        if self.uncertain:
            self.uncertain = False
            raise OSError('response lost after server created issue')
        return item


if __name__ == '__main__':
    unittest.main()
