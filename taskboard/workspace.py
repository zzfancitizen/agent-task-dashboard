"""Pinned checkouts and deterministic, scope-checked patch export."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
from urllib.parse import urlsplit

from .local import hostname_name, regular_path, repository_name
from .protocol import ProtocolError


def repository_url(value: str, config: dict) -> str:
    if not isinstance(value, str):
        raise ProtocolError('INVALID_REPOSITORY', 'Source must be an HTTPS repository URL.')
    parsed = urlsplit(value)
    try:
        hostname = hostname_name(parsed.hostname or '')
        port = parsed.port
    except ValueError as exc:
        raise ProtocolError('INVALID_REPOSITORY', 'Invalid repository URL.') from exc
    if parsed.scheme != 'https' or parsed.username or parsed.password or port or parsed.query or parsed.fragment or '%' in parsed.path:
        raise ProtocolError('INVALID_REPOSITORY', 'Use an HTTPS repository URL without credentials, a port, or query.')
    repo = repository_name(parsed.path.strip('/'))
    if parsed.path not in {f'/{repo}', f'/{repo}/', f'/{repo}.git'}:
        raise ProtocolError('INVALID_REPOSITORY', 'Repository URL must identify one repository.')
    if hostname != config['hostname'] or repo.lower() not in {allowed.lower() for allowed in config['allowed_repos']}:
        raise ProtocolError('REPOSITORY_NOT_ALLOWED', 'Repository is not in this local home\'s configured host and allowlist. Add it explicitly with init --allow-repo.')
    return f'https://{hostname}/{repo}'


def checked_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or any(char in relative for char in '\\:<>"|?*') or any(ord(char) < 32 or ord(char) == 127 for char in relative):
        raise ProtocolError('UNSAFE_PATH', 'Paths must be nonempty relative POSIX paths.')
    value = relative[:-1] if relative.endswith('/') else relative
    parts = value.split('/')
    if any(part in {'', '.', '..'} or part.lower() == '.git' or part.endswith(('.', ' ')) or re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\..*)?', part) for part in parts) or PurePosixPath(value).is_absolute():
        raise ProtocolError('UNSAFE_PATH', f'Unsafe relative path: {relative!r}')
    path = root.joinpath(*parts)
    regular_path(path)
    return path


def _commit(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-fA-F]{40}', value):
        raise ProtocolError('INVALID_COMMIT', 'A full 40-character commit hash is required.')
    return value.lower()


def _environment(hostname: str | None = None) -> dict:
    result = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    result['GIT_TERMINAL_PROMPT'] = '0'
    if hostname:
        result['GH_HOST'] = hostname
    return result


def _run(argv: list[str], *, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(argv, capture_output=True, env=env or _environment(), timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProtocolError('WORKSPACE_COMMAND_FAILED', f'Cannot complete {argv[0]} for the pinned workspace.') from exc
    if check and result.returncode:
        raise ProtocolError('WORKSPACE_COMMAND_FAILED', f'{argv[0]} failed for the pinned workspace (exit {result.returncode}). Check gh authentication and source access.')
    return result


def _git(root: Path, *argv: str, check: bool = True) -> subprocess.CompletedProcess:
    return _run(['git', '-c', f'core.hooksPath={os.devnull}', '-c', 'protocol.file.allow=never', '-c', 'core.fsmonitor=false', '-C', str(root), *argv], check=check)


def _clone(url: str, destination: Path, hostname: str) -> None:
    regular_path(destination)
    _run(['gh', 'repo', 'clone', url, str(destination), '--no-upstream', '--', '--no-checkout'], env=_environment(hostname))
    if not destination.is_dir() or (destination / '.git').is_symlink() or not (destination / '.git').is_dir():
        raise ProtocolError('UNSAFE_PATH', 'Expected a new standalone Git checkout.')


def _ensure_commit(root: Path, commit: str) -> None:
    if _git(root, 'cat-file', '-e', f'{commit}^{{commit}}', check=False).returncode:
        _git(root, 'fetch', '--no-tags', 'origin', commit)
    _git(root, 'cat-file', '-e', f'{commit}^{{commit}}')


def _no_symlinks(root: Path) -> None:
    regular_path(root)
    metadata = root / '.git'
    regular_path(metadata)
    if not metadata.is_dir():
        raise ProtocolError('UNSAFE_PATH', 'Workspace Git metadata was replaced.')
    for directory, directories, files in os.walk(root, followlinks=False):
        if Path(directory) == root:
            directories[:] = [name for name in directories if name != '.git']
        for name in [*directories, *files]:
            path = Path(directory) / name
            if path.is_symlink():
                raise ProtocolError('UNSAFE_PATH', f'Workspace symlinks are not supported: {path.relative_to(root)}')


def prepare_workspace(task: dict, config: dict, run_dir: Path) -> Path:
    source = task['source']
    if source.get('workspace_patch') is not None:
        raise ProtocolError('UNSUPPORTED_PATCH', 'workspace_patch is not supported by this version.')
    source_url = repository_url(source['repository'], config)
    base = _commit(source['base_commit'])
    regular_path(run_dir)
    workspace = run_dir / 'workspace'
    for relative in source['write_paths']:
        checked_path(workspace, relative)
    for resource in task['resources']:
        repository_url(resource['repository'], config)
        _commit(resource['commit'])
        checked_path(workspace, resource['path'])
        checked_path(workspace, resource['destination'])
    if workspace.exists():
        raise ProtocolError('WORKSPACE_EXISTS', 'This attempt already has a workspace; do not replay an uncertain execution.')
    run_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    _clone(source_url, workspace, config['hostname'])
    _ensure_commit(workspace, base)
    _git(workspace, 'checkout', '--detach', base)
    _no_symlinks(workspace)
    repositories = {source_url: workspace}
    for resource in task['resources']:
        url = repository_url(resource['repository'], config)
        commit = _commit(resource['commit'])
        if url not in repositories:
            cache = run_dir / ('resource-' + hashlib.sha256(url.encode()).hexdigest()[:16])
            _clone(url, cache, config['hostname'])
            repositories[url] = cache
        checkout = repositories[url]
        _ensure_commit(checkout, commit)
        entry = _git(checkout, 'ls-tree', '-z', commit, '--', resource['path']).stdout
        records = entry.rstrip(b'\0').split(b'\0') if entry else []
        if len(records) != 1 or b'\t' not in records[0]:
            raise ProtocolError('RESOURCE_NOT_FILE', 'Resource is not a regular Git file at the specified commit.')
        description, name = records[0].split(b'\t', 1)
        if description.split()[0] not in {b'100644', b'100755'} or name.decode('utf-8') != resource['path']:
            raise ProtocolError('RESOURCE_NOT_FILE', 'Resource must be a regular file, not a symlink or submodule.')
        content = _git(checkout, 'show', f'{commit}:{resource["path"]}').stdout
        if hashlib.sha256(content).hexdigest() != resource['sha256'].casefold():
            raise ProtocolError('RESOURCE_HASH_MISMATCH', f'Resource hash mismatch: {resource["destination"]}')
        destination = checked_path(workspace, resource['destination'])
        if destination.exists():
            raise ProtocolError('RESOURCE_COLLISION', 'Resources cannot overwrite an existing checkout file.')
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as stream:
            stream.write(content)
        destination.chmod(0o444)
    return workspace


def export_patch(task: dict, workspace: Path, destination: Path) -> list[str]:
    _no_symlinks(workspace)
    for resource in task['resources']:
        path = checked_path(workspace, resource['destination'])
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != resource['sha256'].casefold():
            raise ProtocolError('RESOURCE_MODIFIED', f'Read-only resource was changed: {resource["destination"]}')
    resources = {resource['destination'] for resource in task['resources']}
    base = _commit(task['source']['base_commit'])
    modified = _git(workspace, 'diff', '--name-only', '-z', base, '--').stdout
    untracked = _git(workspace, 'ls-files', '--others', '--exclude-standard', '-z').stdout
    try:
        changes = sorted(set(name.decode('utf-8') for name in (modified + untracked).split(b'\0') if name) - resources)
    except UnicodeDecodeError as exc:
        raise ProtocolError('UNSAFE_PATH', 'Changed filenames must be valid UTF-8.') from exc
    allowed = [path.rstrip('/') for path in task['source']['write_paths']]
    for name in changes:
        checked_path(workspace, name)
        if not any(name == path or name.startswith(path + '/') for path in allowed):
            raise ProtocolError('WRITE_SCOPE_VIOLATION', f'Changed file is outside write_paths: {name}')
    new_paths = [name.decode('utf-8') for name in untracked.split(b'\0') if name and name.decode('utf-8') in changes]
    if new_paths:
        _git(workspace, 'add', '--intent-to-add', '--', *new_paths)
    patch = _git(workspace, 'diff', '--binary', '--no-ext-diff', '--no-textconv', base, '--', *changes).stdout if changes else b''
    regular_path(destination)
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination.write_bytes(patch)
    return changes
