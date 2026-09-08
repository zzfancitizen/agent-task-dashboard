# GitHub-only 部署与运行

这份实现说明替代最初的独立后台设计。线上只有 GitHub：Issues 保存请求，Actions 顺序处理，`taskboard-state` 分支保存确认后的状态，`gh-pages` 分支保存公开看板。本机程序负责调用已有 CLI 和接收成果，无常驻线上服务或数据库。

## 部署前提

公司 GitHub 已启用 Issues、Actions 和 Pages。GitHub Enterprise Server 使用公司已有 self-hosted runner；GitHub.com 默认使用 `ubuntu-latest`。可设置仓库变量 `TASKBOARD_RUNNER` 选择公司 runner 标签。Runner 需要 Python 3.11+、GitHub CLI 和 Node.js 18+（用于前端测试）；运行 agent 的电脑还需要对应 agent CLI。

工作流使用 `actions/checkout@v4`。内网禁用外部 Actions 时，由管理员将其替换为公司已镜像的 checkout action。代码不调用外部托管平台，也不需要模型 API key。

## 导入与启用

1. 将项目代码导入公司仓库的默认分支，启用 Actions。
2. 如需要，在 `.github/taskboard.json` 的 `allowed_members` 中填写允许参与的 GitHub 用户名。参与者必须具有仓库 write/maintain/admin 权限，以便上传 Release 成果；非空名单会进一步限制参与人员，不能代替 GitHub 原生写权限。普通公开访问者只能浏览。
3. 首次手动运行 **Task board controller**。它会创建 `taskboard-state` 和 `gh-pages` 分支；此时尚不要求 Pages 已启用。
4. 在仓库 **Settings → Pages** 选择从分支发布，来源为 `gh-pages` 的 `/ (root)`。按已确认要求使用公开站点，不增加应用登录。
5. 设置仓库变量 `TASKBOARD_PAGES_ENABLED=true`，再次运行 controller。工作流会显式请求 Pages build，避免依赖 `GITHUB_TOKEN` 提交自动触发构建。
6. 以 GitHub 显示的实际 Pages 地址访问看板。域名、仓库路径与任务链接来自 Actions 当前仓库环境，无需把内网仓库地址写进代码。

GitHub Enterprise Server 的 Pages 管理策略和 build API 权限需要在公司环境验证。如果 build API 被策略禁用，已生成的 `gh-pages` 分支仍可由公司现有发布流程构建；不要另起一套后台作为默认补救。

## 每位成员的本机配置

以下域名和仓库名是说明用值，使用时替换为公司的实际值：

```sh
gh auth login --hostname git.company.example
./bin/taskboard --repo team/agent-task-board --hostname git.company.example init --allow-repo team/demo-api
```

授权在本机完成。看板网页没有 token 输入框，不复制 CLI 登录文件或 session 日志到 GitHub。源代码仓库和资料仓库需要位于配置允许的仓库集合中；通过 init 的 `--allow-repo` 追加。

## Agent 自主发布

用 `taskboard start --agent codex --prompt work.md --workspace /absolute/project/path` 启动受管理的发起会话。启动器把委派工具说明加入上下文，并捕获真实 session 启动事件。Agent 调用 publish 时，本地工具自动关联到这个原会话；任务 Issue 不包含 session ID。Claude Code 同样通过 `--agent claude` 使用。

## 发布、认领与交付

任务包包含完整 prompt、固定 commit 的源代码和资源、允许修改目录、执行时限、验收命令和产物要求。可从看板“发布任务”导出 JSON，或让 agent 按示例生成。示例是结构说明，运行前必须填入真实可访问资源与摘要。

```sh
./bin/taskboard validate task.json
./bin/taskboard publish task.json --session ORIGINAL_SESSION_ID --agent codex --workspace /absolute/project/path
./bin/taskboard run 42 --agent codex
```

`run` 先发送认领请求，等待 Actions 在状态分支确认当前账号与执行编号，之后准备独立工作目录并运行 agent。它不会依据静态页面上的“待认领”直接开跑。开始确认仍在排队时保留工作目录和原请求，重试只继续等待，不把延迟当成执行失败。

