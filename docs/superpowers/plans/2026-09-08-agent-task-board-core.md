# Agent Task Board Core Implementation Plan

> 历史方案：已被 GitHub-only 实现替代。以仓库 `docs/github-only.md` 和当前代码为准；不再部署独立协调服务或 SQLite 后台。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现从发布可执行子任务，到唯一有效认领、CLI 执行、结果投递和原会话验收的可恢复核心闭环。

**Architecture:** Python 标准库实现协议、SQLite 协调状态及本地启动器；GitHub Issue 作为人可读投影。CLI 适配器隔离 Codex/Claude 的差异，结果通过持久化 inbox 返回原会话。首轮以模拟 GitHub/agent 验证故障恢复，真实账号验证单独记录。

**Tech Stack:** Python 3.11+、SQLite、unittest、Git、现有 GitHub CLI、Codex CLI 与 Claude Code；不新增第三方依赖。

**Spec:** `docs/superpowers/specs/2026-09-08-agent-task-board-design.md`

## Global Constraints

- Python 3.11+；核心逻辑、本地状态、协调服务和测试仅使用标准库。
- 首个切片只执行 `subtask` 模式。
- 认领及状态转换以协调服务为准。
- 同时只能有一个有效认领。
- 命令按 argv 执行，不将 prompt、标题、链接或任务提供的字符串拼成 shell 命令。
- 原 session ID 和本地绝对路径无需放进供认领者读取的 Issue。
- 消息送达不等于验收完成。
- 没有新结果时不唤醒模型；无预算时不循环重启 agent。
- 本地协调服务仅绑定 loopback。
- 不修改现有用户文件或提交未被本任务创建的文件。
- 所有提交遵循用户提供的 Lore Commit Protocol，写清动机、验证与未测范围。

## 实施前提与模块边界

仓库当前没有应用代码、依赖清单、测试或 Git remote。以下路径是要创建的代码，不是已有接口。

| 文件或目录 | 职责 |
| --- | --- |
| `taskboard/contracts.py` | 任务/结果校验、规范化摘要和协议错误 |
| `taskboard/store.py` | SQLite 事务、认领、提交和业务事件 |
| `taskboard/artifacts.py` | 成果上传、哈希检查和持久化内容存储 |
| `taskboard/service.py` | 从认证后的调用上下文取得 actor，调度 store 操作 |
| `taskboard/server.py` | 仅用于本地联调的 HTTP JSON 接口 |
| `taskboard/github.py` | Issue 和评论投影、未知写入结果核对 |
| `taskboard/workspace.py` | 工作目录、Git 基准、资源完整性与路径检查 |
| `taskboard/agents.py` | Codex/Claude 进程入口、事件解析与停止 |
| `taskboard/origin.py` | 发起会话绑定、inbox、投递与验收 |
| `taskboard/cli.py`、`taskboard/__main__.py` | 命令行入口和机器可读错误 |
| `bin/taskboard` | 可从任意任务工作目录调用的本地启动脚本 |
| `tests/` | 协议、并发、恢复和端到端测试 |

包的 `__init__.py` 只标记包；不放配置、进程启动或全局数据库连接。

测试中的时钟、进程和 GitHub 接口可注入。生产路径使用服务端时间，不能信任客户端提交的 `now`。本地联调服务每次启动生成本地访问令牌，仅供当前机器使用；actor 来自该令牌绑定的测试身份，不能取自请求 JSON。

### Task 1: 可校验、可冻结的任务协议

**Files:** Create `taskboard/__init__.py`, `taskboard/contracts.py`, `tests/__init__.py`, `tests/support.py`, `tests/test_contracts.py`.

**Interfaces:**
- `ProtocolError(code: str, message: str)`，暴露 `.code`。
- `validate_task(task: dict) -> dict`，返回深拷贝的已校验对象。
- `task_digest(task: dict) -> str`。
- `validate_result(result: dict, task: dict, attempt_id: str) -> dict`。
- `tests.support.task_fixture() -> dict`，从仓库的任务 JSON 示例读取并深拷贝。

- [ ] 先加入以下失败测试，以及 UUID、版本、无 prompt、未知 agent 和越界路径用例。

```python
class ContractTests(unittest.TestCase):
    def test_digest_ignores_object_key_order(self):
        task = task_fixture()
        reversed_task = dict(reversed(list(task.items())))
        self.assertEqual(task_digest(task), task_digest(reversed_task))

    def test_resource_cannot_escape_workspace(self):
        task = task_fixture()
        task['resources'][0]['destination'] = '../outside.txt'
        with self.assertRaises(ProtocolError) as error:
            validate_task(task)
        self.assertEqual(error.exception.code, 'INVALID_RESOURCE_PATH')
```

