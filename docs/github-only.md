# GitHub-only 部署与运行

[完整中文使用手册](../README.md) | [Complete English manual](../README.en.md)

当前实现采用 GitHub Issues、Actions、Pages 和 Release 附件。Issues 保存任务与请求，Actions 顺序处理，`taskboard-state` 分支保存确认状态，`gh-pages` 分支提供羊皮纸看板和下载工具。本地程序调用成员已有的 Codex CLI / Claude Code；没有额外托管的应用服务或数据库，也不要求本地页面。

## 组件与完整流程

| 组件 | 职责 |
| --- | --- |
| GitHub Pages | 显示确认后的任务、交付和榜单，生成对应系统的下载 ZIP |
| 项目 skill / rule / hook | 在现有发布者会话中整理并复核完整交接提案、取得明确发布同意，并绑定准确原会话 |
| 本地执行包 | 引导工具安装和登录，确认领取，在独立目录运行成员自己的 agent，收集成果 |
| GitHub Issues / 评论 | 保存不可变任务内容、领取/验收请求和成果传输分片 |
| Actions controller | 校验身份与任务状态，顺序处理请求，以工作流凭据保存成果，刷新状态和页面 |
| GitHub Releases | 保存确认成果的下载附件 |

发布者的 agent 先在本地整理目标、固定源码和资源、范围、运行前提及验收条件，复核完整执行 prompt 能否支持独立工作。发布者明确同意提案后，工具创建 Issue。Actions 确认后，委托出现在 Pages。

领取者在 Pages 选择操作系统，下载 ZIP，解压并打开启动文件。首次安装/登录引导完成后选择 Codex 或 Claude；本地程序重新读取任务，取得 Actions 确认的当前执行编号后才开始工作。执行结果通过评论交给 Actions，Actions 保存附件并发出结果通知。发布者在原会话中同步、检查和验收；已验收任务计入完成榜。

下载并不预占任务，网页快照不是领取凭据，执行包也不包含可用于登录的 token。浏览器与本地工具之间不需要 HTTP 服务。

## 原生访问权限

没有应用层参与者名单或额外角色授予步骤。参与者使用公司 GitHub SSO 和已有 GitHub 账号，需要看板仓库读取、创建 Issue 和评论的能力，以及任务源码/资料仓库的读取权限。普通参与者不需要 `write`、`maintain` 或 `admin` 仓库角色。

Release 上传与状态分支写入使用 Actions 自己的权限。只有 publisher 能 accept / reject / cancel，只有当前有效 attempt 的 actor 能 start / submit / release；这些任务归属约束仍然保留。身份来自 GitHub API 返回的实际 Issue/comment 作者，不从请求 JSON 采信用户名。

SSO 授权和仓库读取权由公司 GitHub 决定，应用不替成员授予源码访问权。Pages 按本方案作为公开静态站点发布，不增加应用登录或用户私有任务筛选。原 session ID、凭据和完整会话日志保留在发布者本机，不写入任务或公开快照。

## 管理员部署

公司 GitHub 需启用 Issues、Actions 和 Pages。GitHub.com 默认使用 `ubuntu-latest`；GitHub Enterprise Server 默认使用公司已有 `self-hosted` runner。通过仓库变量 `TASKBOARD_RUNNER` 可指定 runner 标签。

Runner 需要 Python 3.11+ 和 GitHub CLI。`Check task board` 还需要 Node.js 18+；无应用第三方包安装步骤。工作流使用 `actions/checkout@v4`，内网不允许外部 Actions 时使用公司镜像版本。

1. 完整导入项目到仓库默认分支，包括 `.github/`、`launchers/`、`integrations/`。
2. 启用 Issues / Actions / Pages，并按公司 GitHub 原生设置开放成员 Issue/评论操作。
3. 允许工作流 `contents: write`、`issues: write`、`pages: write`；不需要成员个人 token 或模型 API key。
4. 手动运行 **Actions → Task board controller → Run workflow**，生成 `taskboard-state` 和 `gh-pages`。
5. 在 **Settings → Pages** 选择 **Deploy from a branch**，来源为 **`gh-pages` / `/ (root)`**。
6. 将仓库变量 `TASKBOARD_PAGES_ENABLED` 设为字符串 `true`，再次运行 controller，显式请求 Pages build。
7. 使用 GitHub 显示的实际 Pages 地址。内网域名与仓库路径由当前 Actions 环境提供，不需改源码中的固定地址。

