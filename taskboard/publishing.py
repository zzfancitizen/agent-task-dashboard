"""Prepare complete committed-input proposals, then publish after user consent."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
import uuid

from .local import LocalStore, hostname_name, regular_path, repository_name, session_id
from .protocol import ProtocolError, format_task_issue, parse_task_issue, task_digest, validate_task
from .workspace import _git, checked_path


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _fingerprint(task: dict, workspace: str, provider: str, session: str, board: dict) -> str:
    content = {key: value for key, value in task.items() if key != 'task_id'}
    return _hash(json.dumps([content, workspace, provider, session, board], ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode())


def _goal(path: Path) -> dict:
    regular_path(path)
    try:
        if path.stat().st_size > 32 * 1024:
            raise ProtocolError('GOAL_INCOMPLETE', 'The local goal JSON must be at most 32 KiB.')
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError) as exc:
        raise ProtocolError('GOAL_INCOMPLETE', 'Supply a UTF-8 goal JSON with goal, context, acceptance, and delegation_reason.') from exc
    required = {'goal', 'context', 'acceptance', 'delegation_reason'}
    if not isinstance(value, dict) or not required <= value.keys() or not value.keys() <= required | {'required_outputs', 'excluded_paths'}:
        raise ProtocolError('GOAL_INCOMPLETE', 'Goal JSON requires goal, context, acceptance, and delegation_reason; optional fields are required_outputs and excluded_paths.')
    for field in ('goal', 'context', 'delegation_reason'):
        if not isinstance(value[field], str) or not value[field].strip() or '\x00' in value[field]:
            raise ProtocolError('GOAL_INCOMPLETE', f'Supply nonempty {field} from the original conversation.')
    for field in ('acceptance', 'required_outputs', 'excluded_paths'):
        entries = value.get(field, [])
        if not isinstance(entries, list) or any(not isinstance(item, str) or not item.strip() for item in entries) or (field == 'acceptance' and not entries):
            raise ProtocolError('GOAL_INCOMPLETE', f'{field} must contain nonempty strings; acceptance requires at least one criterion.')
    return value


def _source(root: Path, hostname: str) -> tuple[str, str]:
    names = _git(root, 'remote').stdout.decode().splitlines()
    remote = 'origin' if 'origin' in names else names[0] if len(names) == 1 else None
    if remote is None:
        raise ProtocolError('SOURCE_REMOTE_REQUIRED', 'Configure an origin remote, or a single unambiguous GitHub remote.')
    raw = _git(root, 'remote', 'get-url', remote).stdout.decode().strip()
    scp = re.fullmatch(r'git@([^:/]+):([^\s]+)', raw)
    if scp:
        host, path = scp.groups()
    else:
        try:
            parsed = urlsplit(raw)
            valid = parsed.scheme in {'https', 'ssh'} and parsed.password is None and not parsed.port and not parsed.query and not parsed.fragment
            valid = valid and (parsed.username is None if parsed.scheme == 'https' else parsed.username in {None, 'git'})
            host, path = parsed.hostname or '', parsed.path.lstrip('/')
        except ValueError:
            valid = False
        if not valid:
            raise ProtocolError('INVALID_REPOSITORY', 'The source remote must be a GitHub HTTPS or SSH URL without credentials or a custom port.')
    host = hostname_name(host)
    repo = repository_name(path)
    if host != hostname:
        raise ProtocolError('SOURCE_HOST_MISMATCH', 'The source must use the configured company GitHub host.')
    return f'https://{host}/{repo}', remote


def _fetchable(root: Path, remote: str, commit: str) -> None:
    advertised = _git(root, 'ls-remote', '--refs', remote, check=False)
    if advertised.returncode:
        raise ProtocolError('SOURCE_NOT_FETCHABLE', 'Cannot verify source access. Check Git authentication and repository access; no proposal was published.')
    for record in advertised.stdout.decode('utf-8', errors='replace').splitlines():
        candidate = record.split('\t', 1)[0]
        if not re.fullmatch(r'[0-9a-f]{40}', candidate):
            continue
        if candidate == commit or _git(root, 'merge-base', '--is-ancestor', commit, candidate, check=False).returncode == 0:
            return
    raise ProtocolError('SOURCE_NOT_FETCHABLE', 'The source commit is not reachable from a verified remote ref. Publish reviewed source changes through the project workflow before delegating them.')


def _at_commit(root: Path, commit: str, path: str) -> bytes | None:
    result = _git(root, 'show', f'{commit}:{path}', check=False)
    return result.stdout if result.returncode == 0 else None


def _assert_clean(store: LocalStore, root: Path, commit: str, goal_path: Path, exclusions: list[str], inputs: list[str], write_paths: list[str]) -> None:
    allowed = set()
    manifests = store.data().get('integrations', {})
    installed = {}
    for item in manifests.values():
        if item.get('project') == str(root):
            installed.update(item.get('files', {}))
    for name in exclusions:
        path = checked_path(root, name)
        baseline = _at_commit(root, commit, name)
        if path.is_file() and baseline == path.read_bytes():
            continue
        if name in inputs or any(name == scope.rstrip('/') or name.startswith(scope.rstrip('/') + '/') for scope in write_paths):
            raise ProtocolError('UNSAFE_EXCLUSION', 'A task input or allowed write path cannot be excluded from the dirty-source check.')
        if path == goal_path and baseline is None and path.is_file():
            allowed.add(name)
            continue
        entry = installed.get(name)
        if entry and path.is_file() and _hash(path.read_bytes()) == entry['sha256'] and (None if baseline is None else _hash(baseline)) == entry['baseline_sha256']:
            allowed.add(name)
            continue
        raise ProtocolError('UNSAFE_EXCLUSION', 'Only the untracked goal helper or unchanged installed integration files can be explicitly excluded.')
    # All changed source is relevant by default. Renames expose both paths via
    # name-only diff; untracked files are included without reading their content.
    changed = set()
    for argv in [('diff', '--name-only', '-z', 'HEAD', '--'), ('ls-files', '--others', '--exclude-standard', '-z')]:
        changed.update(value.decode('utf-8') for value in _git(root, *argv).stdout.split(b'\0') if value)
    remaining = sorted(changed - allowed)
    if remaining:
        raise ProtocolError('DIRTY_SOURCE', 'Committed input is required; these changes would be missing: ' + ', '.join(remaining[:12]))


def prepare_proposal(store: LocalStore, *, workspace: Path, goal_file: Path, title: str, provider: str, session: str, resource_paths: list[str] | None = None, write_paths: list[str] | None = None, commands: list[list[str]] | None = None, size: str = 'M', category: str = 'code') -> dict:
    """Save an immutable local proposal; goal JSON supplies intent and acceptance."""
    if provider not in {'codex', 'claude'}:
        raise ProtocolError('INVALID_AGENT', 'Provider must be codex or claude.')
    session = session_id(session)
    workspace = Path(workspace).expanduser().absolute()
    regular_path(workspace)
    if not workspace.is_dir():
        raise ProtocolError('INVALID_WORKSPACE', 'The original session workspace must exist.')
    root = Path(_git(workspace, 'rev-parse', '--show-toplevel').stdout.decode().strip()).resolve()
    goal_path = Path(goal_file).expanduser().absolute()
    goal = _goal(goal_path)
    config = store.config()
    repository, remote = _source(root, config['hostname'])
    commit = _git(root, 'rev-parse', '--verify', 'HEAD^{commit}').stdout.decode().strip()
    resources = list(dict.fromkeys(resource_paths or []))
    writes = list(dict.fromkeys(write_paths or []))
    for name in [*resources, *writes]:
        checked_path(root, name)
    exclusions = list(dict.fromkeys(goal.get('excluded_paths', [])))
    _assert_clean(store, root, commit, goal_path, exclusions, resources, writes)
    _fetchable(root, remote, commit)
    resource_specs = []
    for name in resources:
        raw = _git(root, 'ls-tree', '-z', commit, '--', name).stdout
        entries = [entry for entry in raw.split(b'\0') if entry]
        if len(entries) != 1 or b'\t' not in entries[0]:
            raise ProtocolError('RESOURCE_NOT_FILE', 'Every resource must be a committed regular file.')
        mode, resource_name = entries[0].split(b'\t', 1)
        if mode.split()[0] not in {b'100644', b'100755'} or resource_name.decode() != name:
            raise ProtocolError('RESOURCE_NOT_FILE', 'Resource symlinks, submodules, and directories are not supported.')
        destination = 'taskboard-inputs/' + name
        if _at_commit(root, commit, destination) is not None:
            raise ProtocolError('RESOURCE_COLLISION', f'The committed source already contains resource destination {destination}.')
        content = _git(root, 'show', f'{commit}:{name}').stdout
        resource_specs.append({'type': 'git_file', 'repository': repository, 'commit': commit, 'path': name, 'destination': destination, 'sha256': _hash(content)})
    prompt = 'Goal\n' + goal['goal'].strip() + '\n\nContext\n' + goal['context'].strip() + '\n\nAcceptance criteria\n' + '\n'.join('- ' + item.strip() for item in goal['acceptance'])
    if resource_specs:
        prompt += '\n\nPinned supporting inputs\n' + '\n'.join('- ' + item['destination'] for item in resource_specs)
    task = validate_task({'schema_version': 1, 'task_id': str(uuid.uuid4()), 'revision': 1, 'mode': 'subtask', 'title': title, 'prompt': prompt, 'delegation_reason': goal['delegation_reason'], 'source': {'repository': repository, 'base_commit': commit, 'workspace_patch': None, 'write_paths': writes}, 'resources': resource_specs, 'execution': {'compatible_agents': ['codex', 'claude'], 'timeout_seconds': 3600, 'max_attempts': 3}, 'acceptance': {'commands': commands or [], 'required_outputs': goal.get('required_outputs', ['changes.patch', 'summary.md', 'verification.json']), 'review_notes': '\n'.join(goal['acceptance'])}, 'size': size, 'category': category})
    board = {'repository': config['repo'], 'hostname': config['hostname']}
    fingerprint = _fingerprint(task, str(workspace), provider, session, board)
    def save(data):
        proposals = data.setdefault('proposals', {})
        for existing in proposals.values():
            if existing.get('fingerprint') == fingerprint:
                return copy.deepcopy(existing)
        proposal = {'id': str(uuid.uuid4()), 'task': task, 'digest': task_digest(task), 'workspace': str(workspace), 'provider': provider, 'session': session, 'board': board, 'fingerprint': fingerprint, 'excluded_paths': exclusions, 'approved': False}
        proposals[proposal['id']] = proposal
        return copy.deepcopy(proposal)
    return store.update(save)


def publish_proposal(store: LocalStore, client, proposal_id: str, *, approved: bool = False) -> dict:
    """Publish only after an affirmative caller decision; retry immutable content."""
    if approved is not True:
        raise ProtocolError('PUBLISH_APPROVAL_REQUIRED', 'Show the prepared proposal and ask the user whether to publish. Supply approval only after an explicit affirmative reply.')
    proposal_id = session_id(proposal_id)
    with store.exclusive('proposal:' + proposal_id):
        proposal = store.data().get('proposals', {}).get(proposal_id)
        if proposal is None:
            raise ProtocolError('PROPOSAL_NOT_FOUND', 'Prepare this proposal locally before publishing.')
        task = validate_task(proposal['task'])
        digest = task_digest(task)
        if digest != proposal['digest'] or _fingerprint(task, proposal['workspace'], proposal['provider'], proposal['session'], proposal['board']) != proposal['fingerprint']:
            raise ProtocolError('PROPOSAL_CHANGED', 'The reviewed proposal changed. Prepare and review a new proposal before publishing.')
        config = store.config()
        if proposal['board'] != {'repository': config['repo'], 'hostname': config['hostname']} or (client.repo, client.hostname) != (config['repo'], config['hostname']):
            raise ProtocolError('PROPOSAL_BOARD_CHANGED', 'The proposal belongs to a different GitHub board.')
        session_id(proposal['session'])
        regular_path(Path(proposal['workspace']))
        if not Path(proposal['workspace']).is_dir():
            raise ProtocolError('INVALID_WORKSPACE', 'The original session workspace no longer exists.')
        store.update(lambda data: data['proposals'][proposal_id].update({'approved': True}))
        key = f'{task["task_id"]}:{task["revision"]}'
        with store.exclusive('publish:' + task['task_id']):
            actor = client.user()
            found = None
            for issue in client.issues():
                try:
                    existing = parse_task_issue(issue.get('body') or '')
                except ProtocolError:
                    continue
                if existing['task_id'] != task['task_id']:
                    continue
                if task_digest(existing) != digest or issue.get('user', {}).get('login', '').casefold() != actor.casefold():
                    raise ProtocolError('TASK_CONFLICT', 'This task ID already belongs to another author or different immutable content.')
                if found is not None:
                    raise ProtocolError('DUPLICATE_TASK', 'More than one Issue has this task ID; reconcile before continuing.')
                found = issue
            prior = store.data()['publications'].get(key)
            if found is None:
                if prior and prior.get('phase') in {'posting', 'unknown', 'published'}:
                    raise ProtocolError('PUBLISH_UNKNOWN', 'A prior create may have succeeded but is not visible. Inspect GitHub before retrying; do not create a new proposal to bypass this check.')
                store.update(lambda data: data['publications'].update({key: {'digest': digest, 'phase': 'posting'}}))
                try:
                    found = client.create_issue(task['title'], format_task_issue(task))
                except BaseException:
                    store.update(lambda data: data['publications'][key].update({'phase': 'unknown'}))
                    raise
            store.bind(found['number'], proposal['provider'], proposal['session'], proposal['workspace'], digest)
            result = {'number': found['number'], 'html_url': found['html_url'], 'task_id': task['task_id'], 'proposal_id': proposal_id, 'digest': digest, 'status': 'pending'}
            def finish(data):
                data['publications'][key] = {'digest': digest, 'phase': 'published', 'issue': found['number']}
                data['proposals'][proposal_id]['publication'] = result
            store.update(finish)
            return result