- [ ] 运行 `python3 -m unittest tests.test_contracts -v`，确认因模块/函数尚未实现失败。
- [ ] 实现字段校验和摘要。UUID 用 `uuid.UUID`，commit 为 40 位十六进制字符串，资源哈希为 64 位；整数拒绝 bool。首版 agent 仅接受 `codex`、`claude`。时长和次数必须为正整数。argv 必须为非空字符串数组。摘要实现固定如下。

```python
def task_digest(task: dict) -> str:
    raw = json.dumps(task, ensure_ascii=False, sort_keys=True,
                     separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()
```

- [ ] 结果校验逐项核对 task ID、revision、digest、attempt ID 和 base commit；不匹配分别报告 `WRONG_TASK`、`STALE_REVISION`、`TASK_DIGEST_MISMATCH`、`STALE_ATTEMPT`、`BASE_MISMATCH`。检查必要产物存在、哈希格式正确、验证 argv 与退出码结构合法。
- [ ] 重跑该测试模块，提交协议和测试。提交动机：使跨成员任务交接具有可验证的固定输入。

### Task 2: 具有期限和尝试编号的认领

**Files:** Create `taskboard/store.py`, `tests/test_store.py`.

**Interfaces:**
- `TaskStore(path: Path)`，每个操作创建自己的 SQLite 连接；连接设置 busy timeout。
- `create(task: dict, *, owner: str, key: str, now: float) -> dict`。
- `claim(task_id: str, revision: int, *, actor: str, key: str, now: float) -> dict`。
- `start(task_id: str, *, actor: str, attempt_id: str, lease: str, now: float) -> dict`。
- `heartbeat(task_id: str, *, actor: str, attempt_id: str, lease: str, now: float) -> dict`。
- `release(task_id: str, *, actor: str, attempt_id: str, lease: str, reason: str, now: float) -> dict`。
- `cancel(task_id: str, revision: int, *, actor: str, key: str, now: float) -> dict`，仅 owner 可以取消当前非 accepted 版本。
- `expire(*, now: float) -> int`，返回失效的认领数。
- `get(task_id: str) -> dict`，返回可展示状态，不返回 lease token。

建立 `tasks`、`attempts`、`idempotency`、`events` 表。任务主键为 `(task_id, revision)`，尝试主键为 attempt ID，幂等记录键为 `(actor, operation, key)`。数据库保存 lease token 的 SHA-256，只向合法认领者首次响应返回原值；幂等 claim 重放必须能安全返回同一结果，需将原响应保存在受限协调存储中，展示接口永不返回该响应。

- [ ] 写并发测试，两个独立 `TaskStore` 实例竞争同一任务；断言一个成功、另一个 `ALREADY_CLAIMED`。

```python
def claim_once(store, actor):
    try:
        return store.claim(TASK_ID, 1, actor=actor, key=actor, now=10)
    except ProtocolError as error:
        return {'error': error.code}

with ThreadPoolExecutor(max_workers=2) as pool:
    responses = list(pool.map(
        lambda actor: claim_once(TaskStore(db_path), actor), ['alice', 'bob']))
assert sum('lease_token' in item for item in responses) == 1
assert sum(item.get('error') == 'ALREADY_CLAIMED' for item in responses) == 1
```

- [ ] 运行 `python3 -m unittest tests.test_store -v`，确认失败。
- [ ] 以 `BEGIN IMMEDIATE` 包住状态检查、attempt 分配、幂等响应及事件写入；所有异常回滚。生成 attempt UUID 和随机 lease；期限为服务端时间加 120 秒。校验当前版本、状态、actor、attempt、token 和期限后才允许更新。
- [ ] 添加期限后重新认领、尚未 expire 清理的过期 heartbeat/submit、错误 actor、取消后的执行、相同幂等键不同参数、服务重启恢复测试。每个写事务检查 `expires_at > now`，过期 lease 不得复活。失效后若耗尽尝试次数则 failed，否则 open；重复 expire 不重复计数。
- [ ] 添加当前任务版本指针，create 在同一事务检查 revision 必须为当前值加 1，且旧版本没有 claimed/running/submitted 工作；拒绝同时执行两个版本，保留旧版本历史。
- [ ] 运行该模块并提交。提交动机：避免并发或失联认领导致重复接纳执行结果。

### Task 3: 提交、验收与持久化事件

**Files:** Modify `taskboard/store.py`; create `taskboard/artifacts.py`, `tests/test_artifacts.py`, `tests/test_results.py`.