任务仓库不再配置 `.github/taskboard.json` 成员名单。公司若禁用 Pages build API，可让既有 Pages 发布流程读取已生成的 `gh-pages`；无需另建应用后台。实际 runner、权限和 Pages build 需在目标 GitHub 环境核验。

## 发布集成与准确会话关联

从 Pages 下载发布工具配置包，解压运行，选择 provider 与源码项目目录。已有开发环境也可执行：

```sh
./bin/taskboard init --repo team/agent-task-board --hostname git.company.example --allow-repo team/demo-api
./bin/taskboard install-integration --provider codex --project /absolute/project/path
```

示例值需要替换；使用 Claude 时将 provider 改为 `claude`。Codex 集成安装到 `.agents/skills/taskboard-publish/`，在 `AGENTS.md` 和 `.codex/hooks.json` 添加受标记管理的规则与 hook；Claude 使用 `.claude/skills/taskboard-publish/`、`CLAUDE.md` 和 `.claude/settings.json`。已有无关配置保留。按 provider 的 hook 信任与加载要求使其生效。

升级已安装的集成时，从当前 Pages 重新下载发布配置包，在同一项目、provider 上重跑安装；源码用户从更新后的代码重跑 `install-integration`。然后重载 hook 并恢复原会话。网页更新不会自动替换项目中已经安装的 skill 和 runtime。

`SessionStart` / `UserPromptSubmit` hook 从真实事件获得 session ID 与 cwd，在本地保存 callback，供 agent 的提案辅助脚本读取。Hook 不查找“最新会话”，不读取 transcript，不做网络轮询，也不调用模型。

Agent 写出目标 JSON 并调用 `prepare`，得到包含固定 commit、资源内容哈希、prompt、`handoff` 和验收的具体本地提案，以及由任务派生的 `execution_prompt`。Agent 先通读这份完整执行输入，修正隐藏依赖并重新准备，再向用户展示提案、询问是否发布；用户同意后调用 `publish ... --approved`。没有同意标记不能通过提案发布接口；安装 skill 或复杂度判断都不是发布授权。已有 `publish FILE` 为显式高级操作保留。

源输入需要已提交且能从远端获取。涉及任务的脏工作区不能静默遗漏；工具不会自动打包未提交工作区或完整原会话。完整任务包和本机原会话绑定用途不同：领取者执行独立任务，发布者稍后用成果恢复自己的工作。

## Prompt 闭包与新任务接纳

任务发布类似向未见过父会话的 sub-agent 分派任务。闭包由完整执行 prompt、固定源码/resources 和已声明的执行前提组成：执行者能知道第一步做什么、从哪里取得必要输入、必须保留哪些决策和行为、怎样产出结果及验证完成。原 session ID 仅服务于成果回送，不为领取者提供缺失上下文。

目标 JSON 的必填内容为 `goal`、`context`、`delegation_reason`、非空 `acceptance` 数组，以及 `handoff`。示例见[目标文件](examples/proposal-goal.json)和[任务文件](examples/task-v1.json)。`handoff` 的键固定为：

| 字段 | 约束与用途 |
| --- | --- |
| `non_goals`、`constraints`、`assumptions` | 字符串数组，可明确为空；说明不做的工作、既定决策/不变量及已有依据的假设 |
| `environment` | 非空字符串，说明工具/版本、依赖准备和外部访问前提，不包含凭据 |
| `stop_conditions` | 至少一条非空字符串，说明何时停止并报告缺失输入或其他阻塞 |
| `review.first_step` | 非空字符串，说明使用已声明输入的第一个具体动作 |
| `review.inputs` | 非空字符串，说明每项必要输入在固定源码或 resources 中的位置和用途 |
| `review.completion` | 非空字符串，说明如何以必需产物和检查判断完成 |
| `review.blocking_questions` | 字符串数组，准备和发布前必须为空 |

所有数组中的现有条目必须为非空字符串。`review` 恰好包含表中四个键。没有额外假设时可以填空数组，不得编造已知事实、已定决策或复核结论来通过结构检查。仍依赖未交付的任务结果、缺少规则或决定时，先保留本地草稿、解决缺口；不能清空问题列表代替解决问题。

