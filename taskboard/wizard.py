"""Terminal first-run guidance for a verified task download."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import urlsplit
import webbrowser

from . import cli
from .github import GitHub
from .local import LocalStore, hostname_name, regular_path, repository_name
from .platforms import application_home, executable_argv
from .protocol import ProtocolError
from .workspace import repository_url

CLI_WAIT_SECONDS = 30
INSTALL_PAGES = {'git': 'https://git-scm.com/downloads/', 'gh': 'https://cli.github.com/', 'codex': 'https://learn.chatgpt.com/docs/codex/cli', 'claude': 'https://code.claude.com/docs/en/setup'}
LABELS = {'git': 'Git（读取任务代码）', 'gh': 'GitHub CLI（公司 GitHub 登录和回传结果）', 'codex': 'Codex CLI', 'claude': 'Claude Code CLI'}


def read_request(path: Path) -> dict:
    regular_path(path)
    if path.stat().st_size > 4096:
        raise ProtocolError('INVALID_DOWNLOAD', '请求文件过大，请从任务网页重新下载。')
    try:
        request = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, UnicodeError) as exc:
        raise ProtocolError('INVALID_DOWNLOAD', '请求文件必须为 UTF-8 JSON。') from exc
    base = {'schema_version', 'hostname', 'repository', 'runtime_sha256', 'action'}
    locator = {'issue', 'revision', 'task_digest'}
    if not isinstance(request, dict) or set(request) not in (base, base | locator) or type(request.get('schema_version')) is not int or request['schema_version'] != 1:
        raise ProtocolError('INVALID_DOWNLOAD', '下载请求格式不受支持。')
    if request['action'] not in ('run', 'install-publisher') or request['action'] == 'run' and not locator <= request.keys():
        raise ProtocolError('INVALID_DOWNLOAD', '下载请求缺少动作或任务定位。')
    request['hostname'] = hostname_name(request['hostname'])
    request['repository'] = repository_name(request['repository'])
    for field in ('runtime_sha256', *(['task_digest'] if 'task_digest' in request else [])):
        if not isinstance(request[field], str) or not re.fullmatch(r'[0-9a-f]{64}', request[field]):
            raise ProtocolError('INVALID_DOWNLOAD', '下载请求的 SHA-256 校验值无效。')
    for field in ('issue', 'revision'):
        if field in request and (type(request[field]) is not int or not 1 <= request[field] <= 9999999999):
            raise ProtocolError('INVALID_DOWNLOAD', '下载请求的任务编号或版本无效。')
    return request


def _display(value) -> str:
    return ''.join(char for char in str(value) if char in '\n\t' or ord(char) >= 32 and ord(char) != 127)


def _choice(message: str, choices: set[str]) -> str:
    while True:
        answer = input(message).strip()
        if answer in choices:
            if answer == '0':
                raise ProtocolError('CANCELLED', '已取消，本地状态保留。')
            return answer
        print('请输入菜单中的数字。')


def _refresh_path() -> None:
    candidates = [str(Path.home() / '.local' / 'bin')]
    if os.name == 'nt':
        import winreg
        for hive, key in ((winreg.HKEY_CURRENT_USER, 'Environment'), (winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment')):
            try:
                with winreg.OpenKey(hive, key) as handle:
                    candidates.extend(os.path.expandvars(winreg.QueryValueEx(handle, 'Path')[0]).split(os.pathsep))
            except OSError:
                pass
    elif sys.platform == 'darwin':
        candidates.extend(('/opt/homebrew/bin', '/usr/local/bin'))
    os.environ['PATH'] = os.pathsep.join(dict.fromkeys([*os.environ.get('PATH', '').split(os.pathsep), *(path for path in candidates if path and Path(path).is_dir())]))


def _native_command(argv: list[str], *, interactive: bool = False) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(executable_argv(argv), stdin=None if interactive else subprocess.DEVNULL, capture_output=not interactive, timeout=900 if interactive else 20)
        if not interactive:
            # Decode here, outside Windows subprocess pipe-reader threads, so
            # malformed output becomes a failed check instead of a lost error.
            result.stdout = result.stdout.decode('utf-8')
            result.stderr = result.stderr.decode('utf-8')
        return result
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        return subprocess.CompletedProcess(argv, 127, '', str(exc))


def installation_argv(tool: str) -> list[str] | None:
    if sys.platform == 'darwin' and shutil.which('brew'):
        return ['brew', 'install', *(['--cask'] if tool in {'codex', 'claude'} else []), {'claude': 'claude-code'}.get(tool, tool)]
    if os.name == 'nt' and shutil.which('winget') and tool in {'git', 'gh', 'claude'}:
        return ['winget', 'install', '--id', {'git': 'Git.Git', 'gh': 'GitHub.cli', 'claude': 'Anthropic.ClaudeCode'}[tool], '--exact']
    if tool == 'codex' and shutil.which('npm'):
        return ['npm', 'install', '--global', '@openai/codex']
    return None


def ensure_tool(tool: str) -> None:
    while True:
        _refresh_path()
        try:
            if _native_command([tool, '--version']).returncode == 0:
                return
        except ProtocolError as exc:
            print(exc.message)
        print(f'\n尚未找到可用的 {LABELS[tool]}。')
        installer = installation_argv(tool)
        print('1 打开官方安装说明（按网页步骤安装）  2 安装后重新检查  0 取消')
        if installer:
            print('3 同意使用已有软件管理器安装：' + ' '.join(installer))
            print('此操作会安装软件；请阅读安装器的权限与提示。')
        choice = _choice('选择：', {'0', '1', '2', *(['3'] if installer else [])})
        if choice == '1':
            webbrowser.open(INSTALL_PAGES[tool])
            print('官方地址：' + INSTALL_PAGES[tool])
        elif choice == '3':
            if _native_command(installer, interactive=True).returncode:
                print('安装未完成。可打开官方说明，或请公司 IT 协助安装后重新检查。')


def ensure_login(tool: str, hostname: str) -> None:
    if tool == 'gh':
        status = ['gh', 'auth', 'status', '--hostname', hostname]
        login = ['gh', 'auth', 'login', '--hostname', hostname, '--git-protocol', 'https', '--web']
    elif tool == 'codex':
        status, login = ['codex', 'login', 'status'], ['codex', 'login']
    else:
        status, login = ['claude', 'auth', 'status'], ['claude', 'auth', 'login']
    while _native_command(status).returncode:
        print(f'\n{LABELS[tool]} 需要登录。登录只由官方 CLI 处理，下载包不保存账号密钥。')
        if tool == 'gh':
            print('将在浏览器中登录 ' + hostname + '；如公司要求 SSO，请在浏览器中完成组织授权。')
        choice = _choice('1 开始官方登录  2 已在其他窗口登录，重新检查  0 取消：', {'0', '1', '2'})
        if choice == '1' and _native_command(login, interactive=True).returncode:
            print('登录未完成，请检查浏览器和公司账号后重试。')


def _record(client: GitHub, request: dict) -> dict:
    record = cli._record(client, request['issue'])
    if record['spec']['revision'] != request['revision'] or record['digest'] != request['task_digest']:
        raise ProtocolError('STALE_DOWNLOAD', '任务版本已改变。未领取或启动，请返回网页重新下载此任务。')
    return record


def _repositories(task: dict, hostname: str) -> list[str]:
    urls = [task['source']['repository'], *(resource['repository'] for resource in task['resources'])]
    repos = list(dict.fromkeys(repository_name(urlsplit(url).path.strip('/')) for url in urls))
    config = {'hostname': hostname, 'allowed_repos': repos}
    for url in urls:
        repository_url(url, config)
    return repos


def _select_provider(compatible: list[str]) -> str:
    print('\n选择本机执行代理（使用你自己的账号与额度）：')
    for index, provider in enumerate(compatible, 1):
        print(f'{index} {LABELS[provider]}')
    print('0 取消')
    choice = _choice('选择：', {'0', *(str(index) for index in range(1, len(compatible) + 1))})
    return compatible[int(choice) - 1]


def _confirmed(store: LocalStore, record: dict, actor: str) -> Path | None:
    attempt = record.get('attempt')
    if record['status'] not in {'submitted', 'accepted'} or not attempt or attempt['actor'].casefold() != actor.casefold():
        return None
    job = store.data()['runs'].get(attempt['id'])
    if not job or job.get('issue') != record['number']:
        return None
    return cli._confirmed_submission(store, record, attempt['id'], job)


def _pending(store: LocalStore, record: dict) -> bool:
    data = store.data()
    attempt = record.get('attempt')
    if attempt:
        job = data['runs'].get(attempt['id'])
        if job and job.get('issue') == record['number'] and job.get('phase') in {'start_pending', 'artifacts_ready', 'uploaded', 'submit_pending'}:
            return True
    return any(item.get('issue') == record['number'] and item.get('phase') in {'posting', 'posted', 'unknown'} and item.get('command', {}).get('op') in {'claim', 'start', 'submit_bundle', 'submit'} and item['command']['request_id'] not in record.get('processed', {}) for item in data['requests'].values())


def _execute(store: LocalStore, client: GitHub, request: dict, provider: str, initial: dict) -> int:
    actor = client.user()
    before = initial['attempt_count']
    generation = before + (1 if initial['status'] == 'open' else 0)
    attempt_id = initial['attempt']['id'] if initial.get('attempt') else None
    waiting_since = time.monotonic()
    while True:
        record = _record(client, request)
        result = _confirmed(store, record, actor)
        if result:
            print('\nGitHub 已确认成果提交，正在等待发布者验收。\n本地成果：' + str(result))
            webbrowser.open(f'https://{request["hostname"]}/{request["repository"]}/issues/{request["issue"]}')
            return 0
        attempt = record.get('attempt')
        if record['status'] == 'open':
            if attempt_id or initial['status'] != 'open' or record['attempt_count'] != before:
                raise ProtocolError('ATTEMPT_CHANGED', '原执行已结束或被释放。请回任务网页检查状态；不会自动重新领取或重跑模型。')
        elif record['status'] in {'claimed', 'running'} and attempt:
            if record['attempt_count'] != generation or attempt['actor'].casefold() != actor.casefold() or attempt_id and attempt['id'] != attempt_id or attempt['expires_at'] <= int(time.time()):
                raise ProtocolError('ATTEMPT_CHANGED', '执行归属或有效期已改变。请回网页检查；不会重新领取或重跑模型。')
            attempt_id = attempt['id']
        else:
            raise ProtocolError('TASK_UNAVAILABLE', '任务当前不能执行，或成果不是本机此次执行。请返回任务网页查看。')
        code = cli.main(['--home', str(store.home), 'run', str(request['issue']), '--agent', provider, '--wait', str(CLI_WAIT_SECONDS), '--expected-attempt-count', str(generation), '--expected-task-digest', request['task_digest']])
        record = _record(client, request)
        result = _confirmed(store, record, actor)
        if result:
            continue
        if record.get('attempt') and record['attempt_count'] == generation and record['attempt']['actor'].casefold() == actor.casefold():
            if attempt_id and record['attempt']['id'] != attempt_id:
                raise ProtocolError('ATTEMPT_CHANGED', '执行归属已改变，本机不会继续自动提交。')
            attempt_id = record['attempt']['id']
        if not _pending(store, record):
            print('\n此次操作未完成。执行和日志保存在：' + str(store.home))
            print('请检查上面的原因和任务网页；已失败或状态不明的模型执行不会自动重跑。')
            return code or 1
        if time.monotonic() - waiting_since > 600:
            _choice('GitHub 仍在确认。1 继续等待  0 退出并保留本地状态：', {'0', '1'})
            waiting_since = time.monotonic()
        print('正在等待 GitHub Actions 确认，或重试回传本地成果。模型不会重复执行。')
        time.sleep(2)


def _workspace() -> Path:
    while True:
        choice = _choice('1 在窗口中选择原项目文件夹  2 粘贴或拖入文件夹路径  0 取消：', {'0', '1', '2'})
        value = ''
        if choice == '1':
            try:
                import tkinter
                from tkinter import filedialog
                root = tkinter.Tk()
                root.withdraw()
                try:
                    value = filedialog.askdirectory(title='选择已有 CLI 会话的原项目文件夹')
                finally:
                    root.destroy()
            except (ImportError, RuntimeError, tkinter.TclError if 'tkinter' in locals() else RuntimeError):
                print('本机无法打开文件夹选择器，请选择 2 粘贴或拖入路径。')
        else:
            value = input('文件夹路径：').strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
        if not value:
            continue
        path = Path(value).expanduser().absolute()
        regular_path(path)
        if path.is_dir():
            result = _native_command(['git', '-C', str(path), 'rev-parse', '--show-toplevel'])
            if result.returncode == 0 and result.stdout.strip():
                project = Path(result.stdout.strip()).absolute()
                regular_path(project)
                return project
        print('请选择已经存在的 Git 项目文件夹。')


def _install_publisher(store: LocalStore, request: dict) -> int:
    from .integration import install_integration
    provider = _select_provider(['codex', 'claude'])
    ensure_tool(provider)
    ensure_login(provider, request['hostname'])
    project = _workspace()
    print('\n将把发布技能和会话绑定配置安装到：' + str(project))
    print('原代理只会在你明确同意具体任务后发布。安装后请按代理提示信任或重载配置，然后继续原项目的原会话。')
    _choice('1 同意安装项目配置  0 取消：', {'0', '1'})
    store.initialize(request['repository'], request['hostname'], [], provider)
    result = install_integration(provider, project, store)
    print('\n发布技能已安装。请回到 ' + LABELS[provider] + ' 的原项目会话，要求它准备可委派的任务。')
    print('技能位置：' + _display(result['skill']))
    return 0


def run_request(request_path: Path) -> int:
    try:
        request = read_request(Path(request_path).absolute())
        identity = hashlib.sha256(f'{request["hostname"]}/{request["repository"]}'.casefold().encode()).hexdigest()[:24]
        store = LocalStore(os.environ.get('TASKBOARD_HOME') or application_home() / 'boards' / identity)
        print('Taskboard 启动引导\n任务看板：' + request['hostname'] + '/' + request['repository'])
        for tool in ('git', 'gh'):
            ensure_tool(tool)
        ensure_login('gh', request['hostname'])
        if request['action'] == 'install-publisher':
            return _install_publisher(store, request)
        client = GitHub(request['repository'], request['hostname'])
        record = _record(client, request)
        repos = _repositories(record['spec'], request['hostname'])
        print('\n任务：' + _display(record['spec']['title']))
        print('将读取以下仓库的指定提交，使用本机代理执行任务，并回传成果：\n' + '\n'.join('- ' + request['hostname'] + '/' + repo for repo in repos))
        print('本次验收会在任务工作区运行：' + _display(json.dumps(record['spec']['acceptance']['commands'], ensure_ascii=False)))
        provider = _select_provider(record['spec']['execution']['compatible_agents'])
        ensure_tool(provider)
        ensure_login(provider, request['hostname'])
        _choice('1 确认领取并执行这个任务  0 取消：', {'0', '1'})
        store.initialize(request['repository'], request['hostname'], repos, provider)
        with store.exclusive(f'wizard:{request["issue"]}'):
            return _execute(store, client, request, provider, record)
    except ProtocolError as exc:
        print(f'{exc.code}: {exc.message}', file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print('本机操作未完成：' + str(exc), file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print('\n已退出，本地状态保留。重新打开启动文件可检查状态。', file=sys.stderr)
        return 130


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python -m taskboard.wizard request.json')
    raise SystemExit(run_request(Path(sys.argv[1])))
