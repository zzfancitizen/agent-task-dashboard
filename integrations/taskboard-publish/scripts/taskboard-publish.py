#!/usr/bin/env python3
"""Load the stable runtime selected by the project integration installer."""
import json
from pathlib import Path
import sys


def main():
    locator = Path(__file__).resolve().parent.parent / 'runtime.json'
    try:
        metadata = json.loads(locator.read_text(encoding='utf-8'))
        runtime = Path(metadata['runtime'])
        if not runtime.is_absolute() or not (runtime / 'taskboard/integration.py').is_file():
            raise ValueError('invalid runtime')
    except (OSError, ValueError, KeyError):
        print('Install this skill with Taskboard install-integration before use. Its project runtime locator is missing or invalid.', file=sys.stderr)
        return 2
    sys.path.insert(0, str(runtime))
    from taskboard.integration import publisher_main
    return publisher_main(metadata=metadata)


if __name__ == '__main__':
    raise SystemExit(main())
