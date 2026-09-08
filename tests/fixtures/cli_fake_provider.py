#!/usr/bin/env python3
"""External process fixture; deliberately makes no provider/model/network calls."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

prompt = sys.stdin.read()
if os.environ.get('CLI_TEST_CALLS'):
    with Path(os.environ['CLI_TEST_CALLS']).open('a') as stream:
        stream.write('call\n')
mode = os.environ.get('CLI_TEST_MODE', 'success')
if mode == 'timeout':
    subprocess.Popen([sys.executable, '-c', 'import time,pathlib; time.sleep(0.7); pathlib.Path("escaped-child").write_text("bad")'])
    time.sleep(10)
if mode == 'silent':
    sys.exit(0)
report = os.environ.get('CLI_TEST_REPORT', 'Implemented task.')
session = os.environ.get('CLI_TEST_SESSION', 'cf20e605-88b8-443f-87a1-50c027e984e1')
if os.environ.get('CLI_TEST_OUTPUTS'):
    for filename, content in json.loads(os.environ['CLI_TEST_OUTPUTS']).items():
        target = Path(filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
if os.environ.get('CLI_TEST_EDIT'):
    Path('src/main.py').write_text('value = 2\n')
if os.environ.get('CLI_TEST_ARGV'):
    Path(os.environ['CLI_TEST_ARGV']).write_text(json.dumps({'argv': sys.argv[1:], 'prompt': prompt, 'cwd': os.getcwd()}))
if mode in {'watch', 'managed'}:
    print(json.dumps({'type': 'thread.started', 'thread_id': session}), flush=True)
    if mode == 'watch':
        deadline = time.monotonic() + 3
        while not Path(os.environ['CLI_TEST_MARKER']).exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not Path(os.environ['CLI_TEST_MARKER']).exists():
            sys.exit(9)
    else:
        command = [os.environ['TASKBOARD_COMMAND'], '--home', os.environ['TASKBOARD_HOME'], 'publish', os.environ['CLI_TEST_TASK']]
        published = subprocess.run(command, capture_output=True, text=True)
        if published.returncode:
            print(published.stderr, file=sys.stderr)
            sys.exit(8)
    mode = 'success'
if Path(sys.argv[0]).name == 'claude':
    print(json.dumps({'type': 'system', 'subtype': 'init', 'session_id': session}))
    print(json.dumps({'type': 'result', 'subtype': 'success' if mode == 'success' else 'error_during_execution', 'is_error': mode != 'success', 'session_id': session, 'result': report, 'usage': {'input_tokens': 4, 'output_tokens': 3}}))
else:
    print(json.dumps({'type': 'thread.started', 'thread_id': session}))
    print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': report}}))
    print(json.dumps({'type': 'turn.completed' if mode == 'success' else 'turn.failed', 'usage': {'input_tokens': 4, 'output_tokens': 3}}))
if mode == 'nonzero':
    sys.exit(7)
