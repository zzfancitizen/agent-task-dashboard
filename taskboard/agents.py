"""Provider CLIs at an argv/stdin boundary; no model SDK or polling loop."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from queue import Empty, Full, Queue
import tempfile
import subprocess
from threading import Event, Thread
import time

from .local import regular_path, session_id
from .protocol import ProtocolError
from .platforms import ProcessTree, executable_argv, process_options


@dataclass(frozen=True)
class AgentOutcome:
    session_id: str
    summary: str
    usage: dict | None


def provider_argv(agent: str, session: str | None = None, *, managed: dict | None = None) -> list[str]:
    if session is not None:
        session = session_id(session)
    if agent == 'codex':
        argv = ['codex', 'exec', '--sandbox', 'workspace-write', '-c', 'approval_policy="never"']
        if managed:
            argv.extend(['--add-dir', str(managed['home']), '-c', 'sandbox_workspace_write.network_access=true'])
        if session:
            return [*argv, 'resume', '--json', session, '-']
        return [*argv, '--json', '-']
    if agent == 'claude':
        # The executor uses file tools; the host runs acceptance commands.
        # Managed origins add specific Bash permission rules below.
        argv = ['claude', '-p', '--output-format', 'stream-json', '--verbose', '--restricted', '--permission-mode', 'acceptEdits', '--tools', 'Read,Glob,Grep,Edit,Write', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}']
        if managed:
            argv.extend(['--add-dir', str(managed['home'])])
            if managed.get('schema_dir'):
                argv.extend(['--add-dir', str(managed['schema_dir'])])
            argv[argv.index('--tools') + 1] += ',Bash'
            rules = [f"Bash({managed['command']} *)"]
            for command in ('git status', 'git rev-parse', 'git ls-files', 'git show', 'git diff', 'git log', 'git cat-file', 'shasum', 'sha256sum'):
                rules.extend([f'Bash({command})', f'Bash({command} *)'])
            # These suppress prompts for the named commands; they do not
            # replace Claude's local/managed permissions with a deny-all list.
            argv.extend(['--allowedTools', *rules, '--permission-prompts', 'none'])
        if session:
            argv.extend(['--resume', session])
        return argv
    raise ProtocolError('INVALID_AGENT', 'Agent must be codex or claude.')


def execute(argv: list[str], cwd: Path, output: Path, errors: Path, timeout: float, prompt: str | None = None, *, on_event=None, env: dict | None = None) -> int:
    if timeout <= 0:
        raise ProtocolError('AGENT_TIMEOUT', 'Execution deadline expired.')
    for path in (cwd, output, errors):
        regular_path(path)
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # A private file as stdin avoids a prompt-size pipe deadlock while stdout
    # is streamed. It is unlinked automatically, and never becomes an artifact.
    with tempfile.TemporaryFile() as source, output.open('wb') as stdout, errors.open('wb') as stderr:
        output.chmod(0o600)
        errors.chmod(0o600)
        if prompt is not None:
            source.write(prompt.encode('utf-8'))
            source.seek(0)
        try:
            process = subprocess.Popen(executable_argv(argv), cwd=cwd, stdin=source, stdout=subprocess.PIPE, stderr=stderr, env=env, **process_options())
        except OSError as exc:
            raise ProtocolError('AGENT_UNAVAILABLE', f'Cannot launch {argv[0]}. Install and authenticate the CLI locally.') from exc
        try:
            tree = ProcessTree(process)
        except BaseException:
            process.stdout.close()
            raise
        deadline = time.monotonic() + timeout
        pending = b''
        chunks = Queue(maxsize=16)
        stopping = Event()
        def read_output():
            try:
                while not stopping.is_set():
                    chunk = process.stdout.read1(65536)
                    while not stopping.is_set():
                        try:
                            chunks.put(chunk, timeout=0.1)
                            break
                        except Full:
                            continue
                    if not chunk:
                        break
            except OSError as exc:
                if not stopping.is_set():
                    chunks.put(exc)
        reader = Thread(target=read_output, name='taskboard-provider-output', daemon=True)
        reader.start()
        try:
            while True:
                if process.poll() is not None:
                    tree.stop()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProtocolError('AGENT_TIMEOUT', 'Execution timed out; its process tree was stopped.')
                try:
                    chunk = chunks.get(timeout=min(0.2, remaining))
                except Empty:
                    continue
                if isinstance(chunk, OSError):
                    raise chunk
                if not chunk:
                    break
                stdout.write(chunk)
                stdout.flush()
                if on_event is None:
                    continue
                pending += chunk
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    try:
                        event = json.loads(line)
                    except (ValueError, UnicodeError):
                        continue
                    if isinstance(event, dict):
                        on_event(event)
                if len(pending) > 1024 * 1024:
                    raise ProtocolError('AGENT_OUTPUT_INVALID', 'Provider emitted an oversized unterminated event.')
            process.wait(timeout=max(0.01, deadline - time.monotonic()))
            return process.returncode
        except subprocess.TimeoutExpired as exc:
            raise ProtocolError('AGENT_TIMEOUT', 'Execution timed out; its process group was stopped.') from exc
        finally:
            stopping.set()
            tree.stop()
            reader.join(timeout=3)
            process.stdout.close()


def run_agent(agent: str, prompt: str, workspace: Path, output_dir: Path, timeout: float, *, session: str | None = None, on_event=None, env: dict | None = None, managed: dict | None = None) -> AgentOutcome:
    regular_path(output_dir)
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    output, errors = output_dir / 'events.jsonl', output_dir / 'stderr.log'
    if env is None:
        env = {key: value for key, value in os.environ.items() if key != 'TASKBOARD_ORIGIN_RUN_ID'}
    status = execute(provider_argv(agent, session, managed=managed), workspace, output, errors, timeout, prompt, on_event=on_event, env=env)
    if status:
        raise ProtocolError('AGENT_FAILED', f'{agent} exited with status {status}; inspect local logs in {output_dir}.')
    observed = None
    completed = False
    failed = False
    summary = ''
    usage = None
    with output.open(encoding='utf-8', errors='replace') as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get('type')
            candidate = event.get('thread_id') if kind == 'thread.started' else event.get('session_id') if agent == 'claude' else None
            if candidate:
                candidate = session_id(candidate)
                if observed and observed != candidate:
                    raise ProtocolError('SESSION_MISMATCH', 'The provider reported more than one session ID.')
                observed = candidate
            if kind == 'item.completed' and isinstance(event.get('item'), dict) and event['item'].get('type') == 'agent_message':
                summary = event['item'].get('text', '')
            if kind == 'turn.completed':
                completed = True
                usage = event.get('usage')
            if kind in {'turn.failed', 'error'}:
                failed = True
            if kind == 'result':
                completed = event.get('is_error') is False and event.get('subtype') == 'success'
                failed = failed or not completed
                summary = event.get('result', '')
                usage = event.get('usage')
    if not completed or failed or not observed or not isinstance(summary, str) or not summary.strip():
        raise ProtocolError('AGENT_INCOMPLETE', f'{agent} did not report a successful terminal result and session ID; inspect {output_dir}.')
    if session is not None and observed != session_id(session):
        raise ProtocolError('SESSION_MISMATCH', 'The resumed provider did not continue the exact bound session. Delivery is uncertain.')
    return AgentOutcome(observed, summary.strip(), usage if isinstance(usage, dict) else None)


def run_checks(commands: list[list[str]], workspace: Path, output_dir: Path, timeout: float) -> list[dict]:
    regular_path(output_dir)
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    result = []
    for index, argv in enumerate(commands, start=1):
        output = output_dir / f'check-{index}.out'
        errors = output_dir / f'check-{index}.err'
        try:
            status = execute(argv, workspace, output, errors, deadline - time.monotonic())
        except ProtocolError as exc:
            if exc.code not in {'AGENT_TIMEOUT', 'AGENT_UNAVAILABLE'}:
                raise
            status = 124 if exc.code == 'AGENT_TIMEOUT' else 127
        result.append({'argv': argv, 'exit_code': status, 'stdout': output.read_text(errors='replace')[-32768:] if output.exists() else '', 'stderr': errors.read_text(errors='replace')[-32768:] if errors.exists() else ''})
        if status:
            break
    return result