**Interfaces:**
- 本任务将 `TaskStore` 构造扩展为 `TaskStore(path: Path, *, artifact_store: ArtifactStore | None = None)`；省略时使用数据库目录下的 `artifacts/`。
- `submit(task_id: str, result: dict, *, actor: str, attempt_id: str, lease: str, key: str, now: float) -> dict`。
- `accept(task_id: str, revision: int, *, actor: str, attempt_id: str, result_id: str, result_digest: str, evidence: dict, key: str, now: float) -> dict`。
- `reject(task_id: str, revision: int, *, actor: str, attempt_id: str, result_id: str, result_digest: str, reason: str, key: str, now: float) -> dict`。
- `events_after(cursor: int, *, owner: str) -> list[dict]`，事件有递增 cursor、UUID event ID、task ID、revision 和 payload。
- `ArtifactStore(root: Path)` 提供 `put(stream: BinaryIO, *, expected_sha256: str) -> dict` 和 `open(sha256: str) -> BinaryIO`；访问授权由 service 在调用前执行。

- [ ] 写测试：提交后状态只能为 submitted；执行者不能验收；结果版本、摘要或 attempt 错误会被拒绝；新进程读取相同数据库仍能收到事件。

```python
first = store.submit(TASK_ID, result, actor='bob', attempt_id=claim['attempt_id'],
                     lease=claim['lease_token'], key='submit-1', now=20)
again = store.submit(TASK_ID, result, actor='bob', attempt_id=claim['attempt_id'],
                     lease=claim['lease_token'], key='submit-1', now=21)
assert first['result_id'] == again['result_id']
assert store.get(TASK_ID)['status'] == 'submitted'
```

- [ ] 运行 `python3 -m unittest tests.test_artifacts tests.test_results -v`，确认失败。
- [ ] ArtifactStore 按块读取 stream 并累计 SHA-256，超过 20 MiB 立即拒绝；临时文件完成后 fsync，核对摘要，再原子重命名为 hash。读取只接受合法 hash。测试中删除执行目录并重开 store，确认成果仍能读取。
- [ ] 添加 `results` 表；先处理已存在的合法幂等请求，再校验当前 lease、产物是否存在及合计大小是否不超过 100 MiB。生成 result UUID 和摘要；结果、submitted 状态和通知事件同事务写入。只有 owner 可 accept/reject，且 revision/attempt/result ID/digest 必须指向当前 submitted 成果，否则 `STALE_RESULT`。
- [ ] 对缺失产物、hash 错误、超限、结果提交后的重复请求、同键异内容、取消后的提交、旧 attempt 迟到、旧成果的延迟验收、验收幂等和拒收耗尽重试次数加入断言。
- [ ] 运行 store/results 模块并提交。提交动机：保留执行和验收的责任边界并支持断线恢复。

### Task 4: 资源准备与可控制的 CLI 执行

**Files:** Create `taskboard/workspace.py`, `taskboard/agents.py`, `tests/test_workspace.py`, `tests/test_agents.py`, `tests/fixtures/fake_agent.py`.

**Interfaces:**
- `prepare_workspace(task: dict, root: Path, fetcher) -> Path`；fetcher 暴露 `checkout(repository, commit, target)` 和 `read_resource(resource) -> bytes`。
- `AgentProcess` 暴露 `session_id`, `events() -> Iterator[dict]`, `wait() -> int`, `stop() -> None`。
- `CodexAdapter.start(workspace: Path, prompt: str, limits: dict) -> AgentProcess`。
- `CodexAdapter.resume(session_id: str, workspace: Path, message: str) -> AgentProcess`。
- `ClaudeAdapter` 提供相同的 start/resume 签名。

- [ ] 用临时本地 Git 仓库及内存 fetcher 写测试，覆盖错误 hash、错误 commit、绝对路径、`..`、符号链接、重名路径和工作区残留；错误发生时断言 agent 未启动。
- [ ] 模拟 agent 以 JSONL 输出启动、进度和完成事件；覆盖异常退出、无完成事件、坏 JSON、超时和停止。首个 CLI smoke 只运行 `--help`，不消耗真实模型额度。
- [ ] 运行 `python3 -m unittest tests.test_workspace tests.test_agents -v`，确认失败。
- [ ] 在任务 UUID/attempt 对应的新目录准备源代码和资源；拒绝跟随逃逸链接。真实 Git fetch/checkout 通过 argv 调用；先核对 commit，再应用经过审核且有哈希的未提交改动包。不得改动用户当前 checkout。
- [ ] Codex start 使用 `codex exec --json --sandbox workspace-write -` 并将 prompt 写入 stdin，从 `thread.started.thread_id` 获取 session ID。恢复使用已核对版本的 `codex exec resume <session_id> - --json`，把消息写入 stdin。
- [ ] Claude start 使用 `claude -p --output-format stream-json --verbose`，prompt 从 stdin 输入；从 `system/init` 或明确的 session 事件取 ID。恢复加 `--resume <session_id>`。由适配器配置显式工具权限，不采用取消全部权限检查的参数。
- [ ] 启动器持有进程组并在超时、取消或 lease 丢失时终止子进程组，收集退出状态；没有 provider 完成事件不得报告成功。结果正文与本地产物另行核对，不能只依赖退出码。
- [ ] 分别运行模拟适配器测试，再完成两个 CLI 的只读参数检查，提交。真实模型运行与计费仍需后面的真实联调。

