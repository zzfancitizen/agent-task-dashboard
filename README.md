# Agent Relay · Agent Task Board

**中文** | [English](README.en.md)

把需要更多 agent 额度的工作发布成完整委托，让有余量的同事认领、使用自己的 Codex CLI 或 Claude Code 执行，再把结果交还发布者。

**日常查看全部在 GitHub Pages。发布由现有 agent 协助完成；领取从网页下载执行包，解压后运行。无需另部署后台、数据库或本地页面。** 线上组件只有 GitHub Issues、Actions、Pages 和 Release 附件，支持公司 GitHub 域名。

看板沿用公司 GitHub 的 SSO 和原生访问权限，没有额外的参与者名单，也不要求普通成员拥有仓库写权限。成员需要能读取看板仓库、创建 Issue 和评论，并读取任务引用的源代码及资料。Release 写入由 Actions 完成。

## 目录

- [1. 从这里开始](#roles)
- [2. 让现有 agent 发布任务](#publish)
- [3. 从网页领取并执行](#execute)
- [4. 查看结果、接回原会话与验收](#results)
- [5. 公会排行榜](#rankings)
- [6. 管理员：部署一次](#deployment)
- [7. 高级配置与 CLI](#setup)
- [8. 状态、时限与命令速查](#reference)
- [9. 常见问题与恢复](#troubleshooting)
- [10. 开发检查与当前范围](#development)

<a id="roles"></a>
## 1. 从这里开始

| 想做什么 | 操作 |
| --- | --- |
| 查看委托 | 打开管理员提供的 GitHub Pages 地址；只需浏览器 |
| 发布任务 | 点击“发布任务”，配置一次发布工具，然后在现有 Codex / Claude 会话中交给 agent 整理和发布 |
| 接任务 | 打开任务详情，点击领取按钮，选择 Windows / macOS / Linux，下载、解压并运行执行包 |
| 查看交付 | 打开 GitHub 通知或看板中的待验收任务；让原 agent 同步并检查结果 |
| 部署看板 | 管理员按[部署步骤](#deployment)导入仓库、开启 Actions 与 Pages |

一次接力是：**agent 整理委托 → 发布者确认 → 发布 → 领取 → 本地执行 → 提交成果 → 发布者验收**。

羊皮纸卡片的一至三星对应 S / M / L 任务规模，印章对应真实状态。页面是 Actions 异步生成的快照；下载执行包不会立即占用任务，运行工具会重新确认任务和领取归属。

本手册的 `git.company.example`、`team/agent-task-board`、`team/demo-api`、Issue `42`、路径和 UUID 均为示例，使用时替换为实际值。演示模式只展示虚构任务，不提供真实领取。

<a id="publish"></a>
## 2. 让现有 agent 发布任务

### 2.1 首次配置

1. 打开正式 GitHub Pages 看板，点击 **“发布任务”**。
2. 点击 **“下载发布接入包”**，选择操作系统并下载 ZIP。
3. 将 ZIP **完整解压**到一个文件夹；不要直接在压缩包里运行文件。
4. 运行对应的启动文件，按向导完成必要工具、GitHub 登录及 SSO 授权。
5. 选择 Codex 或 Claude Code，并选择要安装发布集成的**源码项目目录**。
6. 按 provider 的提示信任项目 hook，重新进入或恢复原项目会话，使新集成生效。

也可使用页面的 **“复制指引给我的 Agent”**，让已有 CLI agent 完成同样的配置。发布集成会加入项目 skill、简短规则和会话 hook，保留已有规则与设置；每个需要使用集成的源码项目配置一次。

工具安装向导的行为见[首次运行](#first-run)。它不会替你注册 provider 账号或提供额度。配置发布工具不会发布任务。

### 2.2 日常发布：描述工作即可

在已经配置的 Codex / Claude 会话里正常工作，也可以直接说：

> 帮我把这部分测试工作整理成可交接的委托。请准备完整 prompt、代码版本、资源、修改范围和验收标准，先给我看，再由我决定是否发布到任务看板。

Skill 引导 agent 判断工作是否适合独立交接。适合时，它会准备一份本地提案，包括：

| 内容 | 作用 |
| --- | --- |
| 目标、背景与完整 prompt | 领取者无需知道之前的对话，也能开始执行 |
| 源码仓库和固定 commit | 明确执行基准，避免不同电脑使用不同代码 |
| Resources 及内容哈希 | 给出必要资料，并检查实际下载内容 |
| 允许修改范围 | 明确本次委托可以改动的目录和文件 |
| 验收要求、命令和产物 | 明确怎样判断完成，以及需要交回什么 |
| 本机原会话关联 | 结果返回后能找到准确的原 Codex / Claude 会话 |

Agent 应先展示具体任务和交接理由，再询问是否发布。**只有收到你对这份提案的明确同意后，才执行发布。** 任务复杂、hook 被触发、安装了 skill，都不等于同意发布；你也可以选择继续本地处理。

Agent 自动收集仓库、commit、资源哈希和准确会话关联，减少手工复制。目标、上下文和验收仍需由 agent 根据当前工作整理；程序不会凭空推断你的意图。影响任务的未提交改动必须先处理，不能静默遗漏；待交接的 commit 和资料还必须能从远端获取。

发布成功会返回 GitHub Issue 链接。Actions 校验后刷新 Pages；页面尚未出现时，可以先在 Issue 和 Actions 查看进度。网络不确定时应重试**同一提案**，保留本地状态，避免重复创建任务。

### 2.3 会话和上下文如何交接

Issue 中发布的是独立任务包。原 session ID、登录凭据和完整聊天记录保留在发布者本机，不进入 Pages 或执行包。Hook 记录 provider 给出的准确 session 与目录，不猜测“最近一个会话”。

领取者运行一个新的、独立的 agent 会话；成果回到发布者后，由原会话读取结果继续工作。因此 Codex 发出的任务可以由兼容的 Claude Code 执行，但这不等于把完整 Codex 会话迁移到 Claude。

### 2.4 修改或取消任务

Actions 确认后的任务包被冻结。编辑 Issue 正文不会改变已确认的 prompt、commit 或资源。需要变更时，让发布 agent 取消旧任务，并用新 task ID 发布新委托；高级命令为：

```sh
./bin/taskboard cancel 42
```

取消会使后续提交失去授权，但不能远程关闭另一台电脑上已经运行的 agent。已验收任务保留为历史记录。

<a id="execute"></a>
## 3. 从网页领取并执行

### 3.1 下载并打开执行包

1. 在 Pages 打开一个待领取任务，阅读目标、资源、修改范围和验收条件。
2. 点击领取按钮，选择 **Windows / macOS / Linux**，下载任务 ZIP。
3. 完整解压 ZIP，保留同一文件夹中的所有文件。
4. 运行对应启动文件：

| 系统 | 启动文件 | 打开方式 |
| --- | --- | --- |
| Windows | `Start-Taskboard.cmd` | 双击，打开命令窗口 |
| macOS | `Start-Taskboard.command` | 双击，在终端中运行 |
| Linux | `Start-Taskboard.sh` | 在文件管理器中选择作为程序运行；具体选项取决于桌面环境 |

执行包包含启动脚本、Python runtime 源码包和任务定位信息，不包含 GitHub token 或 agent 登录。它是可检查的脚本 ZIP，不是签名安装程序或独立 `.exe`。macOS / Windows 可能显示下载来源或运行确认；Linux 可能需要在文件属性中允许作为程序运行。按公司设备策略处理，工具不会关闭这些系统保护。

<a id="first-run"></a>
### 3.2 首次运行：向导引导安装和登录

启动器先检查 **Python 3.11+**。缺少 Python 时，它提供官方安装页面及重试/退出选项。安装完成后返回向导重试，必要时重新打开启动文件。

随后向导检查 Git、GitHub CLI（`gh`）和所选 Codex / Claude Code。缺少工具时，会提供安装引导：有可用包管理器时先展示并征求安装同意，否则打开官方安装说明。工具不会静默安装软件；公司设备需要管理员批准时，按既有方式联系管理员。

向导引导 GitHub 浏览器登录与公司 SSO 授权，并引导所选 agent 的原生登录。登录时请确认 GitHub 账号、provider 账号和将要消耗的额度。已有可用登录会被复用。完成一次后，后续任务通常只需选择 agent 并执行。

向导根据执行包配置看板，并展示本次任务需要的源码和资料仓库。能够登录 SSO 不代表自动获得每个源码仓库的访问权；缺少访问权时，需按公司流程开通。

### 3.3 选择 agent，等待结果

选择任务支持的 **Codex** 或 **Claude Code**，查看仓库和验收说明并确认执行后，工具会：

1. 从 GitHub 读取已确认任务，核对 revision 和摘要；过期执行包会被拒绝。
2. 请求领取，等待 Actions 确认当前账号拥有本次执行。
3. 在独立目录准备固定版本的源码和资料，校验哈希。
4. 请求开始执行，确认后运行你本机的 agent，使用你的 provider 账号额度。
5. 执行验收命令，检查允许修改范围和必要交付文件。
6. 把压缩成果通过 Issue 评论提交给 Actions；Actions 验证后保存到 Release，确认提交并刷新 Pages。
7. 显示提交结果及 GitHub 入口。发布者收到新的结果评论通知，随后验收。

下载并不等于领取成功。若另一位同事先获得确认，工具会提示当前状态，不会继续启动重复执行。Actions 排队时，向导会等待或给出重试选项；只有确认提交后才显示已提交。

本地工具不会修改你原来打开的源码目录，不会自动合并或部署。源项目自身依赖仍需按任务说明准备，通用工具安装向导不负责猜测所有项目环境。

### 3.4 大文件、中断和重试

当前适合代码、补丁、文档和测试报告：**一个压缩成果 ZIP 最大 1 MiB**；每个未压缩文件最大 20 MiB，未压缩总量最大 100 MiB。超限会在发送评论前停止，文件保留在本机。任务应避免要求大体积二进制产物；不能为了过限而删除必要验收证据。

如果网络或 Actions 暂时不可用，保留解压文件夹和本地状态，按向导重试或重新打开同一执行包。已准备的成果会被复用，待确认请求不会仅因超时再次运行 agent。出现“执行结果不明”时，先检查本地进程与记录，工具不会盲目启动第二份执行。

需要中止时在运行终端按 Ctrl+C。工具会尽力请求释放任务；网络失败时核对 Issue 状态，再通过 agent 或[高级命令](#cli-execute)释放。有效期和尝试次数见[状态说明](#reference)。

<a id="results"></a>
## 4. 查看结果、接回原会话与验收

### 4.1 GitHub 通知和网页交付

Actions 确认成果后，会另发一条结果评论，提及发布者和执行者，给出成果编号、下载链接和待验收说明。通知是否显示邮件或推送，由你们的 GitHub 通知设置决定。

在 Pages 的待验收任务中查看交付摘要、假设、未解决事项、验证记录和产物链接。默认成果包括 `changes.patch`、`summary.md`、`verification.json`，以及任务要求的其他文件。

**提交成功表示已交付，验收通过才表示完成。** 补丁应用、合并 PR 和部署继续使用项目原有流程。

### 4.2 在原会话中取回结果

收到通知后，回到发布任务时的 Codex / Claude 会话，例如告诉它：

> 请同步任务看板的交付结果，检查这个委托的改动和验证证据，再继续当前工作。先告诉我验收结论。

发布集成提供准确的本地任务与会话关联。Agent 可以运行 `sync`，下载并校验你的成果，再在当前会话中读取。Hook 只提示已同步到本地、属于当前会话的新结果，不进行远端轮询或额外模型调用。

手动下载也可使用以下命令，前提是已按[高级配置](#setup)准备 CLI，并与发布集成使用相同的 `--home`。下载向导用户可直接让原 agent 使用 hook 提供的完整工具入口，不必自行找安装目录：

```sh
./bin/taskboard sync
```

它只获取当前 GitHub 账号发布、且已确认处于 `submitted` / `accepted` 的成果，打印本地 inbox 路径，不调用模型。

如果原会话已经退出或确定空闲，可以显式恢复准确的原会话：

```sh
./bin/taskboard sync --resume
```

`--resume` 会消耗发布者的 provider 额度，每次接续最多运行 600 秒。不要在另一终端仍使用同一个 session 时调用。工具不使用 `--last`，不会唤醒离线电脑，也没有常驻后台监听器。没有会话绑定或原会话不可用时，成果仍可下载并手动交给 agent。

投递中断而结果不明时，保留 `unknown` 状态并先核对原会话；不会自动重复投递或通过删除本地状态强行重放。

### 4.3 验收或返工

让发布 agent 对照原任务、当前代码和验证证据检查成果，并按你的决定提交验收或拒收。只有发布者能验收、拒收或取消任务；只有当前执行者能提交和释放。这里保留的是任务归属规则，不需要额外的参与者权限配置。

高级命令使用 GitHub 结果评论或本地 inbox 中的**成果 UUID**：

```sh
./bin/taskboard accept 42 --result RESULT_UUID
./bin/taskboard reject 42 --result RESULT_UUID --reason "缺少零金额的边界测试，请补齐并重新验证"
```

成果 UUID 不是 Issue 编号、task ID、attempt ID 或 session ID。验收请求也需等待 Actions 确认。拒收会保留旧成果，剩余尝试次数允许时重新开放任务。`accept` 不会应用补丁、合并或部署代码。

<a id="rankings"></a>
## 5. 公会排行榜

看板显示当前已确认任务范围内的两个前十榜单：

| 榜单 | 计数规则 |
| --- | --- |
| 发布最多 | 按不同 task ID 计算发布量，排除已取消任务 |
| 完成最多 | 仅计入已验收的不同 task ID，归属被验收那次执行的领取者 |

重复提交、重试和多条评论不会增加计数；待验收成果不计入完成量。用户名不区分大小写，数量相同时使用稳定排序。榜单随 Actions 生成的任务快照更新，不代表 token 消耗、任务难度或虚构奖励。

<a id="deployment"></a>
## 6. 管理员：部署一次

### 6.1 准备仓库和 runner

1. 将项目完整导入公司 GitHub 仓库的**默认分支**，保留 `.github/`、`launchers/` 和 `integrations/`。
2. 启用 Issues、Actions 和 Pages，并通过公司 GitHub 原生策略允许成员读取仓库、创建 Issue 和评论。
3. 允许工作流声明的 `contents: write`、`issues: write`、`pages: write`。这些是 Actions 的权限，不是普通参与者必须拥有的仓库角色。
4. 配置可运行工作流的 runner。GitHub.com 默认 `ubuntu-latest`；GitHub Enterprise Server 默认公司已有的 `self-hosted` runner。

Runner 需要 Python 3.11+、GitHub CLI；`Check task board` 工作流还需要 Node.js 18+。不需要安装 Python 或前端第三方包。内网不能使用外部 Actions 时，将工作流中的 `actions/checkout@v4` 换为公司镜像版本。

在 **Settings → Secrets and variables → Actions → Variables** 配置：

| 变量 | 设置 |
| --- | --- |
| `TASKBOARD_RUNNER` | 可选，公司 runner 标签，例如 `corp-linux`，不带额外引号 |
| `TASKBOARD_PAGES_ENABLED` | 首次生成分支后设为字符串 `true`，请求 Pages 构建 |

不配置成员名单，不需要给成员仓库 write / maintain / admin 角色，也不需要上传成员的 GitHub token、agent 登录文件或模型 API key。Actions 使用 GitHub 自动提供的凭据上传成果和发布页面。

### 6.2 启用 Pages

1. 进入 **Actions → Task board controller → Run workflow**，首次运行。
2. 成功后生成 `taskboard-state`（确认状态）和 `gh-pages`（页面、`tasks.json`、下载工具）分支。
3. 在 **Settings → Pages → Build and deployment** 选择 **Deploy from a branch**，来源为 **`gh-pages` / `/ (root)`**。本方案使用公开站点，不设置应用自己的登录入口。
4. 将 `TASKBOARD_PAGES_ENABLED` 设为 `true`，再运行一次 controller。
5. 把 GitHub 显示的实际 Pages 地址提供给成员。企业域名和站点路径以公司环境为准。

任务与命令评论触发更新；成果分片评论先保留在 Issue，最终提交命令触发处理。工作流也支持手动运行，以及每小时第 17、47 分钟的恢复扫描；调度不保证实时。

公司禁用 Pages build API 时，已生成的 `gh-pages` 可交给公司现有 Pages 发布流程。状态分支由 controller 唯一写入，不要手工修改 `taskboard-state/state.json`。部署机制详见 [GitHub-only 说明](docs/github-only.md)。

<a id="setup"></a>
## 7. 高级配置与 CLI

网页下载向导会完成通常需要的配置。本节用于已有开发环境、脚本集成和故障恢复；普通领取者无需手工输入这些命令。本文的 `./bin/taskboard` 示例在看板项目源码根目录使用，不是在下载 ZIP 或任务源码的目录使用。已安装 skill 的 `context` 返回完整 `taskboard_argv`，其中包含 runtime 路径和正确的 `--home`，agent 应直接使用它。

### 7.1 手动安装和看板配置

获取源码并在本项目根目录运行。macOS / Linux 示例使用 `./bin/taskboard`；Windows 可在同一目录使用 `python -m taskboard` 替代。无需 `pip install` 或 `npm install`。

```sh
gh auth login --hostname git.company.example --git-protocol https --web
gh repo clone https://git.company.example/team/agent-task-board
cd agent-task-board
./bin/taskboard init \
  --repo team/agent-task-board \
  --hostname git.company.example \
  --allow-repo team/demo-api \
  --agent codex
```

`--repo` 是看板仓库，格式 `owner/repo`；`--hostname` 是不含协议、端口或路径的 GitHub 域名，默认 `github.com`。`--allow-repo` 可重复，允许读取或执行指定源码/资料仓库，任务仓库自动加入。相同看板再次 `init` 可以追加允许仓库；原生 GitHub 访问权仍由 GitHub 控制。

手工 CLI 默认状态目录为 `~/.local/share/taskboard`，可用 `TASKBOARD_HOME` 或公共参数 `--home PATH` 指定。下载向导按平台使用用户本地数据目录，按看板分开保存配置，避免不同看板混用；路径见[部署说明](docs/github-only.md#下载包与首次引导)。一个状态目录绑定一个看板；手工管理多个看板时使用不同目录，并在安装集成与后续 CLI 操作中使用相同目录。`--agent` 设置默认 provider，`run` 仍需显式选择。

### 7.2 安装发布 skill / rule / hook

```sh
./bin/taskboard install-integration --provider codex --project /absolute/path/to/demo-api
./bin/taskboard install-integration --provider claude --project /absolute/path/to/demo-api
```

只执行需要的 provider 对应命令。Codex 使用项目 `.agents/skills/taskboard-publish/`、`AGENTS.md` 和 `.codex/hooks.json`；Claude 使用 `.claude/skills/taskboard-publish/`、`CLAUDE.md` 和 `.claude/settings.json`。安装器保留其他设置，添加有边界标记的规则及 `SessionStart` / `UserPromptSubmit` hook。安装后按 provider 规则重新载入并信任 hook。

Hook 通过 stdin 获取真实 session 与 cwd，写入本地关联，并把提案工具入口交给 agent。它不上传聊天记录，不扫描任意最近会话，不自行发布任务，也不定时调用模型。Hook 生效后的第一次用户消息也能提供准确关联；如果 provider 尚未重载配置，则恢复同一原会话。

### 7.3 agent 提案与明确发布

下面是给 agent 或脚本集成者的低层接口，日常用户无需填写 JSON。先在源码目录之外写 UTF-8 目标文件：

```json
{
  "goal": "补齐折扣计算的边界测试",
  "context": "已有金额和折扣类型见代码；保持对外接口不变。",
  "acceptance": ["覆盖零金额、零折扣和非法输入", "现有测试继续通过"],
  "delegation_reason": "测试部分可独立执行，代码与验收条件已固定"
}
```

```sh
./bin/taskboard propose \
  --workspace /absolute/path/to/demo-api \
  --goal-file /absolute/path/to/proposal-goal.json \
  --title "补齐折扣边界测试" \
  --provider codex --session ORIGINAL_SESSION_UUID \
  --resource docs/discount-rules.md \
  --write-path tests \
  --command-json '["python3", "-m", "unittest"]' \
  --size M --category code
```

资源、修改范围和验收命令参数可重复；验收命令是 argv 数组，不是 shell 字符串。使用 provider/hook 给出的准确 session。目标 JSON 还可提供 `required_outputs`；刚安装集成时，skill helper 的 `context --callback UUID` 会给出可原样加入的 `excluded_paths`，用于排除未经改动的安装器管理文件。这不能排除源码改动。日常 skill 会处理这些字段；直接调用 `propose` 时需确保源码检查可通过。

提案仅保存本地，展示内容并得到用户明确同意后再运行：

```sh
./bin/taskboard publish-proposal PROPOSAL_UUID --approved
```

没有 `--approved` 会拒绝发布；提案被改动后摘要不一致也会拒绝。同一提案重试复用其任务标识。集成 skill 的辅助脚本可从本地 hook callback 取得准确会话，避免 agent 手工找 UUID。

### 7.4 手工任务包和受管理会话

高级入口仍支持从 Pages 手工表单导出 JSON，或按 [task-v1.json](docs/examples/task-v1.json)准备固定任务包：

```sh
./bin/taskboard validate task.json
./bin/taskboard publish task.json
./bin/taskboard publish task.json --session ORIGINAL_SESSION_UUID --agent codex --workspace /absolute/path/to/demo-api
```

表单导出不会创建 Issue。`publish` 是显式发布命令；原会话三个绑定参数必须一起给出，没有会话则全部省略。漏绑时，可在相同本地配置中用完全相同的任务包重跑并补上绑定。

任务包含独立 prompt、同一 GitHub 主机上的 HTTPS 源码仓库、完整 40 位 commit SHA、资源路径及该 commit 内容的 SHA-256、修改范围、执行要求和验收。只支持已提交输入，`source.workspace_patch` 为 `null`。资源目标不能覆盖 checkout 已有文件。

任务及请求 JSON 最大 48 KiB；执行时限 1–14400 秒，最多尝试 1–5 次。自动产物 `changes.patch`、`summary.md`、`verification.json` 不要求 agent 在源码中再创建；额外必要产物应位于工作目录并符合 `write_paths`。

也保留受管理的原会话入口，它从实际 provider 事件捕获 session：

```sh
./bin/taskboard start --agent codex --prompt work.md --workspace /absolute/path/to/demo-api
```

这是高级非交互运行方式，默认超时 14400 秒，可用 `--timeout` 调整。日常发布推荐已有交互会话的 skill 集成。`start` 不保证发布，也不取消发布前确认要求。

<a id="cli-execute"></a>
### 7.5 领取、执行和恢复命令

```sh
./bin/taskboard run 42 --agent codex
./bin/taskboard run 42 --agent claude --wait 600
./bin/taskboard claim 42 --wait 180
./bin/taskboard release 42 --reason "当前无法继续执行"
```

`run` 自动领取、执行、校验和提交；每个确认阶段默认等待 180 秒，`--wait` 范围 0–600 秒。`claim` 默认等待 0 秒，只排队，不启动 agent。看到 `Claim confirmed.` 或确认状态归属你才算领取成功。

`REQUEST_PENDING` 时等待 Actions 并重跑同一命令。工具保留原请求、工作目录和已生成成果，防止仅因确认延迟重复执行。不要删本地状态来重试。

旧 HTTPS manifest 提交入口仍可用于已有合法 Release 成果：

```sh
./bin/taskboard submit 42 /absolute/path/to/result.json
```

该命令不执行 agent，也不上传本地文件。Manifest 须匹配当前任务、revision、摘要、attempt 与基准 commit，并提供任务仓库 Release 产物的 HTTPS 地址和哈希。普通执行使用评论传输桥，无需参与者拥有 Release 写权限。

旧 macOS URL handler `install-launcher` / `open-url` 为兼容入口保留；网页 ZIP 流程不依赖注册 URL 协议或本地服务。

<a id="reference"></a>
## 8. 状态、时限与命令速查

| 状态 | 含义 | 下一步 |
| --- | --- | --- |
| `open` | 待领取 | 下载执行包并运行 |
| `claimed` | 已确认领取 | 当前领取者准备并开始执行 |
| `running` | 已确认开始 | 等待本机执行与提交 |
| `submitted` | 已提交，待验收 | 发布者同步、检查、验收或拒收 |
| `accepted` | 已验收，计入完成榜 | 按项目原流程集成成果 |
| `cancelled` | 发布者已取消 | 有新需求时另发任务 |
| `failed` | 尝试次数已耗尽等终止状态 | 查看记录，决定是否另发任务 |

认领有效期从 Actions 确认开始，为任务运行时限加 600 秒准备时间。确认认领即消耗一次尝试；释放或过期不退还次数。过期在后续 controller 扫描时处理，旧 attempt 不能覆盖新结果。页面“进行中”包括 `claimed` 和 `running`。

| 命令 | 作用 |
| --- | --- |
| `init --repo OWNER/REPO --hostname HOST` | 手动配置本机看板 |
| `install-integration --provider AGENT --project PATH` | 安装已有会话使用的发布集成 |
| `propose ...` | 生成本地完整提案 |
| `publish-proposal ID --approved` | 在用户同意后发布该提案 |
| `validate FILE` / `publish FILE` | 校验或显式发布手工任务包 |
| `start --agent AGENT --prompt FILE --workspace PATH` | 高级受管理原会话 |
| `claim ISSUE [--wait SECONDS]` | 只请求领取 |
| `run ISSUE --agent AGENT [--wait SECONDS]` | 领取、执行、验证、自动提交 |
| `submit ISSUE FILE` | 提交已上传的 HTTPS 结果 manifest |
| `sync [--resume]` | 获取自己的成果；可显式恢复准确原会话 |
| `accept ISSUE --result UUID` | 发布者验收 |
| `reject ISSUE --result UUID --reason TEXT` | 发布者拒收 |
| `release ISSUE --reason TEXT` | 当前领取者释放 |
| `cancel ISSUE` | 发布者取消 |

加上 `./bin/taskboard` 前缀使用；公共参数 `--repo`、`--hostname`、`--home` 通常在配置后无需重复。以 `COMMAND --help` 为参数准绳。当前没有 `--watch`、浏览器存储 token 的写入流程或自动合并。

<a id="troubleshooting"></a>
## 9. 常见问题与恢复

| 现象 | 处理 |
| --- | --- |
| ZIP 双击后只看到文件列表 | 先完整解压，再运行对应 `Start-Taskboard` 文件，保留其他文件在同一目录 |
| 系统提示无法运行下载脚本 | 按公司设备策略处理下载来源/运行确认；Linux 检查文件属性中的运行权限，不关闭系统保护 |
| 提示缺少工具，安装后仍找不到 | 返回向导重试；PATH 尚未更新时重新打开启动文件。公司受管设备可能需要管理员安装 |
| GitHub 登录失败或 `GITHUB_403` | 检查登录主机、账号、SSO、Issue/评论及源码读取权限；Actions 错误还需检查工作流自己的写权限 |
| Pages 没有新任务或结果 | 检查 controller、`gh-pages/tasks.json` 和 Pages build；管理员可手动运行 controller。演示不是实际数据 |
| Actions 一直排队 | 检查 `TASKBOARD_RUNNER` 标签、runner 在线情况与组织策略，等待确认，不重复创建任务 |
| 执行包已过期或任务已被领取 | 刷新正式看板、查看当前状态。新下载不会抢占别人的有效领取 |
| `TASK_PENDING` / `REQUEST_PENDING` | 保留本地目录，等待 Actions，重试同一执行包或命令 |
| 成果超过 1 MiB 压缩限制 | 查看保留的本地产物，按任务要求减少不必要的大文件；必要交付本身超限时由发布者调整任务 |
| `CONFIG_CONFLICT` / `LAUNCH_REPOSITORY_MISMATCH` | 核对看板域名与仓库；手工管理多个看板时分开使用 `--home` |
| `REPOSITORY_NOT_ALLOWED` | 向导会根据确认任务配置；手工 CLI 用 `init --allow-repo OWNER/REPO` 追加，并确认 GitHub 原生读取权限 |
| `INCOMPATIBLE_AGENT` | 选择任务支持的 Codex / Claude，不兼容选择不会消耗认领次数 |
| `NOT_OWNER` / `ATTEMPT_EXPIRED` / `STALE_ATTEMPT` | 查看当前归属、有效期和尝试次数；旧执行不能冒用新编号提交 |
| `RESOURCE_HASH_MISMATCH` / `RESOURCE_COLLISION` / `UNSAFE_PATH` | 核对固定 commit 和资源内容，资源不能覆盖已有文件或指向 `.git`；工作目录当前不支持符号链接 |
| `VERIFICATION_FAILED` / `WRITE_SCOPE_VIOLATION` / `OUTPUT_MISSING` | 查看终端给出的 `checks/` 和 `artifacts/verification.json`；失败不会提交为成功成果 |
| 上传或提交中断 | 保留目录并重试同一执行包/命令；使用相同成果和请求恢复，不能覆盖同名的不同内容 |
| `EXECUTION_UNKNOWN` / `LOCAL_BUSY` | 先核对已有进程和本地记录，避免启动第二份执行；需要时停止并释放，再等待新认领 |
| `PUBLISH_UNKNOWN` / `TASK_CONFLICT` | 核对 Issue 是否已创建，同提案/同任务内容可重试；变更内容使用新 task ID |
| 原 agent 没有显示发布 skill | 确认安装的是当前项目与 provider，按 provider 要求信任并重载 hook，恢复原会话 |
| 原会话没有自动收到结果 | GitHub 通知不会自动启动本机 agent。回原会话要求同步；hook 只提示本地已有的新结果 |
| `sync` 无结果或没有 session 绑定 | 确认当前账号是发布者、成果已提交、看板正确；没有绑定仍可下载手动使用 |
| 接续为 `unknown` | 先核对原会话是否收到结果，不清空状态强制再次投递 |

<a id="development"></a>
## 10. 开发检查与当前范围

以下仅供开发与验收；正式用户直接打开 GitHub Pages。

```sh
python3 -m unittest discover -s tests -v
node --test tests/test_*.mjs
python3 -m compileall -q taskboard scripts
```

可选本地视觉预览：

```sh
python3 -m http.server 8080 --bind 127.0.0.1 --directory site
```

打开 [本地开发预览](http://127.0.0.1:8080/) 并明确进入演示模式。发布下载资源由 controller 的站点构建生成；只启动源码 `site/` 的 HTTP 预览不等同于完整部署产物。

- 只支持独立 `subtask` 委派，不支持完整会话迁移、未提交工作区补丁或任意递归转派。
- 本地工具校验哈希、路径、修改范围和执行关联；`write_paths` 是提交前检查，不是操作系统 ACL。任务代码和验收命令使用本机权限，应来自你已授权的任务。
- 成员保留各自 GitHub 与 provider 登录；Pages、任务包和下载脚本不携带这些凭据或原会话日志。
- Python/Node 测试以临时 Git 仓库和模拟 GitHub/provider 边界验证流程，不消耗真实模型额度。Windows / Linux 原生桌面安装与双击流程、公司 SSO、实际 runner/Pages 和两账号计费仍需在目标环境联调；不声称这些已完成现场验证。

| 文件 | 内容 |
| --- | --- |
| [README.en.md](README.en.md) | 完整英文手册 |
| [docs/github-only.md](docs/github-only.md) | 部署架构、评论传输和恢复机制 |
| [发布 skill](integrations/taskboard-publish/SKILL.md) | 已有 agent 会话的提案与发布流程 |
| [委派说明](resources/publisher-instructions.md) | 受管理原会话使用的说明 |
| [任务示例](docs/examples/task-v1.json) / [成果示例](docs/examples/result-v1.json) | 协议结构示例 |
| [工作流](.github/workflows/taskboard.yml) | 协调、成果保存和 Pages 更新 |
| [CLI](taskboard/cli.py) / [向导](taskboard/wizard.py) | 本地执行入口 |
| [协议](taskboard/protocol.py) / [状态](taskboard/state.py) / [传输](taskboard/transfers.py) | 校验、状态转换和成果桥 |

`docs/superpowers/` 保留早期设计记录；其中的独立后台方案已被当前 GitHub-only 实现替代。