执行结果通过 Release 附件保存，记录哈希，之后提交结果 manifest 到 Issue。报告保留 summary、assumptions 和 unresolved；如果 agent 返回普通文本，会保留全文并明确提示尚未得到结构化假设/未解决事项。不会自动合并源代码。相同执行编号的产物不允许用不同内容覆盖。

已发布的任务包不可原地修改。需要改变目标或资源时，先用 `taskboard cancel ISSUE` 取消旧任务，用新的 task ID 发布新任务；避免两种内容共享同一个确认记录。

## 回到原会话

```sh
./bin/taskboard sync
./bin/taskboard sync --resume
```

默认 sync 下载并登记新成果。用户产物保存在该结果目录的 `artifacts/` 下，manifest 和投递日志使用独立位置，避免同名文件覆盖。只有显式选择 `--resume` 才尝试把已绑定成果送回指定原 session；从不使用 `--last`。用户应在原会话已经退出或确认空闲时调用。程序会对同一 session 的本地投递加锁，但不能阻止用户在另一个未受管理的终端同时打开相同 session。

投递前记录 delivering，完成后记录 delivered。中断导致结果不明时保留 unknown 状态，避免自动重复消耗额度；需要核对会话后再处理。结果送达与任务验收分离，发起者确认实际结果后才 accept。

原电脑离线、原账号没有额度或原 session 记录不可用时，成果继续保留在 GitHub，并可下载查看。

## 一键运行

macOS 可安装本地 URL handler：

```sh
./bin/taskboard install-launcher
```

之后看板的“认领并运行”会唤起本机终端。首次安装和本机 gh/agent 登录是前提。URL 只携带仓库、域名、Issue 编号及所选 Agent；启动器核对它们与本机配置一致后，再读取真实任务。

Linux 和未安装 handler 的电脑可复制看板提供的 CLI 命令。页面和本地运行工具之间不需要一个额外部署的 HTTP 服务。

## 协调与恢复规则

- 控制器工作流采用仓库级单并发，所有状态变更均由它处理。
- 请求保留在 Issue 评论中；每次运行重新检查未处理评论。待运行工作流被替换不等于请求丢失。
- 手动 workflow_dispatch 与每小时两次的定时恢复都会补处理积压请求；调度时间不保证实时。
- 同一 request ID 重放不重复执行；内容不同的重用被拒绝。
- 当前认领固定期限为任务运行时限加 10 分钟准备时间。首版不以频繁 Actions 心跳续租。
- 同时只有一个有效执行编号能提交；旧编号过期后的迟到结果不能覆盖新结果。
- 状态分支先提交，Issue 展示后更新。页面是异步快照，不作为认领依据。
- 不把执行者的完成声明自动当作已验收事实。

Actions 只处理 JSON、GitHub API 和页面构建；不会执行任务 prompt、资源中的脚本或验收命令。真正的 agent 工作消耗认领者自己的 CLI 额度。

## 本地检查与预览

```sh
python3 -m unittest discover -s tests -v
node --test tests/test_site.mjs
python3 -m compileall -q taskboard scripts
python3 -m http.server 8080 --directory site --bind 127.0.0.1
```

打开 `http://127.0.0.1:8080`。未配置的看板显示接入说明；示例任务只在用户明确进入演示模式时显示，不会作为真实任务部署。

## 实际联调范围

本地测试覆盖协议、权限、认领冲突、迟到提交、持久化/重复投递、路径与资源校验、GitHub transport 及 UI 行为。公司内网的 SSO、runner 权限、Pages build、两个真实账号执行和计费归属，需要在导入后验证。项目不声称已完成无法访问的内网部署。

## 参考

- [Issue 评论触发工作流](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#issue_comment)
- [Actions 并发与积压处理](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)
- [请求 Pages build](https://docs.github.com/en/rest/pages/pages#request-a-github-pages-build)
- [Enterprise Server runner](https://docs.github.com/en/enterprise-server@3.19/actions/concepts/runners/github-hosted-runners)
