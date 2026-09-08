# Agent Relay · Agent Task Board

**中文** | [English](README.en.md)

让有额度压力的成员发布完整的 agent 任务包，由有余量的成员使用自己的 CLI 接力执行，再把成果送回发起者验收或接续原会话。

**正式使用全部通过 GitHub Pages 查看，不需要本地页面、HTTP 服务或额外部署的后台。** 查看任务的人只需浏览器；发布、执行和接续会话的人使用本地 CLI。线上组件只有 GitHub Issues、Actions、Pages 和 Release 附件，支持公司内网 GitHub 域名。

## 目录

- [1. 先选择你的使用角色](#roles)
- [2. 管理员：部署一次](#deployment)
- [3. 发布者与领取者：初始化本机](#setup)
- [4. 如何发布任务](#publish)
- [5. 如何领取并执行任务](#execute)
- [6. 如何查看、验收和接回结果](#results)
- [7. 状态、时限与常用命令](#reference)
- [8. 常见问题与恢复](#troubleshooting)
- [9. 开发检查与可选预览](#development)
- [10. 当前范围与文件入口](#scope)

<a id="roles"></a>
## 1. 先选择你的使用角色

| 角色 | 从哪里开始 | 需要安装什么 |
| --- | --- | --- |
| 只查看任务 | 打开管理员提供的 GitHub Pages 地址 | 无，只需浏览器及公司网络访问能力 |
| 管理员 | [部署仓库和 Pages](#deployment) | 公司可用的 GitHub Actions runner |
| 发布者 | [初始化本机](#setup)，再[发布任务](#publish) | Git、Python、GitHub CLI；使用 agent 自主发布或接续时还需要对应 agent CLI |
| 领取者 | [初始化本机](#setup)，再[领取并运行](#execute) | Git、Python、GitHub CLI，以及任务兼容的 Codex CLI 或 Claude Code |

一次完整接力是：**发布 → Actions 确认 → 领取 → 本地执行 → 提交成果 → 发起者验收**。Pages 显示的是异步更新的快照；网页上显示“待领取”，不代表点击时一定仍可领取。本地工具会重新确认归属。

本手册中的 `git.company.example`、`team/agent-task-board`、`team/demo-api`、路径、Issue `42` 和 UUID 都是说明用值，请替换为实际值。不要直接执行示例仓库中的虚构任务。

<a id="deployment"></a>
## 2. 管理员：部署一次

### 2.1 准备任务仓库与 runner

1. 将此项目完整导入公司 GitHub 仓库的**默认分支**，保留 `.github/` 目录。
2. 启用仓库的 Issues、Actions 和 Pages。
3. 确保 Actions 可以按工作流声明使用 `contents: write`、`issues: write` 和 `pages: write`；组织策略也必须允许这些操作。
4. 准备 runner：GitHub.com 默认选择 `ubuntu-latest`；内网 GitHub Enterprise Server 默认选择公司已有的 `self-hosted` runner。

Runner 需要 Python **3.11+** 和 GitHub CLI（`gh`）；`Check task board` 工作流还需要 Node.js **18+**。无需安装 Python 或前端第三方包。内网不允许直接使用外部 Actions 时，将两个工作流中的 `actions/checkout@v4` 替换为公司已镜像的版本。

在 **Settings → Secrets and variables → Actions → Variables** 配置：

| 仓库变量 | 用途 | 设置方式 |
| --- | --- | --- |
| `TASKBOARD_RUNNER` | 选择公司 runner 的标签 | 可选，例如 `corp-linux`；填写标签本身，不加引号 |
| `TASKBOARD_PAGES_ENABLED` | 在更新 `gh-pages` 后显式请求 Pages 构建 | 首次生成分支后再设置为字符串 `true` |

协调工作流使用 GitHub 自动提供的凭据；不需要在仓库中配置成员的 agent 登录文件或模型 API key。

### 2.2 设置参与者权限

发布、领取、提交和验收的账号需要任务仓库的 **write / maintain / admin** 权限。成果通过 Release 附件上传，只读权限不能完成执行闭环。

默认 `.github/taskboard.json` 中的空名单允许所有具备上述权限的成员。需要进一步限制时，填写实际 GitHub 用户名：

```json
{
  "schema_version": 1,
  "allowed_members": ["alice", "bob"]
}
```

非空名单是额外限制，**不会授予 GitHub 原生写权限**。成员还需能访问任务所引用的源代码和资料仓库，并在自己的本机允许列表中加入这些仓库。

### 2.3 启用 Pages

1. 进入 **Actions → Task board controller → Run workflow**，首次手动运行。
2. 成功后会生成两个分支：`taskboard-state` 保存确认后的任务状态；`gh-pages` 保存静态页面和 `tasks.json`。
3. 在 **Settings → Pages → Build and deployment** 选择 **Deploy from a branch**，来源为 **`gh-pages` / `/ (root)`**，按本方案使用公开站点。
4. 将仓库变量 `TASKBOARD_PAGES_ENABLED` 设置为 `true`，再运行一次 **Task board controller**。
5. 将 GitHub 显示的实际 Pages 地址发给成员。内网域名和站点路径以公司环境的设置为准。

之后，任务和命令评论会触发更新；也可手动运行 controller 补处理。工作流每小时第 17、47 分钟配置了恢复扫描，实际启动时间取决于 GitHub 和 runner 的调度。

如果公司禁用 Pages build API，`gh-pages` 中的静态文件仍可交给公司现有 Pages 发布流程。无需另搭一个应用后台。协调器是状态分支的唯一写入者；不要手工修改 `taskboard-state/state.json`。

<a id="setup"></a>
## 3. 发布者与领取者：初始化本机

### 3.1 准备工具和代码

本地 CLI 当前支持 **macOS / Linux**；Windows 原生 CLI 尚未支持，浏览 Pages 不受此限制。一键唤起终端的入口目前支持 macOS，Linux 使用命令行。

需要 Git、Python 3.11+ 和 GitHub CLI；执行任务、使用 `start` 或恢复原会话时，还需要所选的 `codex` 或 `claude` 命令。先按公司方式安装和登录对应 agent，确认它使用你希望消耗的账号与额度。当前参数曾对照 Codex CLI `0.153.4`、Claude Code `2.1.263` 的帮助信息核验，这不是对所有旧版本的兼容承诺。

```sh
git --version
python3 --version
gh --version
```

登录公司 GitHub，完成组织所需的 SSO 授权，然后获取本项目：

```sh
gh auth login --hostname git.company.example
gh auth status --hostname git.company.example
gh repo clone https://git.company.example/team/agent-task-board
cd agent-task-board
./bin/taskboard --help
```

也可以解压源码包后进入目录。如果解压工具丢失执行权限，先运行 `chmod +x bin/taskboard`。

下面的 `./bin/taskboard` 示例均在**本项目根目录**执行，源代码工作目录通过参数单独指定。无需 `pip install` 或 `npm install`。如果要在任意目录使用页面复制的 `taskboard ...` 命令，将本项目的 `bin` 加入 shell 的 PATH：

```sh
export PATH="/absolute/path/to/agent-task-board/bin:$PATH"
```

把实际路径加入自己的 shell 启动配置后，新终端也可使用。安装 macOS 启动器后不要随意移动本项目目录，因为启动器会记录其路径。

### 3.2 初始化一个看板

```sh
./bin/taskboard init \
  --repo team/agent-task-board \
  --hostname git.company.example \
  --allow-repo team/demo-api \
  --agent codex
```

- `--repo` 是**任务看板仓库**，格式为 `owner/repo`。
- `--hostname` 只填写 GitHub 域名，不含 `https://`、端口或路径；省略时使用 `github.com`。
- `--allow-repo` 添加允许执行或读取资源的仓库，可重复传入。任务仓库自动加入；其他源代码和资料仓库必须显式加入。
- `--agent` 设置一键启动的默认 agent，值为 `codex` 或 `claude`；`run` 命令仍需显式指定 agent，Pages 的执行选择也可指定。

初始化只写本机配置，不创建 GitHub 仓库，也不修改 GitHub 权限。再次 `init` 同一看板可以追加仓库：

```sh
./bin/taskboard init \
  --repo team/agent-task-board \
  --hostname git.company.example \
  --allow-repo team/reference-docs \
  --agent codex
```

默认本地状态目录是 `~/.local/share/taskboard`；设置了 `TASKBOARD_HOME` 时使用该值。一个状态目录绑定一个看板。使用另一个看板时，在初始化和后续命令中一致传入单独的 `--home`：

```sh
./bin/taskboard --home /absolute/path/to/second-board-state init \
  --repo another-team/board \
  --hostname git.company.example \
  --allow-repo another-team/service
```

<a id="publish"></a>
## 4. 如何发布任务

### 4.1 先准备可独立执行的任务包

| 内容 | 需要写清楚什么 |
| --- | --- |
| 目标与 prompt | 背景、目标、执行要求、允许修改范围、交付要求；不能依赖“我之前的对话” |
| 源代码 | 同一 GitHub 主机上的 HTTPS 仓库地址、可访问的完整 40 位 commit SHA |
| Resources | 固定 commit 的 Git 文件、源路径、目标相对路径和实际 SHA-256；无需额外资源时可为空数组 |
| 执行要求 | 兼容 agent、时长上限、最多尝试次数 |
| 验收 | 命令的参数数组、必要交付文件、人工检查事项 |

只支持已提交的输入，`source.workspace_patch` 必须为 `null`。源代码和资源提交必须已经能从远端获取，本机未推送的 commit 不足以交接。

在源代码仓库中获取提交；资源哈希必须针对**指定 commit 中的文件内容**计算：

```sh
git -C /absolute/path/to/demo-api rev-parse HEAD

git -C /absolute/path/to/demo-api show COMMIT_SHA:docs/discount-rules.md \
  | python3 -c 'import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'
```

把 `COMMIT_SHA` 替换为实际的完整 commit SHA，并先确认 `git show` 成功。资源的 `destination` 应使用例如 `resources/discount-rules.md` 的新路径，不能覆盖 checkout 中已有的文件。

当前任务和请求 JSON 有 48 KiB 限制；执行时限为 1–14400 秒，最多尝试次数为 1–5。使用新的任务 UUID，通常从 `revision: 1` 开始；可用 `python3 -c 'import uuid; print(uuid.uuid4())'` 生成 UUID。

验收命令使用参数数组，例如 `["python3", "-m", "unittest"]`，不是 `"python3 -m unittest"` 这样的 shell 字符串。多个命令分别列出；需要手动检查时可使用空命令数组并写明验收说明。

本地工具自动生成 `changes.patch`、`summary.md` 和 `verification.json`。将它们列为必要产物即可，不要求 agent 在源码目录再创建同名文件。其他必要产物，例如 `reports/analysis.md`，必须由 agent 在工作目录内生成，其路径也要符合 `write_paths`。项目不会自动安装源项目依赖，任务必须明确可执行的准备与检查步骤。

每个上传成果文件不得超过 **20 MiB**，一次提交的成果文件合计不得超过 **100 MiB**。

### 4.2 方式一：在 GitHub Pages 填表，再用 CLI 发布

1. 打开正式 Pages 看板，点击 **“发布任务”**。
2. 填写目标、源代码 commit、修改范围、资源、兼容 agent 和验收要求。
3. 点击 **“导出任务 JSON”**，保存下载的文件。
4. 在本项目根目录验证并发布，使用实际下载路径：

```sh
./bin/taskboard validate /absolute/path/to/downloaded-task.json
./bin/taskboard publish /absolute/path/to/downloaded-task.json
```

**网页导出文件不会直接创建 Issue。** `publish` 成功后会打印 `Published: https://.../issues/42`。等待 Actions 校验，刷新 Pages 后才能看到确认后的任务。普通 `publish` 不自动知道已有会话的 session ID。

### 4.3 方式二：让 agent 自主决定是否发布

创建一个 UTF-8 文件 `work.md`，写入你的实际目标，例如：

```text
检查折扣计算模块并补充边界测试。
若任务可以独立交接、输入和验收都明确，且允许等待认领，
可以通过已配置的 Taskboard 发布子任务；否则继续在本地处理。
```

在本项目根目录启动受管理的原会话：

```sh
./bin/taskboard start \
  --agent codex \
  --prompt work.md \
  --workspace /absolute/path/to/demo-api
```

使用 Claude Code 时把 `--agent codex` 改为 `--agent claude`。`start` 使用非交互 CLI，加入[委派说明](resources/publisher-instructions.md)，从真实启动事件捕获 session ID。Agent 调用 `publish` 时会自动把新 Issue 绑定到这个原会话；关联仅保存在本机。

启动 `start` **不保证一定发布**：agent 会根据任务可交接性选择本地完成或发布。默认原会话运行上限为 14400 秒；可用 `--timeout` 指定 1–14400 秒。保留后续接收和验收所需的额度。

### 4.4 方式三：手工任务包或已有会话

也可以复制[任务示例](docs/examples/task-v1.json)，替换任务 UUID、仓库、commit、资源哈希、prompt 与验收条件后执行 `validate`、`publish`。示例中的仓库和哈希是虚构说明值，不能原样运行。

若要将任务关联到一个已经存在的原会话，三个绑定参数必须一起提供：

```sh
./bin/taskboard publish task.json \
  --session ORIGINAL_SESSION_UUID \
  --agent codex \
  --workspace /absolute/path/to/demo-api
```

使用真实的 provider 会话 UUID，不是 Issue 编号或任务 UUID。若之前漏绑，可在同一本地看板配置中用完全相同的任务包重跑上述命令，复用已有 Issue 并补上绑定。Taskboard 不会猜测或选择“最近一个会话”。

这里的 `--agent` 和 `--workspace` 描述**原会话**；领取者可以选择任务兼容列表中的其他 agent。没有原会话时省略这三个绑定参数，仍可发布任务并人工验收。

### 4.5 修改或取消已发布任务

任务内容会被冻结。直接编辑 Issue 正文，不会修改 Actions 已确认的任务包。需要换目标或资源时，由发布者取消尚未完成的旧任务，再用**新 task ID**发布；已验收的任务直接保留为历史记录：

```sh
./bin/taskboard cancel 42
```

取消是异步请求，会使后续提交失去有效授权，但不能远程强制关闭另一台电脑上已经启动的 agent。必要时请执行者在本机停止进程。

<a id="execute"></a>
## 5. 如何领取并执行任务

### 5.1 推荐：直接领取并运行

先在 Pages 查看完整 prompt、资源、修改范围、兼容 agent 和验收标准，然后执行：

```sh
./bin/taskboard run 42 --agent codex
```

或者使用兼容的 Claude Code：

```sh
./bin/taskboard run 42 --agent claude
```

`run` 会依次：

1. 检查当前 GitHub 账号有任务仓库写权限，所选 agent 与任务兼容。
2. 请求认领，等待 Actions 确认；若你已经持有有效认领，则复用该次认领。
3. 在独立目录中准备固定版本源码和资源，校验哈希、路径和本机仓库允许列表。
4. 请求开始执行，等待确认后运行本机 agent。
5. 在执行时限内运行验收命令、检查修改范围和必要产物。
6. 上传 Release 附件并提交成果 manifest；之后等待发布者验收。

不会修改你正在使用的源码 checkout，也不会自动合并成果。各确认阶段默认最多等待 180 秒，可指定 0–600 秒：

```sh
./bin/taskboard run 42 --agent codex --wait 600
```

出现 `REQUEST_PENDING` 时，先查看 Actions，待确认后重跑**同一条命令**。尚未开始的执行会继续等待原请求；已生成成果的上传/提交重试会复用本地产物。不要通过删除本地状态来“重试”，这会丢失防重复执行的信息。

### 5.2 只领取，暂不执行

```sh
./bin/taskboard claim 42
./bin/taskboard claim 42 --wait 180
```

`claim` 默认立即返回请求已排队，**不会启动 agent**。只有输出 `Claim confirmed.` 或确认后的任务状态明确归属你，才算领取成功。准备执行时再用 `run`。

有效期从 Actions 确认认领时开始，长度为任务执行时限加 600 秒准备时间。认领成功会计入尝试次数，释放或过期不会退回这次计数。

### 5.3 从 Pages 一键唤起本地执行

macOS 在本机初始化和登录完成后，安装一次启动器：

```sh
./bin/taskboard install-launcher
```

之后在正式 Pages 中打开任务详情，选择兼容 agent，点击 **“领取并运行”**。浏览器会唤起本机终端，由 CLI 完成认领确认和执行；首次使用可能需要允许本地应用打开终端。

Linux 或未安装启动器时，点击 **“复制 CLI”**，在已配置 PATH 的终端运行；也可把开头的 `taskboard` 换成本项目的 `./bin/taskboard` 或绝对路径。演示模式不会提供真实领取。

本机配置的仓库和域名必须与页面一致。不需要启动 `localhost` 页面或本地 HTTP 服务。

### 5.4 放回任务池

当前领取者可以释放自己的有效认领：

```sh
./bin/taskboard release 42 --reason "当前无法继续执行"
```

如果 agent 已在运行，先在其终端按 Ctrl+C 停止。CLI 会尽力发送释放请求；如果网络失败，再核对状态并按需执行 `release`。剩余次数足够时任务重新开放，否则进入 `failed`。

### 5.5 高级：手动提交已有成果

通常 `run` 自动完成上传和提交。只有已经准备好、上传完并保留了准确执行关联的 manifest 时才需要：

```sh
./bin/taskboard submit 42 /absolute/path/to/result.json
```

`submit` **不会执行 agent，也不会上传本地文件**。Manifest 必须绑定当前 task ID、revision、digest、attempt ID 和基准 commit，包含所需产物的 HTTPS 地址与哈希。当前下载实现使用任务仓库的 Release 附件；不要使用本机路径或普通源码文件链接替代。

<a id="results"></a>
## 6. 如何查看、验收和接回结果

### 6.1 在 GitHub Pages 查看

任务进入 `submitted` 后，在 Pages 的 **“待验收” → “查看交付”** 中查看摘要、假设、未解决事项、验证记录和产物链接。点击链接可通过 GitHub 下载成果。

默认成果包括：

- `changes.patch`：源码变更补丁。
- `summary.md`：执行摘要、假设、未解决事项及变更文件。
- `verification.json`：验收命令、退出码及输出记录。
- 任务明确要求的其他交付文件。

### 6.2 下载自己的任务结果

发布者在原来的本地配置中运行：

```sh
./bin/taskboard sync
```

`sync` 只获取**当前 GitHub 账号发布的、处于 submitted/accepted 的成果**，验证哈希并登记到 inbox，不调用模型。终端会打印下载目录。

默认目录结构如下；若使用了 `--home` 或 `TASKBOARD_HOME`，则位于对应目录下：

```text
~/.local/share/taskboard/
├── config.json
├── local.json
├── runs/<issue-number>/<attempt-id>/
│   ├── workspace/
│   ├── provider/
│   ├── checks/
│   ├── artifacts/
│   └── result.json
└── inbox/<result-id>/
    ├── artifacts/
    ├── result.json
    └── delivery/          # 请求接续后保存的本地日志
```

### 6.3 接回原 agent 会话

确保受管理的原会话已经停止；对于手工绑定的外部会话，也先确认没有其他终端正在使用同一 session，再执行：

```sh
./bin/taskboard sync --resume
```

它会处理当前账号可投递的已绑定成果，以结果消息恢复**准确的原 session**，让原 agent 检查并继续。这会消耗原账号的 agent 额度；每次接续最多运行 600 秒。

当前 `sync` 是一次性命令，不是后台监听器。它不会自动唤醒离线电脑。受管理的原会话仍活跃、没有绑定、没有额度或会话不可用时，成果仍保留供查看；已完成的投递不会重复发送。

中断产生 `unknown` 投递状态时，工具不会自动重放。先核对原会话是否已收到结果；需要人工继续时，可把下载的产物交给原 CLI。不要清空状态来强行触发第二次投递。

### 6.4 验收通过或拒收

先查看成果，核对原任务及当前代码状态，执行必要检查，再决定接受或拒收。

在对应 **GitHub Issue 的 Actions 状态评论**中复制“**成果编号**”。它是 result UUID，不是 Issue 编号、task ID、attempt ID 或 session ID。

也可以从 `sync` 输出的 `inbox/<result-id>` 下载目录中取得末级的 UUID。

```sh
./bin/taskboard accept 42 --result RESULT_UUID
```

需要返工时给出具体原因：

```sh
./bin/taskboard reject 42 \
  --result RESULT_UUID \
  --reason "缺少零金额的边界测试，请补齐并重新验证"
```

只有任务发布者可以验收、拒收或取消；当前领取者负责提交和释放。拒收后会保留旧成果记录，有剩余尝试次数时任务重新开放。

**`accept` 只记录验收决定，不会应用补丁、合并 PR 或部署代码。** 这些操作仍通过你们既有的源码协作流程完成。看到 `queued for Actions confirmation` 只说明请求已发送，最终状态需等待 Actions 确认和页面刷新。

<a id="reference"></a>
## 7. 状态、时限与常用命令

| 状态 | 含义 | 通常下一步 |
| --- | --- | --- |
| `open` | 待领取 | `run`，或先 `claim` |
| `claimed` | 已确认领取，尚未开始 | 当前领取者执行 `run` |
| `running` | 已确认开始 | 等待本地执行和提交 |
| `submitted` | 已提交，待验收 | 发布者 `sync`，然后 `accept` / `reject` |
| `accepted` | 已验收 | 按项目原流程集成成果 |
| `cancelled` | 发布者已取消 | 如有新需求，另发新任务 |
| `failed` | 尝试次数已耗尽等终止状态 | 查看记录，由发布者决定是否另发新任务 |

页面“进行中”包含 `claimed` 和 `running`。发布后尚未被 controller 确认的 Issue，不会立即出现为可领取任务。有效认领到期后，后续 controller 运行会重新开放或标记失败；迟到的旧执行编号不能覆盖新结果。

| 命令 | 作用 |
| --- | --- |
| `init --repo OWNER/REPO --hostname HOST` | 配置本机看板 |
| `validate FILE` | 离线校验任务 JSON 并打印 SHA-256 摘要 |
| `start --agent AGENT --prompt FILE --workspace PATH` | 启动可自动绑定发布的原会话 |
| `publish FILE` | 发布固定任务包；可附三个原会话绑定参数 |
| `claim ISSUE [--wait SECONDS]` | 只请求领取，默认等待 0 秒 |
| `run ISSUE --agent AGENT [--wait SECONDS]` | 领取、执行、验证、上传和提交；默认每阶段等待 180 秒 |
| `submit ISSUE FILE` | 提交已经上传的结果 manifest |
| `accept ISSUE --result UUID` | 发布者验收当前成果 |
| `reject ISSUE --result UUID --reason TEXT` | 发布者拒收并说明原因 |
| `release ISSUE --reason TEXT` | 当前领取者释放有效认领 |
| `cancel ISSUE` | 发布者取消任务 |
| `sync [--resume]` | 获取自己的成果；可显式接续已绑定会话 |
| `install-launcher` | 安装 macOS 一键入口 |
| `open-url URL` | 由启动器调用，校验并处理 `taskboard://run` 链接 |

表中命令都加上 `./bin/taskboard` 前缀使用。公共参数为 `--repo`、`--hostname`、`--home`；通常初始化后无需重复指定前两个。随时用 `./bin/taskboard COMMAND --help` 核对参数。当前没有 `--watch`、网页直接写 API 的发布方式或自动合并功能。

<a id="troubleshooting"></a>
## 8. 常见问题与恢复

| 现象或错误 | 原因与处理 |
| --- | --- |
| Pages 显示“等待连接仓库”或没有新任务 | 核对实际 Pages 地址、最近一次 controller、`gh-pages/tasks.json` 和 Pages build。演示模式不是实际任务；管理员可手动 Run workflow。 |
| Actions 一直排队 | 核对 `TASKBOARD_RUNNER` 标签、公司 runner 在线情况与组织策略；这不需要另建应用后台。 |
| `GITHUB_403` / `WRITE_PERMISSION_REQUIRED` | 检查 `gh auth status --hostname ...`、SSO 授权和任务仓库原生写权限；名单不能代替写权限。Actions 的错误还需管理员检查工作流权限。 |
| `TASK_PENDING` / `REQUEST_PENDING` | 请求尚未确认。等待工作流，之后重跑同一条命令；不会因此直接重跑已开始的模型。 |
| `CONFIG_CONFLICT` / `LAUNCH_REPOSITORY_MISMATCH` | 命令或页面指向另一个域名/仓库。使用正确配置；另一个看板使用单独的 `--home`。 |
| `REPOSITORY_NOT_ALLOWED` | 用相同看板参数再次 `init --allow-repo OWNER/REPO`；同时确认 gh 账号能访问该仓库。 |
| `INCOMPATIBLE_AGENT` | 使用任务列出的 `codex` 或 `claude`，不会为不兼容选择消耗认领次数。 |
| `TASK_NOT_OPEN` / `NOT_OWNER` / `ATTEMPT_EXPIRED` / `STALE_ATTEMPT` | 核对当前领取者、有效期和尝试次数。旧执行不能冒用新 attempt 提交；不要手工改状态分支。 |
| `RESOURCE_HASH_MISMATCH` / `RESOURCE_COLLISION` / `UNSAFE_PATH` | 核对固定 commit、该版本文件的 hash 和资源目标路径。当前执行目录不支持符号链接，包括构建过程中生成的链接；资源不能覆盖现有文件或指向 `.git`。 |
| `VERIFICATION_FAILED` / `WRITE_SCOPE_VIOLATION` / `OUTPUT_MISSING` | 查看终端给出的本地目录及 `checks/`、`artifacts/verification.json`。失败不会作为成功成果提交；修正任务范围或依赖后再按正常流程重试。 |
| 上传或提交时网络中断 | 保留本地目录，重跑同一 `run` 命令。若处于成果已准备阶段，会复用产物；内容不同的同名 Release 附件不会被覆盖。 |
| `EXECUTION_UNKNOWN` / `LOCAL_BUSY` | 可能已有执行或锁，不能盲目启动第二份。先确认相关本地进程和记录，必要时停止并释放任务，再等待新的有效认领。 |
| `PUBLISH_UNKNOWN` / `TASK_CONFLICT` | 核对 GitHub 中是否已有同一任务；同 ID 同内容可复用，内容变化需新 task ID。不要用删除本地状态的方法绕过不确定写入。 |
| `sync` 显示没有结果 | 确认当前 gh 账号是发布者、成果已由 Actions 确认为 submitted/accepted、配置的是正确看板。 |
| 没有 session 绑定或投递为 `unknown` | 下载结果仍可使用。可用原任务包显式补绑定；未知投递需先核对原会话，不自动重放。 |
| 点击“领取并运行”没有反应 | macOS 先完成初始化和 `install-launcher`，检查系统应用/终端权限；Linux 或未安装时复制 CLI。重新安装到新目录前，只移除原来的 `Taskboard Launcher.app`。 |

<a id="development"></a>
## 9. 开发检查与可选预览

以下步骤仅供开发与验收；正式使用者直接打开 GitHub Pages。

```sh
python3 -m unittest discover -s tests -v
node --test tests/test_site.mjs
python3 -m compileall -q taskboard scripts
```

可选本地预览：

```sh
python3 -m http.server 8080 --bind 127.0.0.1 --directory site
```

打开 [本地开发预览](http://127.0.0.1:8080/)，点击“先体验演示”。示例模式不会实际发布、领取或执行任务，不作为正式数据部署。

<a id="scope"></a>
## 10. 当前范围与文件入口

- 仅提供 `subtask` 委派，不支持完整会话迁移、未提交工作区补丁或任意递归转派。
- 每个成员保留自己的 GitHub 和 agent 登录；原 session 关联只在本机。
- 本地工具检查资源和产物 hash、修改范围、当前执行编号及结果编号。`write_paths` 是提交前检查，不是操作系统级路径 ACL；验收命令使用本机权限执行，应来自已授权的可信任务。
- 程序化执行不会自动准备所有项目依赖，也不保证适配所有旧版 provider CLI。
- 测试使用临时 Git 仓库、模拟 GitHub 边界和模拟 provider 子进程，不消耗真实模型额度。公司 SSO、实际 runner/Pages 发布、两个真实账号执行与计费仍需在内网验证。

| 文件 | 内容 |
| --- | --- |
| [README.en.md](README.en.md) | 完整英文使用手册 |
| [docs/github-only.md](docs/github-only.md) | 部署与恢复机制补充 |
| [resources/publisher-instructions.md](resources/publisher-instructions.md) | 自动加入受管理原会话的委派说明 |
| [task-v1.json](docs/examples/task-v1.json) / [result-v1.json](docs/examples/result-v1.json) | 任务与结果结构示例 |
| [taskboard.yml](.github/workflows/taskboard.yml) | 协调与 Pages 更新工作流 |
| [protocol.py](taskboard/protocol.py) / [state.py](taskboard/state.py) | 任务协议与状态转换 |
| [cli.py](taskboard/cli.py) | 本地 CLI 实现 |

`docs/superpowers/` 保留早期设计记录，其中的独立后台方案已被当前 GitHub-only 实现替代。