### Task 5: 原会话绑定与可靠的结果接收

**Files:** Create `taskboard/origin.py`, `tests/test_origin.py`.

**Interfaces:**
- `OriginStore(path: Path)`。
- `bind(task_id: str, revision: int, *, provider: str, host_id: str, session_id: str, workspace: str, checkpoint: dict) -> None`。
- `receive(event: dict) -> bool`，新入箱返回 True，重复 event ID 返回 False。
- `deliver_next(*, session_probe, adapter, budget_probe) -> dict`；probe 从启动器实际状态取值。
- `resolve_delivery(delivery_id: str, *, outcome: str, evidence: str) -> None`。

数据表为 `bindings`、`inbox`。绑定按任务版本查找；只有当前机器才能对其本地会话投递。接收事件和前进 cursor 在同一事务，cursor 不先于 inbox 持久化。跨进程执行锁使用 macOS/Linux 的 `fcntl.flock`，锁文件名由 provider/host/session 的规范化值计算 hash；所有管理中的源会话运行也遵守同一把锁。

- [ ] 先写重复事件、会话忙、原会话丢失、无额度、错误 host，以及数据库重开恢复测试。

```python
assert origin.receive(event) is True
assert origin.receive(event) is False
outcome = origin.deliver_next(session_probe=lambda _: 'busy',
                              adapter=recording_adapter,
                              budget_probe=lambda _: 'available')
assert outcome['status'] == 'pending'
assert recording_adapter.calls == []
```

- [ ] 运行 `python3 -m unittest tests.test_origin -v`，确认失败。
- [ ] 先以非阻塞方式取得会话锁，再确认该会话由启动器管理且为空闲/退出并记录仍存在；外部会话或状态不确定时继续排队。构造包含 delivery ID、来源、task revision、摘要和产物引用的结果消息，通过对应 adapter 投递。锁持有到 provider 结束及结果持久化；投递前保存 delivering，provider 确认后保存 delivered；验收由原 agent 后续调用完成。
- [ ] 对重启时遗留的 delivering 记录设为 delivery_unknown；不自动重发。`resolve_delivery` 只接受 `confirmed_delivered` 或 `confirmed_not_delivered`，必须附核对证据；后者回到 pending。
- [ ] 加测试模拟“provider 已收到消息，持久化 delivered 前崩溃”；重启不得造成第二次调用。再用两个进程向同一个会话投递不同任务，断言 adapter 不重叠运行。检查源代码基准改变会附带明确的重新验证要求，投递本身不应用 patch。
- [ ] 运行该模块并提交。提交动机：让异步结果回到正确会话，同时避免重复唤醒或错误验收。

### Task 6: GitHub 投影、CLI 与端到端联调

**Files:** Create `taskboard/github.py`, `taskboard/service.py`, `taskboard/server.py`, `taskboard/cli.py`, `taskboard/__main__.py`, `bin/taskboard`, `resources/publisher-instructions.md`, `tests/test_github.py`, `tests/test_cli.py`, `tests/test_end_to_end.py`; update `README.md` with verified commands.

**Interfaces:**
- `GitHubProjection(transport)`，transport 提供 `request(method: str, path: str, body: dict | None) -> dict | list`。
- `publish(task: dict, *, repository: str) -> dict`。
- `project_event(issue: dict, event: dict) -> dict`。
- `reconcile(operation: dict) -> dict`，结果为 confirmed/pending/conflict。
- `TaskService(store, policy, clock)`，actor 从本地认证上下文获取。
- `TaskService.get(task_id: str, *, actor: str) -> dict`，校验访问权限后返回任务和当前成果引用。
- `main(argv: list[str] | None = None) -> int`。

