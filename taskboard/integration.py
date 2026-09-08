"""Project-local publisher skills and exact-session, offline lifecycle hooks."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import uuid

from .local import LocalStore, atomic_json, read_json, regular_path, session_id
from .protocol import ProtocolError


RULE_START = '<!-- taskboard-publish:start -->'
RULE_END = '<!-- taskboard-publish:end -->'


def _provider(provider: str) -> None:
    if provider not in {'codex', 'claude'}:
        raise ProtocolError('INVALID_AGENT', 'Provider must be codex or claude.')


def _project(project: Path) -> Path:
    path = Path(project).expanduser().absolute()
    regular_path(path)
    if not path.is_dir():
        raise ProtocolError('INVALID_WORKSPACE', 'Choose the existing source project directory.')
    return path.resolve()


def _key(provider: str, project: Path) -> str:
    return hashlib.sha256(f'{provider}\0{project}'.encode()).hexdigest()


def _write(path: Path, content: bytes) -> None:
    regular_path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _runtime(store: LocalStore) -> Path:
    """Install a content-addressed private runtime independent of the download."""
    source = Path(__file__).resolve().parent.parent
    files = {}
    for pattern in ('taskboard/*.py', 'bin/taskboard', 'resources/*', 'docs/examples/*', 'integrations/taskboard-publish/SKILL.md', 'integrations/taskboard-publish/scripts/*.py'):
        for path in sorted(source.glob(pattern)):
            if path.is_file():
                regular_path(path)
                files[path.relative_to(source).as_posix()] = path.read_bytes()
    if 'integrations/taskboard-publish/SKILL.md' not in files:
        raise ProtocolError('INTEGRATION_MISSING', 'The Taskboard distribution is missing its publisher skill.')
    digest = hashlib.sha256()
    for name, content in sorted(files.items()):
        digest.update(name.encode() + b'\0' + hashlib.sha256(content).digest())
    target = store.home / 'runtimes' / digest.hexdigest()
    with store.exclusive('runtime:' + digest.hexdigest()):
        for name, content in files.items():
            path = target / name
            regular_path(path)
            if path.exists() and path.read_bytes() != content:
                raise ProtocolError('RUNTIME_CHANGED', 'Installed runtime content changed; choose a clean local home.')
            if not path.exists():
                _write(path, content)
        atomic_json(target / 'runtime.json', {'schema_version': 1, 'sha256': digest.hexdigest()})
    return target


def _rule(text: str, skill: str) -> str:
    block = f'{RULE_START}\nWhen an independent task can be delegated, read `{skill}` and prepare its full prompt, committed resources, scope, and acceptance criteria. Show the concrete proposal and ask the user whether to publish. Only an explicit affirmative reply authorizes the proposal publish helper. Installing this integration, a hook event, or general permission to work does not authorize publishing. Keep session callbacks and transcripts local.\n{RULE_END}'
    if RULE_START in text or RULE_END in text:
        if text.count(RULE_START) != 1 or text.count(RULE_END) != 1 or text.index(RULE_START) > text.index(RULE_END):
            raise ProtocolError('INTEGRATION_CONFLICT', 'Existing Taskboard rule markers are malformed; repair them before reinstalling.')
        start, end = text.index(RULE_START), text.index(RULE_END) + len(RULE_END)
        return text[:start] + block + text[end:]
    return text + ('\n' if text.endswith('\n') or not text else '\n\n') + block + '\n'


def install_integration(provider: str, project: Path, store: LocalStore) -> dict:
    """Merge skill/rule/hooks into one project; never change global agent setup."""
    _provider(provider)
    project = _project(project)
    store.config()
    identifier = _key(provider, project)
    prefix = '.agents' if provider == 'codex' else '.claude'
    relative_skill = f'{prefix}/skills/taskboard-publish/SKILL.md'
    skill = project / relative_skill
    config_path = project / ('.codex/hooks.json' if provider == 'codex' else '.claude/settings.json')
    rule_path = project / ('AGENTS.md' if provider == 'codex' else 'CLAUDE.md')
    with store.exclusive('integration:' + identifier):
        settings = read_json(config_path, {})
        hooks = settings.setdefault('hooks', {})
        if not isinstance(hooks, dict) or any(not isinstance(hooks.get(event, []), list) for event in ('SessionStart', 'UserPromptSubmit')):
            raise ProtocolError('INTEGRATION_CONFLICT', 'Existing hooks must be an event object containing handler lists.')
        regular_path(rule_path)
        rule = _rule(rule_path.read_text(encoding='utf-8') if rule_path.exists() else '', relative_skill)
        runtime = _runtime(store)
        script = skill.parent / 'scripts/taskboard-publish.py'
        argv = [sys.executable, '-I', '-X', 'utf8', str(script), 'hook']
        command, windows_command = shlex.join(argv), subprocess.list2cmdline(argv)
        prior = store.data().get('integrations', {}).get(identifier, {})
        old_commands = set(prior.get('commands', []))
        for event in ('SessionStart', 'UserPromptSubmit'):
            groups = []
            for original in hooks.get(event, []):
                if not isinstance(original, dict) or not isinstance(original.get('hooks'), list):
                    raise ProtocolError('INTEGRATION_CONFLICT', 'Existing hook groups must contain handler lists.')
                group = copy.deepcopy(original)
                group['hooks'] = [handler for handler in group['hooks'] if not (isinstance(handler, dict) and handler.get('command') in old_commands)]
                if group['hooks']:
                    groups.append(group)
            handler = {'type': 'command', 'command': command, 'timeout': 10}
            if provider == 'codex':
                handler['commandWindows'] = windows_command
            groups.append({'hooks': [handler]})
            hooks[event] = groups
        metadata = {'schema_version': 1, 'provider': provider, 'project': str(project), 'home': str(store.home), 'runtime': str(runtime)}
        writes = {
            skill: (runtime / 'integrations/taskboard-publish/SKILL.md').read_bytes(),
            script: (runtime / 'integrations/taskboard-publish/scripts/taskboard-publish.py').read_bytes(),
            skill.parent / 'runtime.json': (json.dumps(metadata, sort_keys=True) + '\n').encode(),
            rule_path: rule.encode(),
            config_path: (json.dumps(settings, ensure_ascii=False, indent=2) + '\n').encode(),
        }
        tracked = {}
        for path, content in writes.items():
            regular_path(path)
            name = path.relative_to(project).as_posix()
            baseline = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
            previous = prior.get('files', {}).get(name)
            if previous and baseline == previous['sha256']:
                baseline = previous['baseline_sha256']
            if name.endswith('/SKILL.md') or name.endswith('/scripts/taskboard-publish.py') or name.endswith('/runtime.json'):
                if path.exists() and not previous and path.read_bytes() != content:
                    raise ProtocolError('INTEGRATION_CONFLICT', 'A different publisher skill already exists in this project; preserve it before installing.')
            tracked[name] = {'baseline_sha256': baseline, 'sha256': hashlib.sha256(content).hexdigest()}
        for path, content in writes.items():
            _write(path, content)
        record = {'provider': provider, 'project': str(project), 'skill': str(skill), 'runtime': str(runtime), 'commands': [command, windows_command], 'files': tracked}
        store.update(lambda data: data.setdefault('integrations', {}).update({identifier: record}))
        return {'provider': provider, 'project': str(project), 'skill': str(skill), 'runtime': str(runtime), 'hooks': str(config_path), 'rule': str(rule_path), 'restart_required': True, 'next_step': 'Review and trust the new hooks in the provider, then resume the original session so it supplies its exact callback.'}


def _capture(provider: str, project: Path, store: LocalStore, event: dict) -> tuple[str, dict]:
    if not isinstance(event, dict) or event.get('agent_id') or event.get('hook_event_name') not in {'SessionStart', 'UserPromptSubmit'}:
        raise ProtocolError('INVALID_HOOK_EVENT', 'Only original-session SessionStart/UserPromptSubmit events are supported.')
    session = session_id(event.get('session_id'))
    cwd = event.get('cwd')
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        raise ProtocolError('INVALID_HOOK_EVENT', 'The provider must supply the exact absolute session cwd.')
    workspace = _project(Path(cwd))
    if not workspace.is_relative_to(project):
        raise ProtocolError('HOOK_PROJECT_MISMATCH', 'The provider event is outside this installed project.')
    # Exact tuples separate concurrent sessions without a latest-session lookup.
    callback_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'taskboard:{provider}:{session}:{project}:{workspace}'))
    callback = {'provider': provider, 'session_id': session, 'project': str(project), 'workspace': str(workspace)}
    def save(data):
        callbacks = data.setdefault('callbacks', {})
        existing = callbacks.get(callback_id)
        if existing and any(existing.get(key) != value for key, value in callback.items()):
            raise ProtocolError('CALLBACK_CONFLICT', 'This callback refers to another original session.')
        callbacks[callback_id] = {**(existing or {}), **callback}
        return copy.deepcopy(callbacks[callback_id])
    callback = store.update(save)
    return callback_id, callback


def handle_hook(provider: str, project: Path, store: LocalStore, event: dict) -> dict:
    """Capture exact native fields and report only locally pending references."""
    _provider(provider)
    project = _project(project)
    local_state = store.home / 'local.json'
    regular_path(local_state)
    if local_state.exists() and local_state.stat().st_size > 4 * 1024 * 1024:
        raise ProtocolError('HOOK_STATE_TOO_LARGE', 'Local task history exceeds the fast hook limit. Use the exact installed Taskboard CLI for sync and result review.')
    installed = store.data().get('integrations', {}).get(_key(provider, project))
    if not installed:
        raise ProtocolError('INTEGRATION_MISSING', 'Install the publisher integration for this project first.')
    callback_id, callback = _capture(provider, project, store, event)
    event_name = event['hook_event_name']
    context = []
    if event_name == 'SessionStart' or not callback.get('context_shown'):
        script = Path(installed['skill']).parent / 'scripts/taskboard-publish.py'
        helper = json.dumps([sys.executable, '-I', '-X', 'utf8', str(script)], ensure_ascii=False)
        context.append(f'Taskboard publisher integration is available. Read {installed["skill"]} when independent work can be delegated. Exact local callback: {callback_id}. Helper argv: {helper}. Append context --callback {callback_id} to obtain the local goal directory, proposal inputs, and exact Taskboard CLI argv for fetching and reviewing results. Prepare the complete proposal, ask the user whether to publish, and wait for an explicit affirmative reply before using publish --approved. A hook event is not publication approval.')
    def pending(data):
        current = data['callbacks'][callback_id]
        current['context_shown'] = True
        seen = current.setdefault('notified_results', [])
        references = []
        for result_id, item in data['inbox'].items():
            if len(references) >= 8:
                break
            binding = data['bindings'].get(str(item.get('issue')), {})
            if item.get('delivery') != 'pending' or result_id in seen or any(binding.get(key) != value for key, value in {'agent': provider, 'session_id': callback['session_id'], 'workspace': callback['workspace']}.items()):
                continue
            session_id(result_id)
            if type(item.get('issue')) is not int or item['issue'] < 1:
                continue
            references.append(f'Issue #{item["issue"]}, result {result_id}')
            seen.append(result_id)
        return references
    references = store.update(pending)
    if references:
        context.append('Locally synced results await review in this original session: ' + '; '.join(references) + '. Read their stored manifests and verified artifacts before accepting or integrating. Do not launch a concurrent writer into this session. These references do not mean the result has passed review.')
    return {'hookSpecificOutput': {'hookEventName': event_name, 'additionalContext': '\n'.join(context)}} if context else {}


def callback_context(store: LocalStore, callback_id: str, *, provider: str, project: Path) -> dict:
    """Resolve only the callback explicitly supplied by the native hook."""
    callback_id = session_id(callback_id)
    project = _project(project)
    callback = store.data().get('callbacks', {}).get(callback_id)
    if not callback or callback.get('provider') != provider or callback.get('project') != str(project):
        raise ProtocolError('CALLBACK_NOT_FOUND', 'Use the exact callback supplied to this session by its native hook; do not choose another session.')
    session_id(callback.get('session_id'))
    installed = store.data().get('integrations', {}).get(_key(provider, project))
    if not installed:
        raise ProtocolError('INTEGRATION_MISSING', 'Install the publisher integration first.')
    goal_directory = store.home / 'proposal-goals' / callback_id
    regular_path(goal_directory)
    goal_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    exclusions = set()
    for record in store.data().get('integrations', {}).values():
        if record.get('project') == str(project):
            exclusions.update(record.get('files', {}))
    helper = [sys.executable, '-I', '-X', 'utf8', str(Path(installed['skill']).parent / 'scripts/taskboard-publish.py')]
    taskboard = [sys.executable, '-I', '-X', 'utf8', str(Path(installed['runtime']) / 'bin/taskboard'), '--home', str(store.home)]
    return {**callback, 'callback': callback_id, 'goal_directory': str(goal_directory), 'excluded_paths': sorted(exclusions), 'python_executable': sys.executable, 'helper_argv': helper, 'taskboard_argv': taskboard, 'board': {'repository': store.config()['repo'], 'hostname': store.config()['hostname']}}


def publisher_main(argv: list[str] | None = None, *, metadata: dict) -> int:
    """Installed skill helper; arguments remain argv/JSON, never shell text."""
    parser = argparse.ArgumentParser(description='Prepare a local Taskboard proposal and publish only after an explicit user decision.')
    commands = parser.add_subparsers(dest='action', required=True)
    commands.add_parser('hook')
    context = commands.add_parser('context')
    context.add_argument('--callback', required=True)
    prepare = commands.add_parser('prepare')
    prepare.add_argument('--callback', required=True)
    prepare.add_argument('--goal-file', required=True)
    prepare.add_argument('--title', required=True)
    prepare.add_argument('--resource', action='append', default=[])
    prepare.add_argument('--write-path', action='append', default=[])
    prepare.add_argument('--command-json', action='append', default=[])
    prepare.add_argument('--size', choices=['S', 'M', 'L'], default='M')
    prepare.add_argument('--category', choices=['code', 'docs', 'research'], default='code')
    publish = commands.add_parser('publish')
    publish.add_argument('proposal_id')
    publish.add_argument('--callback', required=True)
    publish.add_argument('--approved', action='store_true')
    args = parser.parse_args(argv)
    try:
        if metadata.get('schema_version') != 1:
            raise ProtocolError('INTEGRATION_MISSING', 'The installed runtime locator is invalid; reinstall it.')
        store = LocalStore(metadata['home'])
        provider, project = metadata['provider'], Path(metadata['project'])
        if args.action == 'hook':
            raw = sys.stdin.buffer.read(64 * 1024 + 1)
            if len(raw) > 64 * 1024:
                raise ProtocolError('INVALID_HOOK_EVENT', 'The hook event is too large.')
            result = handle_hook(provider, project, store, json.loads(raw))
        else:
            callback = callback_context(store, args.callback, provider=provider, project=project)
            if args.action == 'context':
                result = callback
            elif args.action == 'prepare':
                from .publishing import prepare_proposal
                result = prepare_proposal(store, workspace=Path(callback['workspace']), goal_file=Path(args.goal_file), title=args.title, provider=provider, session=callback['session_id'], resource_paths=args.resource, write_paths=args.write_path, commands=[json.loads(value) for value in args.command_json], size=args.size, category=args.category)
            else:
                from .github import GitHub
                from .publishing import publish_proposal
                proposal = store.data().get('proposals', {}).get(args.proposal_id)
                if proposal and (proposal['provider'], proposal['session'], proposal['workspace']) != (provider, callback['session_id'], callback['workspace']):
                    raise ProtocolError('CALLBACK_CONFLICT', 'This proposal belongs to another source session.')
                config = store.config()
                result = publish_proposal(store, GitHub(config['repo'], config['hostname']), args.proposal_id, approved=args.approved)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ProtocolError, OSError, ValueError, KeyError) as exc:
        code = getattr(exc, 'code', 'INTEGRATION_INPUT_INVALID')
        message = getattr(exc, 'message', 'Cannot read the local integration input; verify its paths and JSON.')
        if args.action == 'hook':
            print(json.dumps({'systemMessage': f'Taskboard publisher hook unavailable ({code}): {message}'}))
            return 0
        print(f'{code}: {message}', file=sys.stderr)
        return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Taskboard native session hook')
    parser.add_argument('--provider', required=True, choices=['codex', 'claude'])
    parser.add_argument('--project', required=True)
    parser.add_argument('--home', required=True)
    args = parser.parse_args(argv)
    return publisher_main(['hook'], metadata={'schema_version': 1, 'provider': args.provider, 'project': args.project, 'home': args.home})


if __name__ == '__main__':
    raise SystemExit(main())