作者 agent 在现有会话中完成语义复核，不增加模型调用或审批阶梯，也不称为独立盲审。程序只验证声明结构和已知阻塞，无法证明语义闭包。`taskboard/prompts.py` 从任务的 prompt 与完整声明派生实际执行输入，发布预览和运行器共享该组合逻辑；执行时遇到未提供的关键输入必须报告，不猜测父会话。

新任务在提案准备、提案发布、CLI `validate` / `publish`、Pages 高级表单导出和 Actions 首次接纳 Issue 时执行发布条件校验。缺少 `handoff` 为 `PROMPT_CLOSURE_REQUIRED`，声明仍有阻塞为 `PROMPT_CLOSURE_BLOCKED`。直接创建原始 Issue 不能绕开接纳条件，但结构校验仍不能代替手工发布者的内容复核。

协议版本继续为 V1。`validate_task` 允许读取没有 `handoff` 的旧内容；新发布使用更严格的 `validate_publication`。已经进入 canonical state 的旧任务不改内容、不补字段、不改变摘要，继续支持领取、执行、交付和验收。尚未确认的旧草稿需要满足新要求后才能进入任务板，不能通过手改状态分支迁移。

## 下载包与首次引导

站点构建生成 `downloads/runtime.json` 和 `downloads/taskboard-runtime.zip`，并复制各系统 starter 及 `bootstrap.py`。浏览器先验证 runtime SHA-256，再将它和 `request.json`、说明文件及所选 starter 打成单个任务 ZIP。

`request.json` 只携带版本、GitHub 主机/仓库、Issue 编号、task revision/digest、runtime 摘要及 `run` / `install-publisher` 动作。任务 prompt 不拼接进可执行脚本。Bootstrap 再次校验 runtime，安全解压到用户本地缓存；配置按看板隔离。

| 系统 | 双击入口 | 本地数据根目录 |
| --- | --- | --- |
| Windows | `Start-Taskboard.cmd` | `%LOCALAPPDATA%/Taskboard` |
| macOS | `Start-Taskboard.command` | `~/Library/Application Support/Taskboard` |
| Linux | `Start-Taskboard.sh` | `$XDG_DATA_HOME/taskboard`，缺省 `~/.local/share/taskboard` |

下载 runtime 按摘要保存在数据根目录的 `runtimes/`，看板状态位于 `boards/` 下各自目录。`TASKBOARD_HOME` 可指定看板状态位置；手工 CLI 的 `--home` 同样可指定。发布集成另在对应看板状态目录的 `runtimes/` 保留其 runtime，不依赖用户一直保留下载 ZIP。安装后的 helper `context` 返回包含正确解释器、runtime 和 `--home` 的完整 `taskboard_argv`，避免 agent 使用错误看板配置。

用户应先完整解压 ZIP。Starter 先检查 Python 3.11+，缺少时提供官方安装页面、重试和退出。之后向导检查 Git、gh 和所选 provider，提供经用户同意的可用包管理器安装或官方说明页。GitHub 使用浏览器登录与公司 SSO；agent 使用自己的原生登录。

启动文件是脚本，不是签名原生可执行程序。系统可能要求来源/运行确认，Linux 文件管理器可能要求允许作为程序或在终端运行；不绕过系统保护。源项目自身依赖按任务描述准备，向导不自动推断或安装所有项目依赖。

## 普通成员如何提交 Release 成果

普通只读成员不能直接写 Release，因此默认结果路径使用 Issue 评论传输桥：

1. 本地将成果文件确定性压缩为 ZIP，并计算摘要与 manifest。
2. 在所属 Issue 创建分片评论，保留实际返回的 comment ID。
3. 发布 `submit_bundle` 命令，引用分片 ID、当前 task revision/digest、attempt ID 和验证报告。
4. Actions 先验证当前 actor、attempt 和租约，再读取同 Issue、同作者、未经编辑的分片。
5. 校验分片和完整 ZIP 摘要、路径、文件数量/大小及报告；拒绝符号链接、重复路径、穿越和解压超限。
6. Actions 只解包保存文件，不运行产物。它以工作流凭据上传 Release，补齐 HTTPS 产物地址，然后提交普通成果状态。
7. 先持久化 canonical state，再发结果通知并更新 Pages。通知失败可以恢复，不撤销已经确认的成果。

