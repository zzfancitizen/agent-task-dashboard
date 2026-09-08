#!/usr/bin/env python3
"""Standalone standard-library bootstrap. No runtime import before verification."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile


def regular_path(path):
    for part in (path, *path.parents):
        if part.is_symlink():
            if str(part) in {'/var', '/tmp', '/etc'} and part.resolve() == Path('/private') / part.name:
                continue
            raise ValueError('路径含符号链接，请将下载包解压到普通文件夹。')


def read_request(path):
    regular_path(path)
    if path.stat().st_size > 4096:
        raise ValueError('请求文件过大。请从任务网页重新下载。')
    request = json.loads(path.read_text(encoding='utf-8'))
    base = {'schema_version', 'hostname', 'repository', 'runtime_sha256', 'action'}
    locator = {'issue', 'revision', 'task_digest'}
    if not isinstance(request, dict) or set(request) not in (base, base | locator) or type(request.get('schema_version')) is not int or request['schema_version'] != 1:
        raise ValueError('请求文件格式不受支持。')
    host, repo = request['hostname'], request['repository']
    if not isinstance(host, str) or len(host) > 253 or not all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', part) for part in host.split('.')):
        raise ValueError('GitHub 域名无效。')
    if not isinstance(repo, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*', repo):
        raise ValueError('任务仓库无效。')
    if request['action'] not in ('run', 'install-publisher') or request['action'] == 'run' and not locator <= request.keys():
        raise ValueError('请求动作或任务定位无效。')
    for key in ('runtime_sha256', *(['task_digest'] if 'task_digest' in request else [])):
        if not isinstance(request[key], str) or not re.fullmatch(r'[0-9a-f]{64}', request[key]):
            raise ValueError('SHA-256 校验值无效。')
    for key in ('issue', 'revision'):
        if key in request and (type(request[key]) is not int or not 1 <= request[key] <= 9999999999):
            raise ValueError('任务编号或版本无效。')
    return request


def cache_home():
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData' / 'Local'))) / 'Taskboard' / 'runtimes'
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'Taskboard' / 'runtimes'
    return Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local' / 'share'))) / 'taskboard' / 'runtimes'


def archive_files(archive):
    entries, folded, total = {}, set(), 0
    if len(archive.infolist()) > 2000:
        raise ValueError('运行时文件数量超过限制。')
    reserved = {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)), *(f'lpt{i}' for i in range(1, 10))}
    for info in archive.infolist():
        name = info.filename
        parts = name.split('/')
        if not name or '\\' in name or any(ord(char) < 32 or ord(char) == 127 for char in name) or any(not part or part in {'.', '..'} or ':' in part or part.endswith((' ', '.')) or part.split('.')[0].casefold() in reserved for part in parts):
            raise ValueError('运行时包含不安全路径。')
        mode = info.external_attr >> 16
        if info.is_dir() or stat.S_IFMT(mode) not in {0, stat.S_IFREG} or info.flag_bits & 1:
            raise ValueError('运行时只能包含未加密普通文件。')
        if name.casefold() in folded or any('/'.join(parts[:index]).casefold() in folded for index in range(1, len(parts))) or any(prior.startswith(name.casefold() + '/') for prior in folded):
            raise ValueError('运行时存在重复或冲突路径。')
        if info.file_size > 5 * 1024 * 1024:
            raise ValueError('运行时单文件过大。')
        total += info.file_size
        if total > 20 * 1024 * 1024:
            raise ValueError('运行时解压大小超过限制。')
        folded.add(name.casefold())
        entries[name] = info
    if not {'taskboard/__init__.py', 'taskboard/wizard.py'} <= entries.keys():
        raise ValueError('运行时缺少启动模块。')
    return entries


def verify_cache(directory, archive, entries):
    regular_path(directory)
    actual = set()
    for file in directory.rglob('*'):
        regular_path(file)
        if file.is_file():
            actual.add(file.relative_to(directory).as_posix())
    if actual != set(entries):
        raise ValueError('运行时缓存文件清单不一致。请删除此版本缓存后重新启动：' + str(directory))
    for name, info in entries.items():
        target = directory.joinpath(*name.split('/'))
        if target.stat().st_size != info.file_size or hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(archive.read(info)).digest():
            raise ValueError('运行时缓存已改变。请删除此版本缓存后重新启动：' + str(directory))


def extract_runtime(runtime, digest, cache):
    regular_path(runtime)
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest) or runtime.stat().st_size > 20 * 1024 * 1024 or hashlib.sha256(runtime.read_bytes()).hexdigest() != digest:
        raise ValueError('运行时 SHA-256 校验失败。请从可信任务网页重新下载。')
    with zipfile.ZipFile(runtime) as archive:
        entries = archive_files(archive)
        regular_path(cache)
        destination = cache / digest
        regular_path(destination)
        if destination.exists():
            verify_cache(destination, archive, entries)
            return destination
        cache.mkdir(parents=True, mode=0o700, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix='.extract-', dir=cache))
        try:
            for name, info in entries.items():
                target = temporary.joinpath(*name.split('/'))
                target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                data = archive.read(info)  # ZipFile also verifies CRC and length.
                if len(data) != info.file_size:
                    raise ValueError('运行时解压长度不一致。')
                with target.open('xb') as stream:
                    stream.write(data)
                target.chmod(0o700 if info.external_attr >> 16 & 0o111 else 0o600)
            try:
                temporary.rename(destination)
            except OSError:
                if not destination.is_dir():
                    raise
                verify_cache(destination, archive, entries)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        return destination


def run(request_path):
    request_path = Path(request_path).absolute()
    request = read_request(request_path)
    runtime = extract_runtime(request_path.with_name('runtime.zip'), request['runtime_sha256'], cache_home())
    env = {**os.environ, 'PYTHONPATH': str(runtime), 'PYTHONDONTWRITEBYTECODE': '1'}
    return subprocess.run([sys.executable, '-B', '-P', '-S', '-X', 'utf8', '-m', 'taskboard.wizard', str(request_path)], cwd=runtime, env=env).returncode


def main():
    if sys.version_info < (3, 11):
        print('Taskboard 需要 Python 3.11 或更新版本。请重新运行启动文件获取安装引导。')
        return 1
    try:
        return run(Path(sys.argv[1]) if len(sys.argv) == 2 else Path(__file__).with_name('request.json'))
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print('启动未完成：' + str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('已取消。重新双击启动文件可以继续查看本地状态。')
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
