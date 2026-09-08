#!/usr/bin/env python3
"""File-backed fake gh for subprocess-only managed-origin integration tests."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, os.environ['CLI_TEST_PROJECT'])
from tests.fixtures.cli_gateway import Gateway

path = Path(os.environ['CLI_TEST_GATEWAY'])
state = json.loads(path.read_text())
gateway = Gateway()
gateway.__dict__.update(state)
try:
    output = gateway.run(['gh', *sys.argv[1:]], data=sys.stdin.read() if '--input' in sys.argv else None)
    path.write_text(json.dumps(gateway.__dict__))
    print(output)
except Exception as exc:
    print(type(exc).__name__ + ': ' + str(exc), file=sys.stderr)
    sys.exit(1)
