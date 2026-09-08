# Agent Relay · Agent Task Board

让有额度压力的成员通过 agent 发布完整任务包，由其他成员用自己的 CLI 接力执行，再将可验证的结果送回原会话。

**线上只使用 GitHub：公开 Pages + Issues + Actions + Release 附件。无需额外后台服务、数据库或网页账号系统。** 支持可配置的公司内网 GitHub 域名。

## 已实现

- 中文响应式看板：搜索、状态/Agent/类型筛选、任务详情、完整 prompt 复制、参考资源与交付展示。
- 任务表单导出经过校验的 JSON；空仓库显示接入说明，演示数据单独标识。
- Actions 处理认领、开始、提交、拒收与验收；状态先持久化，页面随后异步更新。
- 本地 CLI 准备固定 commit 的工作目录，检查资源哈希，运行 Codex 或 Claude Code，执行验收命令并回传成果。
- `start` 管理发起会话，捕获真实 session ID，让 agent 发布时自动建立本机关联。
- `sync` 下载结果；`sync --resume` 显式将结果送回原会话，保留失败或未知投递状态以避免重复执行。
- macOS 一键启动入口；Linux 使用复制 CLI 命令。

## 先看界面

```sh
python3 -m http.server 8080 --bind 127.0.0.1 --directory site
```

打开 [本地看板](http://127.0.0.1:8080/)，点击“先体验演示”。示例模式不会发布、认领或运行真实任务。

## 导入公司 GitHub

完整步骤见 [部署与运行指南](docs/github-only.md)。核心步骤：

1. 将代码导入仓库默认分支；使用公司已有的 Actions runner，按需设置 `TASKBOARD_RUNNER` 标签。
2. 手动运行 **Task board controller**，生成 `taskboard-state` 与 `gh-pages` 分支。
3. Pages 选择 `gh-pages` 的根目录公开发布。
4. 设置 `TASKBOARD_PAGES_ENABLED=true`，再次运行 controller 请求 Pages build。

内网 runner 需要 Python 3.11+、GitHub CLI；检查工作流另外使用 Node.js 18+。如果公司只允许镜像过的 Actions，将 checkout action 换为公司提供的版本。

## 本地使用

以下使用说明用域名和仓库名；替换为实际配置。无需把地址或凭据交给开发者。

```sh
gh auth login --hostname git.company.example
./bin/taskboard init --repo team/agent-task-board --hostname git.company.example --allow-repo team/demo-api

# 让发起者的 agent 可以自主发布，并自动关联真实会话
./bin/taskboard start --agent codex --prompt work.md --workspace /absolute/path/to/demo-api

# 或发布一个已经准备好的任务包
./bin/taskboard validate task.json
./bin/taskboard publish task.json

# 认领者执行；等待 Actions 确认后才开始
./bin/taskboard run 42 --agent codex

# 发起者下载结果，或明确选择接回原会话
./bin/taskboard sync
./bin/taskboard sync --resume

# macOS 注册看板的一键运行入口
./bin/taskboard install-launcher
```

若从既有的未受管理会话发布，使用 `publish --session UUID --agent codex --workspace PATH` 显式绑定。恢复前确保该会话已退出或空闲；本地工具只能锁定它管理的运行和投递。

当前执行参数已对照本机 Codex CLI `0.153.4` 与 Claude Code `2.1.263` 的帮助信息核验。工作流不会执行模型；真正的 agent 任务只在认领者电脑运行。

任务与资源仓库需要在本机允许列表中。`write_paths` 用于提交前校验，不是操作系统级路径 ACL；验收命令是本地执行的命令，因此只接收已授权、可信仓库的任务。

## 验证

```sh
python3 -m unittest discover -s tests -v
node --test tests/test_site.mjs
python3 -m compileall -q taskboard scripts
```

测试使用临时 Git 仓库、模拟 GitHub 边界和模拟 provider 子进程，不消费真实模型额度。真实内网 SSO、公司 runner/Pages 配置、两名成员的真实 CLI 计费归属需在公司环境联调。

## 文件入口

- [部署与运行指南](docs/github-only.md)
- [Agent 委派说明](resources/publisher-instructions.md)
- [任务包示例](docs/examples/task-v1.json) / [结果示例](docs/examples/result-v1.json)
- [任务协议](taskboard/protocol.py) / [状态转换](taskboard/state.py)
- [Actions 控制器](taskboard/controller.py) / [本地 CLI](taskboard/cli.py)

`docs/superpowers/` 中保留了早期设计记录；其中的独立后台方案已被当前 GitHub-only 实现替代。
