"""Private, crash-safe local state. Nothing in this module is published to GitHub."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Callable
import uuid

from .protocol import ProtocolError


def repository_name(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*', value):
        raise ProtocolError('INVALID_REPOSITORY', 'Repository must be owner/repo.')
    if value.endswith('.git'):
        value = value[:-4]
    return value


def hostname_name(value: str) -> str:
    if not isinstance(value, str) or len(value) > 253 or not all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label) for label in value.split('.')):
        raise ProtocolError('INVALID_HOSTNAME', 'Hostname must be a DNS name without a port, path, or credentials.')
    return value.lower()


def session_id(value: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ProtocolError('INVALID_SESSION', 'Supply an explicit provider session UUID; session discovery is not supported.') from exc
    if str(parsed) != value.lower():
        raise ProtocolError('INVALID_SESSION', 'Session ID must use UUID notation.')
    return str(parsed)


def regular_path(path: Path) -> None:
    """Reject existing symlinks, including parent directories, before local writes."""
    for part in (path, *path.parents):
        if part.is_symlink():
            # macOS exposes these OS-owned paths as canonical /private aliases.
            if str(part) in {'/var', '/tmp', '/etc'} and part.resolve() == Path('/private') / part.name:
                continue
            raise ProtocolError('UNSAFE_PATH', f'Symlinks are not allowed in local state paths: {part}')


def atomic_json(path: Path, value: dict) -> None:
    regular_path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path: Path, default: dict | None = None) -> dict:
    regular_path(path)
    try:
        with path.open(encoding='utf-8') as stream:
            result = json.load(stream)
    except FileNotFoundError:
        if default is not None:
            return default
        raise ProtocolError('NOT_INITIALIZED', 'Run taskboard init with --repo and --hostname first.') from None
    except (ValueError, OSError) as exc:
        raise ProtocolError('LOCAL_STATE_INVALID', f'Cannot read local JSON: {path}') from exc
    if not isinstance(result, dict):
        raise ProtocolError('LOCAL_STATE_INVALID', f'Expected an object in {path}.')
    return result


class LocalStore:
    def __init__(self, home: Path | str):
        self.home = Path(home).expanduser().absolute()
        regular_path(self.home)

    @contextmanager
    def exclusive(self, name: str, *, blocking: bool = False):
        regular_path(self.home)
        self.home.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory = self.home / 'locks'
        regular_path(directory)
        directory.mkdir(mode=0o700, exist_ok=True)
        path = directory / (hashlib.sha256(name.encode()).hexdigest() + '.lock')
        regular_path(path)
        fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError as exc:
                raise ProtocolError('LOCAL_BUSY', 'Another local Taskboard process holds this operation or session lock.') from exc
            yield
        finally:
            os.close(fd)

    def initialize(self, repo: str, hostname: str, allowed_repos: list[str], agent: str = 'codex') -> dict:
        repo, hostname = repository_name(repo), hostname_name(hostname)
        if agent not in {'codex', 'claude'}:
            raise ProtocolError('INVALID_AGENT', 'Agent must be codex or claude.')
        allowed = list(dict.fromkeys([repo, *(repository_name(value) for value in allowed_repos)]))
        config = {'schema_version': 1, 'repo': repo, 'hostname': hostname, 'allowed_repos': allowed, 'agent': agent}
        with self.exclusive('local-state', blocking=True):
            prior = read_json(self.home / 'config.json', {})
            if prior and (prior.get('repo'), prior.get('hostname')) != (repo, hostname):
                raise ProtocolError('CONFIG_CONFLICT', 'This local home belongs to another board. Choose a different --home.')
            if prior:
                config['allowed_repos'] = list(dict.fromkeys([*prior.get('allowed_repos', []), *allowed]))
            atomic_json(self.home / 'config.json', config)
        return config

    def config(self) -> dict:
        result = read_json(self.home / 'config.json')
        if result.get('schema_version') != 1 or result.get('agent') not in {'codex', 'claude'}:
            raise ProtocolError('LOCAL_STATE_INVALID', 'Unsupported local config.')
        result['repo'] = repository_name(result.get('repo'))
        result['hostname'] = hostname_name(result.get('hostname'))
        allowed = result.get('allowed_repos')
        if not isinstance(allowed, list) or not allowed:
            raise ProtocolError('LOCAL_STATE_INVALID', 'Local repository allowlist is missing.')
        result['allowed_repos'] = [repository_name(value) for value in allowed]
        return result

    def data(self) -> dict:
        result = read_json(self.home / 'local.json', {'schema_version': 1, 'bindings': {}, 'inbox': {}, 'requests': {}, 'publications': {}, 'runs': {}, 'origins': {}})
        if result.get('schema_version') != 1:
            raise ProtocolError('LOCAL_STATE_INVALID', 'Unsupported local state version.')
        result.setdefault('origins', {})
        for field in ('bindings', 'inbox', 'requests', 'publications', 'runs', 'origins'):
            if not isinstance(result.get(field), dict):
                raise ProtocolError('LOCAL_STATE_INVALID', f'Invalid local {field} state.')
        return result

    def update(self, operation: Callable[[dict], object]):
        with self.exclusive('local-state', blocking=True):
            current = self.data()
            result = operation(current)
            atomic_json(self.home / 'local.json', current)
            return result

    def bind(self, issue: int, agent: str, provider_session: str, workspace: Path | str, digest: str) -> None:
        if agent not in {'codex', 'claude'}:
            raise ProtocolError('INVALID_AGENT', 'Agent must be codex or claude.')
        provider_session = session_id(provider_session)
        workspace = Path(workspace).expanduser().absolute()
        regular_path(workspace)
        if not workspace.is_dir():
            raise ProtocolError('INVALID_WORKSPACE', 'The explicitly bound workspace must already exist.')
        binding = {'agent': agent, 'session_id': provider_session, 'workspace': str(workspace), 'task_digest': digest}
        def save(state):
            existing = state['bindings'].get(str(issue))
            if existing and any(existing.get(key) != value for key, value in binding.items()):
                raise ProtocolError('BINDING_CONFLICT', 'The issue already has a different original-session binding.')
            state['bindings'][str(issue)] = {**(existing or {}), **binding}
        self.update(save)

    def binding(self, issue: int) -> dict | None:
        return self.data()['bindings'].get(str(issue))

    def remember_result(self, issue: int, result_id: str, manifest: dict) -> dict:
        session_id(result_id)
        def remember(state):
            inbox = state['inbox']
            if result_id not in inbox:
                inbox[result_id] = {'issue': issue, 'result_id': result_id, 'manifest': manifest, 'delivery': 'pending', 'downloaded': False}
            elif inbox[result_id]['issue'] != issue or inbox[result_id]['manifest'] != manifest:
                raise ProtocolError('RESULT_CONFLICT', 'A saved result ID now refers to different content.')
            return inbox[result_id]
        return self.update(remember)

    def begin_delivery(self, result_id: str) -> bool:
        def begin(state):
            item = state['inbox'][result_id]
            if item['delivery'] == 'delivering':
                item['delivery'] = 'unknown'
                return False
            if item['delivery'] != 'pending':
                return False
            item['delivery'] = 'delivering'
            return True
        return self.update(begin)

    def finish_delivery(self, result_id: str, successful: bool) -> None:
        def finish(state):
            state['inbox'][result_id]['delivery'] = 'delivered' if successful else 'unknown'
        self.update(finish)