当前上限：压缩 ZIP 1 MiB；每个未压缩文件 20 MiB；未压缩总量 100 MiB；分片每段 30 KiB 原始字节、最多 35 段；最终命令不超过 48 KiB。超限在本地发送前拒绝，适合代码和文本报告。大体积必需产物应调整委托范围，不能静默丢弃。

分片使用 `<!-- taskboard:artifact:v2 -->`，最终命令使用命令 marker。单个分片无需启动一次 controller。相同执行与内容可恢复上传；不同内容不能冒用相同请求 ID 或覆盖同名产物。

旧 HTTPS `submit` manifest 仍作高级兼容接口保留，它不替使用者上传文件。无需普通成员通过此旧路径自己创建 Release。

## 结果通知、原会话与排行榜

Actions 对已确认提交发布独立结果评论，提及 publisher 与 executor，包含 result ID 和产物链接。它使用结果标记和通知记录处理重试；普通状态评论的编辑不代替新交付通知。GitHub 的邮件/推送行为遵循用户原有通知设置。

发布者在原会话要求 agent 通过 hook 给出的 `taskboard_argv` 同步与检查。手工安装 CLI 的用户可在看板项目源码根目录执行以下命令，并确保 `--home` 与发布时一致：

```sh
./bin/taskboard sync
./bin/taskboard sync --resume
```

默认 `sync` 不调用模型，只下载当前账号发布的确认成果，校验哈希，保存到 `inbox/<result-id>/artifacts/`。Manifest 与投递日志存于独立位置，避免用户产物覆盖元数据。项目 hook 只提示已在本地同步、与当前 session 精确绑定的新成果。

`--resume` 是显式原会话接续：应先退出或确认原会话空闲，避免同一 session 的多个写入者。工具对本地投递加锁、记录开始与完成；结果不明保留 `unknown`，不自动重复调用模型。没有常驻 listener，不会唤醒离线电脑；结果仍保留在 GitHub。

结果送达不等于验收。发布者通过 result ID 接受或拒收；`accept` 不应用补丁、合并或部署。排行榜基于确认快照：发布榜统计不同 task ID，排除 cancelled；完成榜仅统计 accepted，归属被验收那次执行的 actor。重试、重复评论和 submitted 状态不增加完成量。

## 协调与恢复规则

- 仓库级 controller 单并发，统一串行处理状态变更。
- 请求保留在 Issue 评论中，每次扫描补处理未处理评论；替换待运行工作流不等于丢失请求。
- `workflow_dispatch` 和每小时两次扫描可恢复积压，实际调度不保证实时。
- 同 request ID、同身份和内容重放不重复应用；不同内容的重用被拒绝。
- 当前认领期限为运行时限加 600 秒准备时间；确认领取即计一次尝试，不靠频繁 Actions 心跳续租。
- 同时只有当前有效 attempt 能提交，迟到的旧执行不能覆盖新结果。
- 等待 start 确认时保留原请求与工作目录；确认延迟不视为可以重跑模型的理由。
- 本地执行或投递结果不明时保留记录并停在可检查状态；不要删除状态来盲目重试。
- 状态分支先提交，Issue 和 Pages 后投影；通知或显示失败不撤销已确认状态。
- Actions 不执行任务 prompt、资源脚本、agent 或验收命令；模型工作与计费在领取者电脑上发生。

管理员恢复时先检查 Actions 日志、Issue 请求和确认状态，再手动运行 controller。不要手工编辑状态分支或把 Pages 快照当成权威状态。

## 开发与目标环境验证

```sh
python3 -m unittest discover -s tests -v
node --test tests/test_*.mjs
python3 -m compileall -q taskboard scripts
```

可选视觉预览用 `python3 -m http.server 8080 --directory site --bind 127.0.0.1`，再打开 `http://127.0.0.1:8080` 并明确进入演示。完整下载资源需要先走站点构建；源码 `site/` 预览不是完整发布产物。正式成员不需要本地预览。

验证分为本地自动化和目标环境联调：本地以临时 Git 仓库、模拟 GitHub API/provider 子进程及下载包检查覆盖状态、传输、安装分支和 UI；不执行真实安装、登录、模型调用或 Issue 写入。Windows / Linux 原生桌面双击与安装、公司 SSO、runner/Pages、真实两账号的完整接力与计费，需要在目标环境继续验证，不声称已完成这些现场检查。
