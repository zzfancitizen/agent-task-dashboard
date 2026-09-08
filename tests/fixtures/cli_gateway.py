"""Deterministic fake gh process boundary, backed by the real task reducer."""
import base64
import copy
import json
from pathlib import Path
import time
from urllib.parse import urlsplit

from taskboard.protocol import ProtocolError, parse_command, parse_task_issue, validate_publication
from taskboard.state import new_task
from taskboard.github import GitHub


class Gateway:
    def __init__(self):
        self.hostname = 'git.corp.example'
        self.repo = 'company/tasks'
        self.actor = 'alice'
        self.permission = 'write'
        self.issue_rows = []
        self.state = {'schema_version': 1, 'tasks': {}, 'updated_at': 0}
        self.comments = []
        self.releases = {}
        self.assets = {}
        self.operations = []
        self.delay = False
        self.delay_op = None
        self.lose_create = False
        self.lose_comment = False
        self.uploads = 0
        self.fail_upload_at = None
        self.lose_op = None
        self.controller_depth = 0

    def process_pending(self, number):
        from taskboard.controller import process_command
        self.controller_depth += 1
        try:
            rows = [row for row in self.comments if row.get('issue_number', number) == number]
            for comment in rows:
                try:
                    command = parse_command(comment['body'])
                except ProtocolError:
                    continue
                if command is None:
                    continue
                self.state['tasks'][str(number)] = process_command(self.state['tasks'][str(number)], command, rows, GitHub(self.repo, self.hostname), actor=comment['user']['login'], comment_id=comment['id'], now=int(time.time()))
        finally:
            self.controller_depth -= 1

    def run(self, argv, *, data=None, binary=False):
        self.operations.append(list(argv))
        if argv[:3] == ['gh', 'release', 'upload']:
            if self.permission == 'read' and not self.controller_depth:
                raise ProtocolError('GITHUB_403', 'A read-only participant cannot upload Releases directly.')
            tag, file = argv[3], Path(argv[4])
            self.uploads += 1
            if self.uploads == self.fail_upload_at:
                raise ProtocolError('GITHUB_OUTCOME_UNKNOWN', 'Fixture lost upload connection')
            release = self.releases[tag]
            identifier = len(self.assets) + 1
            self.assets[identifier] = file.read_bytes()
            release['assets'].append({'id': identifier, 'name': file.name, 'size': file.stat().st_size, 'browser_download_url': f'https://{self.hostname}/{self.repo}/releases/download/{tag}/{file.name}'})
            return ''
        if argv[:2] != ['gh', 'api'] or argv[argv.index('--hostname') + 1] != self.hostname:
            raise AssertionError(f'Unexpected external command: {argv}')
        method = argv[argv.index('--method') + 1] if '--method' in argv else 'GET'
        index = argv.index('--method') + 2 if '--method' in argv else argv.index('--hostname') + 2
        path = urlsplit(argv[index]).path
        body = json.loads(data) if data else None
        base = f'repos/{self.repo}'
        if path == 'user':
            result = {'login': self.actor}
        elif path.startswith(base + '/collaborators/') and path.endswith('/permission'):
            result = {'permission': self.permission}
        elif path == base + '/contents/state.json':
            result = {'sha': 'abc', 'encoding': 'base64', 'content': base64.b64encode(json.dumps(self.state).encode()).decode()}
        elif path == base + '/issues' and method == 'GET':
            result = self.issue_rows
        elif path == base + '/issues' and method == 'POST':
            issue = {'number': len(self.issue_rows) + 1, 'html_url': f'https://{self.hostname}/{self.repo}/issues/{len(self.issue_rows) + 1}', 'title': body['title'], 'body': body['body'], 'user': {'login': self.actor}, 'created_at': '2026-09-08T00:00:00Z'}
            self.issue_rows.append(issue)
            self.state['tasks'][str(issue['number'])] = new_task(issue, validate_publication(parse_task_issue(issue['body'])), int(time.time()))
            if self.lose_create:
                self.lose_create = False
                raise ProtocolError('GITHUB_OUTCOME_UNKNOWN', 'Fixture lost create response')
            result = issue
        elif path.startswith(base + '/issues/'):
            parts = path.split('/')
            number = int(parts[4])
            if len(parts) == 6 and parts[5] == 'comments':
                if method == 'POST':
                    comment = {'id': len(self.comments) + 1, 'issue_number': number, 'body': body['body'], 'user': {'login': self.actor, 'type': 'User'}, 'created_at': '2026-09-08T00:00:00Z', 'updated_at': '2026-09-08T00:00:00Z'}
                    self.comments.append(comment)
                    try:
                        command = parse_command(body['body'])
                        op = command['op'] if command else None
                    except ProtocolError:
                        op = None
                    if op and not self.delay and op != self.delay_op:
                        self.process_pending(number)
                    if self.lose_comment or op is not None and op == self.lose_op:
                        self.lose_comment = False
                        self.lose_op = None
                        raise ProtocolError('GITHUB_OUTCOME_UNKNOWN', 'Fixture lost comment response')
                    result = comment
                else:
                    result = [row for row in self.comments if row.get('issue_number', number) == number]
            else:
                result = self.issue_rows[number - 1]
        elif path == base + '/releases' and method == 'POST':
            if self.permission == 'read' and not self.controller_depth:
                raise ProtocolError('GITHUB_403', 'A read-only participant cannot create Releases directly.')
            result = {'id': len(self.releases) + 1, 'tag_name': body['tag_name'], 'assets': []}
            self.releases[body['tag_name']] = result
        elif path.startswith(base + '/releases/tags/'):
            tag = path.rsplit('/', 1)[1]
            if tag not in self.releases:
                raise ProtocolError('GITHUB_404', 'Missing fixture release')
            result = self.releases[tag]
        elif path.startswith(base + '/releases/assets/'):
            return self.assets[int(path.rsplit('/', 1)[1])]
        else:
            raise AssertionError(f'Unexpected GitHub request: {method} {path}')
        return json.dumps(copy.deepcopy(result))
