"""Build a deterministic public runtime and fixed OS starter templates."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import stat
import tempfile
import zipfile

from .local import atomic_json, regular_path
from .protocol import ProtocolError

STARTERS = ('bootstrap.py', 'Start-Taskboard.cmd', 'Start-Taskboard.command', 'Start-Taskboard.sh')


def build_download_assets(output: Path, project: Path) -> dict:
    output, project = Path(output).absolute(), Path(project).absolute()
    destination = output / 'downloads'
    regular_path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    files = list((project / 'taskboard').glob('*.py'))
    files.extend(project / name for name in ('bin/taskboard', 'README.md', 'README.en.md'))
    for directory in ('resources', 'docs/examples', 'integrations'):
        root = project / directory
        regular_path(root)
        if root.is_dir():
            files.extend(path for path in root.rglob('*') if path.is_file() or path.is_symlink())
    selected = {}
    for path in files:
        regular_path(path)
        name = path.relative_to(project).as_posix()
        if any(part.startswith('.') or part == '__pycache__' for part in path.relative_to(project).parts) or path.suffix in {'.pyc', '.pyo'}:
            continue
        if not path.is_file():
            raise ProtocolError('RUNTIME_ASSET_MISSING', f'Runtime source asset is missing: {name}')
        selected[name] = path
    if not {'taskboard/wizard.py', 'taskboard/__init__.py', 'bin/taskboard', 'docs/examples/task-v1.json', 'resources/publisher-instructions.md'} <= selected.keys():
        raise ProtocolError('RUNTIME_ASSET_MISSING', 'Portable runtime source files are incomplete.')
    descriptor, temporary_name = tempfile.mkstemp(prefix='.runtime-', dir=destination)
    os.close(descriptor)
    temporary = Path(temporary_name)
    runtime = destination / 'taskboard-runtime.zip'
    regular_path(runtime)
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, source in sorted(selected.items()):
                info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | (0o755 if name == 'bin/taskboard' else 0o644)) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, source.read_bytes())
        os.replace(temporary, runtime)
    finally:
        temporary.unlink(missing_ok=True)
    for name in STARTERS:
        source, target = project / 'launchers' / name, destination / name
        regular_path(source)
        regular_path(target)
        shutil.copyfile(source, target)
        target.chmod(0o755 if name.endswith(('.sh', '.command')) else 0o644)
    manifest = {'schema_version': 1, 'file': runtime.name, 'sha256': hashlib.sha256(runtime.read_bytes()).hexdigest(), 'size': runtime.stat().st_size}
    atomic_json(destination / 'runtime.json', manifest)
    return manifest
