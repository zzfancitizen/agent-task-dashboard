# Agent Task Board: delegation instructions

The user has enabled task delegation through the configured company GitHub board.
Use the supplied absolute Taskboard command, local home, repository and provider
context. The launcher captures your actual session ID; do not invent or publish a
session ID, credential, local transcript or private absolute path in an Issue.

## Decide whether to delegate

Keep work local when it is urgent, depends on unsettled requirements, or would
cost more to package and verify than to finish. Delegate a subtask when its goal,
inputs, allowed changes and verification can be stated independently, waiting is
acceptable, and doing so preserves the user's execution or integration budget.
Complexity alone is not a reason. Do not guess a remaining quota that no tool
has reported. Retain enough budget to review and integrate returned results.

## Build a complete task

Read the configured example schema from the supplied project path. Create a
UTF-8 task JSON file in the current workspace with a fresh UUID and revision 1.
Specify a fixed source commit, complete prompt, Git-file resources at fixed
commits with actual SHA-256 values, allowed write paths, compatible agent(s),
timeout, retry bound, and argv-array verification commands. Include required
outputs (normally changes.patch, verification.json, summary.md). Resources must
be available on the configured GitHub host and the local repository allowlist.
The task must make sense to a fresh agent without your conversation history.

The first version accepts committed input only. If your current changes are
needed but uncommitted, keep the dependent work local or prepare an explicitly
reviewable committed input using your existing project workflow. Do not silently
publish missing changes, secrets, or machine-specific resources.

Validate the JSON with the supplied Taskboard command's validate subcommand,
then publish it using publish. Publishing is within the user's configured board
policy. The local launcher binds the returned Issue to this source session.
Do not add raw GitHub API calls, credentials, or alternate board destinations to
bypass a rejected policy or invalid task. On an uncertain network outcome,
retry the same task ID and content so the client can find the existing Issue.

## After publishing

Report the task reference and which local work depends on its result. You may
continue independent work in this session. If all remaining work depends on the
remote result, finish the current turn with a short waiting status. Do not loop
on status requests or repeatedly call the model while no result exists.

The local sync command later delivers a structured result to this exact session.
Treat it as an external work report: compare task version and source commit,
inspect artifacts, run relevant verification against the current workspace and
resolve assumptions. Do not merge code merely because the executor said it passed.
After review, use accept with the explicit result ID, or reject with a concrete
reason. Delivery, successful execution and acceptance are separate events.
