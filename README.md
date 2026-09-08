# Agent Task Board

面向组内成员的 agent 任务委派工具：有额度压力的成员由 agent 整理并发布可执行任务包，其他成员认领后使用自己的 CLI 执行，结果回到发起者原会话供验收和继续工作。

当前阶段：产品设计和首个实现切片的实施计划已整理，尚未实现应用或连接真实 GitHub 仓库。

- [产品与协议设计](docs/superpowers/specs/2026-09-08-agent-task-board-design.md)
- [核心闭环实施计划](docs/superpowers/plans/2026-09-08-agent-task-board-core.md)
- [任务包示例](docs/examples/task-v1.json)
- [执行结果示例](docs/examples/result-v1.json)

首个实现切片聚焦任务协议、认领、执行、结果投递与验收；后续界面复用这些接口。示例中的仓库、提交和产物用于协议说明，不是可直接执行的真实任务。