CLI 首批命令为 `start --agent codex --prompt FILE`、`draft TASK_JSON`、`publish TASK_JSON`、`list`、`claim TASK_ID`、`run TASK_ID`、`sync`、`accept TASK_ID --result RESULT_ID --evidence FILE`、`reject TASK_ID --result RESULT_ID --reason TEXT`。机器模式使用 `--json`；协议错误例如 `{"error":{"code":"STALE_REVISION","message":"任务版本已过期"}}` 输出到 stderr 并非零退出，不把失败打印成成功摘要。

accept/reject 的 CLI 根据显式 result ID 读取其不可变记录，补齐 revision、attempt ID 和 result digest 后调用 service；不能以“当前最新成果”替换用户指定的 result ID。

- [ ] 用 recording transport 测试 Issue 正文包含稳定 task/revision 标记；评论包含 event 标记。模拟远端写入成功但请求超时，重试先 reconcile，不直接再次 POST。
- [ ] 实现 GitHub transport，通过 `subprocess.run(['gh', 'api', '--method', method, path, '--input', '-'], input=json.dumps(body), text=True, capture_output=True)` 传递 JSON；GET 不传 body。检查退出码，解析响应，错误日志不包含认证值。
- [ ] 为本地 server 添加请求长度限制、JSON 类型校验、本地令牌检查、稳定错误映射和仅 loopback 监听。service 调用 store 时填充认证 actor 与服务端 now。
- [ ] `bin/taskboard` 从自身路径找到项目根并导入 `taskboard.cli.main`，不依赖当前任务目录。`start` 生成 origin run ID，通过 `TASKBOARD_ORIGIN_RUN_ID` 和本地启动器绑定文件传递来源；先捕获并保存真实 session 事件，再允许带该 run ID 的 publish。生成发布说明，写明可委派条件、完整任务包要求、CLI 路径和无结果时退出等待的规则。
- [ ] CLI 调用共用的 service/client 路径；不能绕过策略直接发布。`run` 复用本人的当前有效 claim，或原子认领后执行；资源准备失败释放或标记失败，不遗留无期限占用。
- [ ] 建立下面的端到端测试，使用两个本地身份、真实 SQLite、临时 Git 仓库、模拟 GitHub 和模拟 CLI。

```python
task = owner.publish(task_fixture())
claim = contributor.claim(task['task_id'])
contributor.run(task['task_id'])
submitted = service.get(task['task_id'], actor='owner')
assert submitted['status'] == 'submitted'
owner.sync()
owner.sync()
assert source_agent.resume_calls == 1
owner.accept(task['task_id'], result_id=submitted['current_result']['result_id'],
             evidence={'checks': ['fixture verification passed']})
assert service.get(task['task_id'], actor='owner')['status'] == 'accepted'
```

- [ ] 测试用的 `owner`、`contributor` 封装真实 service/runner；只模拟模型和 GitHub 网络。再运行过期认领、迟到结果、原会话忙、恢复未知投递和 GitHub 中断的端到端用例。
- [ ] 运行 `python3 -m unittest discover -s tests -v`、`python3 -m compileall -q taskboard tests`、`git diff --check`。只有新故障或改动才扩大/重复检查。
- [ ] 更新 README：写明已测试的本地命令、支持平台、CLI 版本、配置路径、如何停止服务，以及真实联调尚未覆盖的项目；提交完整核心闭环。

## 后续交付：任务大厅和真实组织试点

核心切片通过后，界面提供“任务大厅 / 我发布的 / 我认领的”，操作仅调用已测试接口；首次配置建立本地启动入口。页面上的“认领并运行”要显示资源检查、运行、提交和待验收状态。

真实联调需要指定组织私有任务仓库、协调服务部署位置、两名参与者的独立登录和可用额度。服务部署需完成真实身份认证与权限隔离，不能使用本地测试身份机制。

真实任务验收：发布一个边界明确的子任务，由另一位成员执行，回传到原会话，原 agent 在当前代码状态下验收并继续。记录 token/费用的实际来源、人工介入、等待时间和验证结果；不能把模拟 agent 测试报告写成这个验收已经完成。

## 设计覆盖自查

| 设计要求 | 实施任务 |
| --- | --- |
| 完整任务包、版本固定、路径及资源校验 | 1、4 |
| 唯一有效认领、超时与重试 | 2 |
| 执行与验收分离、结构化结果 | 3 |
| 独立工作区、CLI 登录与事件 | 4 |
| 原会话关联、离线恢复、忙时排队、未知投递 | 5 |
| GitHub 记录、政策校验与状态同步 | 6 |
| 真实用户体验、账号及计费验证 | 后续真实组织试点 |

此文档中的测试是待实施步骤，当前只完成设计文档与协议示例的检查。
