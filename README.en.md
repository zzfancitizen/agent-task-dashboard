# Agent Relay · Agent Task Board

[中文](README.md) | **English**

Members who are running low on agent quota can publish a complete task package. Another member with available quota runs it through their own CLI, then returns the results for the publisher to review or continue in the original session.

**Use GitHub Pages for all normal browsing. No local page, HTTP server, or separately deployed backend is required.** Viewers need only a browser; publishing, execution, and session continuation use the local CLI. The only hosted components are GitHub Issues, Actions, Pages, and Release assets. Company GitHub domains are supported.

## Contents

- [1. Choose your role](#roles)
- [2. Administrators: deploy once](#deployment)
- [3. Publishers and executors: set up your machine](#setup)
- [4. Publish a task](#publish)
- [5. Claim and execute a task](#execute)
- [6. View, review, and resume results](#results)
- [7. States, limits, and command reference](#reference)
- [8. Troubleshooting and recovery](#troubleshooting)
- [9. Development checks and optional preview](#development)
- [10. Current scope and file guide](#scope)

<a id="roles"></a>
## 1. Choose your role

| Role | Start here | Requirements |
| --- | --- | --- |
| Viewer | Open the GitHub Pages address provided by your administrator | A browser and access to the company network |
| Administrator | [Deploy the repository and Pages](#deployment) | A GitHub Actions runner available in your company environment |
| Publisher | [Set up your machine](#setup), then [publish a task](#publish) | Git, Python, and GitHub CLI; the corresponding agent CLI is also needed for agent-directed publishing or session continuation |
| Executor | [Set up your machine](#setup), then [claim and run a task](#execute) | Git, Python, GitHub CLI, and a Codex CLI or Claude Code version compatible with the task |

A complete handoff follows **publish → Actions confirmation → claim → local execution → submit results → publisher acceptance**. Pages displays a snapshot that updates asynchronously. A task marked “Available” (待领取) may have been claimed since the last refresh; the local tool confirms ownership again.

The hostname `git.company.example`, repositories `team/agent-task-board` and `team/demo-api`, paths, Issue `42`, and UUIDs in this guide are examples. Replace them with actual values. Do not execute the fictional tasks in the example files unchanged.

<a id="deployment"></a>
## 2. Administrators: deploy once

### 2.1 Prepare the task repository and runner

1. Import this entire project into the **default branch** of your company GitHub repository, keeping the `.github/` directory.
2. Enable Issues, Actions, and Pages for the repository.
3. Ensure Actions can use the `contents: write`, `issues: write`, and `pages: write` permissions declared by the workflows. Organization policies must also allow these operations.
4. Prepare a runner. GitHub.com defaults to `ubuntu-latest`; an internal GitHub Enterprise Server defaults to an existing company `self-hosted` runner.

The runner needs Python **3.11+** and GitHub CLI (`gh`). The `Check task board` workflow also needs Node.js **18+**. No third-party Python or frontend packages are required. If your company blocks external Actions, replace `actions/checkout@v4` in both workflows with your company's mirrored version.

Configure repository variables under **Settings → Secrets and variables → Actions → Variables**:

| Repository variable | Purpose | How to set it |
| --- | --- | --- |
| `TASKBOARD_RUNNER` | Select a company runner label | Optional; for example, `corp-linux`. Enter the label itself without quotes. |
| `TASKBOARD_PAGES_ENABLED` | Explicitly request a Pages build after updating `gh-pages` | Set to the string `true` after the branch has been generated for the first time. |

The controller workflow uses credentials supplied automatically by GitHub. Do not configure members' agent login files or model API keys in the repository.

### 2.2 Configure participant permissions

Accounts that publish, claim, submit, or accept tasks need **write / maintain / admin** permission on the task repository. Results are uploaded as Release assets, so read-only access cannot complete the execution workflow.

The empty list in the default `.github/taskboard.json` allows all members with those permissions. To restrict participation further, enter actual GitHub usernames:

```json
{
  "schema_version": 1,
  "allowed_members": ["alice", "bob"]
}
```

A nonempty list adds a restriction; **it does not grant native GitHub write permission**. Members also need access to the source and reference repositories used by their tasks, and must add those repositories to their local allowlist.

### 2.3 Enable Pages

1. Open **Actions → Task board controller → Run workflow** and run the workflow manually for the first time.
2. A successful run creates two branches: `taskboard-state` holds confirmed task state; `gh-pages` holds the static site and `tasks.json`.
3. Under **Settings → Pages → Build and deployment**, choose **Deploy from a branch** with **`gh-pages` / `/ (root)`** as the source. This setup uses a public site.
4. Set the repository variable `TASKBOARD_PAGES_ENABLED` to `true`, then run **Task board controller** again.
5. Share the actual Pages address displayed by GitHub with your members. The internal hostname and site path depend on your company environment.

After setup, task and command comments trigger updates. You can also run the controller manually to process pending work. A recovery scan is scheduled at minutes 17 and 47 of every hour; actual start times depend on GitHub and runner scheduling.

If your company disables the Pages build API, the static files in `gh-pages` can still be published through its existing Pages process. No additional application backend is needed. The controller is the sole writer of the state branch; do not edit `taskboard-state/state.json` manually.

<a id="setup"></a>
## 3. Publishers and executors: set up your machine

### 3.1 Prepare the tools and code

The local CLI currently supports **macOS / Linux**. Native Windows CLI use is not yet supported, but this does not restrict browsing Pages. The launcher that opens a terminal from the website currently supports macOS; use the command line on Linux.

Install Git, Python 3.11+, and GitHub CLI. Executing a task, using `start`, or resuming the original session also requires your chosen `codex` or `claude` command. Install and sign in to the agent using your company's procedure, and confirm it uses the account and quota you intend to consume. The current arguments were checked against the help output of Codex CLI `0.153.4` and Claude Code `2.1.263`; this is not a compatibility guarantee for all older versions.

```sh
git --version
python3 --version
gh --version
```

Sign in to your company GitHub, complete any organization SSO authorization, and clone this project:

```sh
gh auth login --hostname git.company.example
gh auth status --hostname git.company.example
gh repo clone https://git.company.example/team/agent-task-board
cd agent-task-board
./bin/taskboard --help
```

You can also extract a source archive and enter its directory. If extraction removed the executable permission, run `chmod +x bin/taskboard` first.

All `./bin/taskboard` examples below run from **this project's repository root**. The source workspace is supplied separately as an argument. No `pip install` or `npm install` is required. To run the `taskboard ...` commands copied from Pages from any directory, add this project's `bin` directory to your shell's PATH:

```sh
export PATH="/absolute/path/to/agent-task-board/bin:$PATH"
```

Add the actual path to your shell startup configuration to make it available in new terminals. After installing the macOS launcher, keep this project at the same location because the launcher records its path.

### 3.2 Initialize a board

```sh
./bin/taskboard init \
  --repo team/agent-task-board \
  --hostname git.company.example \
  --allow-repo team/demo-api \
  --agent codex
```

- `--repo` is the **task board repository**, in `owner/repo` format.
- `--hostname` takes only the GitHub hostname, without `https://`, a port, or a path. It defaults to `github.com` when omitted.
- `--allow-repo` adds a repository permitted for execution or resource reads. Repeat the flag to add more repositories. The task repository is included automatically; other source and reference repositories must be added explicitly.
- `--agent` sets the default agent for launcher use to `codex` or `claude`. The `run` command still requires an explicit agent, and the execution selector on Pages can specify one too.

Initialization writes local configuration only. It does not create a GitHub repository or change GitHub permissions. Run `init` again for the same board to add repositories:

```sh
./bin/taskboard init \
  --repo team/agent-task-board \
  --hostname git.company.example \
  --allow-repo team/reference-docs \
  --agent codex
```

The default local state directory is `~/.local/share/taskboard`; if `TASKBOARD_HOME` is set, its value is used instead. Each state directory is bound to one board. To use a different board, consistently pass a separate `--home` during initialization and every subsequent command:

```sh
./bin/taskboard --home /absolute/path/to/second-board-state init \
  --repo another-team/board \
  --hostname git.company.example \
  --allow-repo another-team/service
```

<a id="publish"></a>
## 4. Publish a task

### 4.1 Prepare a task package that can run independently

| Content | What to specify |
| --- | --- |
| Goal and prompt | Background, objective, execution instructions, allowed changes, and deliverables. Do not depend on “my earlier conversation.” |
| Source code | An HTTPS repository URL on the same GitHub host and an accessible full 40-character commit SHA |
| Resources | Git files pinned to commits, source paths, relative destination paths, and actual SHA-256 hashes. Use an empty array if no extra resources are needed. |
| Execution requirements | Compatible agents, execution timeout, and maximum attempts |
| Acceptance criteria | Commands as argument arrays, required output files, and items for manual review |

Only committed inputs are supported, and `source.workspace_patch` must be `null`. Source and resource commits must already be fetchable from the remote; a local unpushed commit is not enough for a handoff.

Obtain the source commit in its repository. Calculate each resource hash from **the file content at the specified commit**:

```sh
git -C /absolute/path/to/demo-api rev-parse HEAD

git -C /absolute/path/to/demo-api show COMMIT_SHA:docs/discount-rules.md \
  | python3 -c 'import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'
```

Replace `COMMIT_SHA` with the actual full commit SHA and first confirm that `git show` succeeds. A resource's `destination` must use a new path, such as `resources/discount-rules.md`; it cannot overwrite a file already present in the checkout.

Task and request JSON currently have a 48 KiB limit. Execution timeouts must be 1–14400 seconds, and maximum attempts must be 1–5. Use a new task UUID, normally starting with `revision: 1`. Generate a UUID with `python3 -c 'import uuid; print(uuid.uuid4())'`.

Acceptance commands are argument arrays such as `["python3", "-m", "unittest"]`, not shell strings such as `"python3 -m unittest"`. List multiple commands separately. For manual review, you can use an empty command array and describe the review requirements.

The local tool generates `changes.patch`, `summary.md`, and `verification.json` automatically. You can list them as required artifacts without asking the agent to create files with the same names inside the source directory. Other required artifacts, such as `reports/analysis.md`, must be generated by the agent inside the workspace, and their paths must comply with `write_paths`. The project does not install source project dependencies automatically; the task must give runnable preparation and verification steps.

Each uploaded artifact must be no larger than **20 MiB**, and the combined artifacts in one submission must be no larger than **100 MiB**.

### 4.2 Option 1: fill out the Pages form, then publish with the CLI

1. Open the deployed Pages board and click **Publish task (发布任务)**.
2. Enter the goal, source commit, allowed changes, resources, compatible agents, and acceptance criteria.
3. Click **Export task JSON (导出任务 JSON)** and save the downloaded file.
4. From this project's repository root, validate and publish it using the actual download path:

```sh
./bin/taskboard validate /absolute/path/to/downloaded-task.json
./bin/taskboard publish /absolute/path/to/downloaded-task.json
```

**Exporting a file from the website does not create an Issue.** A successful `publish` prints `Published: https://.../issues/42`. Wait for Actions validation, then refresh Pages to see the confirmed task. A plain `publish` does not automatically know an existing session's ID.

### 4.3 Option 2: let the agent decide whether to publish

Create a UTF-8 file named `work.md` that describes your actual goal, for example:

```text
Review the discount calculation module and add boundary tests.
If the task can be handed off independently, its inputs and acceptance criteria
are clear, and waiting for a claimant is acceptable, you may publish a subtask
through the configured Taskboard. Otherwise, continue working locally.
```

Start a managed original session from this project's repository root:

```sh
./bin/taskboard start \
  --agent codex \
  --prompt work.md \
  --workspace /absolute/path/to/demo-api
```

For Claude Code, replace `--agent codex` with `--agent claude`. `start` uses the noninteractive CLI, adds the [delegation instructions](resources/publisher-instructions.md), and captures the session ID from actual startup events. When the agent calls `publish`, the new Issue is automatically bound to this original session. The association is stored locally only.

Running `start` **does not guarantee that a task will be published**. The agent chooses whether to finish locally or publish based on whether the work can be handed off. The original session has a default timeout of 14400 seconds; use `--timeout` to set 1–14400 seconds. Reserve enough quota to receive and review the results later.

### 4.4 Option 3: write a task package manually or bind an existing session

You can also copy the [task example](docs/examples/task-v1.json), replace its task UUID, repository, commit, resource hashes, prompt, and acceptance criteria, then run `validate` and `publish`. The example repository and hashes are fictional placeholders and cannot be used unchanged.

To associate a task with an existing original session, provide all three binding arguments together:

```sh
./bin/taskboard publish task.json \
  --session ORIGINAL_SESSION_UUID \
  --agent codex \
  --workspace /absolute/path/to/demo-api
```

Use the actual provider session UUID, not an Issue number or task UUID. If you previously omitted the binding, rerun the command above with the exact same task package and local board configuration to reuse the existing Issue and add the binding. Taskboard does not guess or select “the most recent session.”

Here, `--agent` and `--workspace` describe the **original session**. The executor can choose another agent from the task's compatibility list. If there is no original session, omit all three binding arguments; you can still publish the task and review its results manually.

### 4.5 Change or cancel a published task

Task content is frozen. Editing the Issue body does not change the task package already confirmed by Actions. To change the goal or resources, the publisher cancels an unfinished old task and publishes another with a **new task ID**. Keep accepted tasks as historical records:

```sh
./bin/taskboard cancel 42
```

Cancellation is an asynchronous request. It invalidates authorization for later submissions, but cannot remotely force an agent already running on another computer to stop. Ask the executor to stop the process locally if needed.

<a id="execute"></a>
## 5. Claim and execute a task

### 5.1 Recommended: claim and run together

First review the full prompt, resources, allowed changes, compatible agents, and acceptance criteria on Pages, then run:

```sh
./bin/taskboard run 42 --agent codex
```

Or use Claude Code if it is compatible with the task:

```sh
./bin/taskboard run 42 --agent claude
```

`run` performs these steps in order:

1. Checks that the current GitHub account has write permission on the task repository and that the chosen agent is compatible with the task.
2. Requests a claim and waits for Actions confirmation. If you already hold a valid claim, it reuses that attempt.
3. Prepares the pinned source and resources in an isolated directory, checking hashes, paths, and the local repository allowlist.
4. Requests execution start, waits for confirmation, then runs the local agent.
5. Runs acceptance commands within the execution time limit and checks allowed changes and required artifacts.
6. Uploads Release assets and submits the result manifest, which then awaits publisher acceptance.

It does not change your existing source checkout or merge the results automatically. Each confirmation stage waits up to 180 seconds by default; you can specify 0–600 seconds:

```sh
./bin/taskboard run 42 --agent codex --wait 600
```

If you receive `REQUEST_PENDING`, check Actions, wait for confirmation, then rerun **the same command**. An execution that has not started continues waiting for the original request; upload or submission retries reuse results already generated locally. Do not delete local state to “retry,” because it contains the information that prevents duplicate execution.

### 5.2 Claim now and execute later

```sh
./bin/taskboard claim 42
./bin/taskboard claim 42 --wait 180
```

By default, `claim` returns immediately after queueing the request. **It does not start an agent.** A claim is successful only when the output says `Claim confirmed.` or the confirmed task state identifies you as its owner. Use `run` when you are ready to execute.

A claim's validity period starts when Actions confirms it and lasts for the task's execution timeout plus 600 seconds of preparation time. A confirmed claim counts as an attempt; releasing it or letting it expire does not restore that attempt.

### 5.3 Launch local execution from Pages

On macOS, after local initialization and sign-in, install the launcher once:

```sh
./bin/taskboard install-launcher
```

Then open a task's details on the deployed Pages site, select a compatible agent, and click **Claim and run (领取并运行)**. The browser opens a local terminal, where the CLI confirms the claim and executes the task. On first use, you may need to allow the local application to open Terminal.

On Linux, or without the launcher, click **Copy CLI (复制 CLI)** and run the copied command in a terminal with PATH configured. You can also replace its initial `taskboard` with this project's `./bin/taskboard` or an absolute path. Demo mode does not allow real claims.

Your local repository and hostname configuration must match the page. No `localhost` page or local HTTP service is needed.

### 5.4 Return a task to the pool

The current claimant can release their valid claim:

```sh
./bin/taskboard release 42 --reason "Unable to continue at this time"
```

If an agent is already running, press Ctrl+C in its terminal first. The CLI makes a best-effort attempt to send a release request. If the network fails, check the task state and run `release` as needed. The task reopens if attempts remain; otherwise, it enters `failed`.

### 5.5 Advanced: submit results prepared earlier

Normally, `run` handles upload and submission automatically. Use the following only when you have a prepared manifest whose artifacts have already been uploaded and whose execution identifiers are preserved accurately:

```sh
./bin/taskboard submit 42 /absolute/path/to/result.json
```

`submit` **does not run an agent or upload local files**. The manifest must bind the current task ID, revision, digest, attempt ID, and base commit, and include HTTPS URLs and hashes for the required artifacts. The current download implementation uses Release assets in the task repository. Do not substitute local paths or ordinary source-file links.

<a id="results"></a>
## 6. View, review, and resume results

### 6.1 View results on GitHub Pages

Once a task reaches `submitted`, open **Awaiting review (待验收) → View delivery (查看交付)** on Pages to see its summary, assumptions, unresolved items, verification records, and artifact links. Follow the links to download artifacts through GitHub.

The default results include:

- `changes.patch`: a patch containing the source changes.
- `summary.md`: the execution summary, assumptions, unresolved items, and changed files.
- `verification.json`: acceptance commands, exit codes, and captured output.
- Any additional deliverables explicitly required by the task.

### 6.2 Download results for your own tasks

As the publisher, run this command using your original local configuration:

```sh
./bin/taskboard sync
```

`sync` retrieves only **results in submitted/accepted state for tasks published by the current GitHub account**. It verifies hashes and records the results in the inbox without calling a model. The terminal prints the download directory.

The default directory layout is shown below. If you use `--home` or `TASKBOARD_HOME`, these files are under that directory instead:

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
    └── delivery/          # Local logs saved after requesting session continuation
```

### 6.3 Continue in the original agent session

Ensure that the managed original session has stopped. For a manually bound external session, also confirm that no other terminal is using that same session. Then run:

```sh
./bin/taskboard sync --resume
```

This processes bound results eligible for delivery to the current account and resumes **the exact original session** with a results message, letting the original agent inspect the work and continue. It consumes the original account's agent quota. Each continuation runs for at most 600 seconds.

Currently, `sync` is a one-shot command, not a background listener. It does not wake an offline computer automatically. If the managed original session is still active, no binding exists, quota is unavailable, or the session cannot be used, results remain available for inspection. Completed deliveries are not sent again.

If an interruption leaves a delivery in `unknown` state, the tool does not replay it automatically. First check whether the original session received the results. If manual continuation is needed, give the downloaded artifacts to the original CLI. Do not clear state to force a second delivery.

### 6.4 Accept or reject a result

Review the artifacts, compare them with the original task and the current code state, and run the necessary checks before accepting or rejecting the result.

Copy the **Result ID (成果编号)** from the **Actions status comment in the corresponding GitHub Issue**. This is a result UUID, not an Issue number, task ID, attempt ID, or session ID.

You can also take the UUID from the final directory name in the `inbox/<result-id>` download path printed by `sync`.

```sh
./bin/taskboard accept 42 --result RESULT_UUID
```

If the result needs rework, give a specific reason:

```sh
./bin/taskboard reject 42 \
  --result RESULT_UUID \
  --reason "Missing the zero-amount boundary test; add it and rerun verification"
```

Only the task publisher can accept, reject, or cancel. The current claimant is responsible for submission and release. Rejection preserves the previous result record and reopens the task if attempts remain.

**`accept` records the acceptance decision only. It does not apply a patch, merge a PR, or deploy code.** Complete those steps through your existing source collaboration process. An output of `queued for Actions confirmation` means only that the request was sent; wait for Actions confirmation and refresh the page to see the final state.

<a id="reference"></a>
## 7. States, limits, and command reference

| State | Meaning | Usual next step |
| --- | --- | --- |
| `open` | Available to claim | `run`, or `claim` first |
| `claimed` | Claim confirmed; execution has not started | The current claimant runs `run` |
| `running` | Execution start confirmed | Wait for local execution and submission |
| `submitted` | Results submitted; awaiting review | The publisher runs `sync`, then `accept` / `reject` |
| `accepted` | Results accepted | Integrate the results through the project's existing process |
| `cancelled` | Cancelled by the publisher | Publish a new task if new work is needed |
| `failed` | A terminal state, such as exhausted attempts | Review the records; the publisher decides whether to create a new task |

The **In progress (进行中)** view includes `claimed` and `running`. A newly published Issue does not immediately become claimable before the controller confirms it. After a claim expires, a subsequent controller run reopens the task or marks it failed. A late submission from an old attempt cannot overwrite a newer result.

| Command | Purpose |
| --- | --- |
| `init --repo OWNER/REPO --hostname HOST` | Configure the local board |
| `validate FILE` | Validate task JSON offline and print its SHA-256 digest |
| `start --agent AGENT --prompt FILE --workspace PATH` | Start an original session whose published tasks can be bound automatically |
| `publish FILE` | Publish a fixed task package; optionally provide all three original-session binding arguments |
| `claim ISSUE [--wait SECONDS]` | Request a claim only; default wait is 0 seconds |
| `run ISSUE --agent AGENT [--wait SECONDS]` | Claim, execute, verify, upload, and submit; default wait is 180 seconds per stage |
| `submit ISSUE FILE` | Submit a result manifest whose artifacts are already uploaded |
| `accept ISSUE --result UUID` | Accept the current result as its publisher |
| `reject ISSUE --result UUID --reason TEXT` | Reject the result as its publisher and explain why |
| `release ISSUE --reason TEXT` | Release a valid claim as the current claimant |
| `cancel ISSUE` | Cancel a task as its publisher |
| `sync [--resume]` | Retrieve your own results; optionally resume bound sessions explicitly |
| `install-launcher` | Install the macOS launcher |
| `open-url URL` | Called by the launcher to validate and handle a `taskboard://run` link |

Prefix the commands in this table with `./bin/taskboard`. Common arguments are `--repo`, `--hostname`, and `--home`; you normally do not need to repeat the first two after initialization. Check arguments at any time with `./bin/taskboard COMMAND --help`. There is currently no `--watch`, browser publishing that writes directly to the API, or automatic merge feature.

<a id="troubleshooting"></a>
## 8. Troubleshooting and recovery

| Symptom or error | Cause and recovery |
| --- | --- |
| Pages shows “Waiting for repository connection” (等待连接仓库) or no new tasks | Check the actual Pages address, latest controller run, `gh-pages/tasks.json`, and Pages build. Demo mode is not live task data. An administrator can use **Run workflow** manually. |
| Actions stays queued | Check the `TASKBOARD_RUNNER` label, company runner availability, and organization policies. A separate application backend is not required. |
| `GITHUB_403` / `WRITE_PERMISSION_REQUIRED` | Check `gh auth status --hostname ...`, SSO authorization, and native write permission on the task repository. The allowlist cannot replace write permission. For Actions errors, an administrator must also check workflow permissions. |
| `TASK_PENDING` / `REQUEST_PENDING` | The request has not been confirmed. Wait for the workflow, then rerun the same command. This does not automatically rerun a model execution that already started. |
| `CONFIG_CONFLICT` / `LAUNCH_REPOSITORY_MISMATCH` | The command or page points to another hostname or repository. Use the correct configuration and a separate `--home` for another board. |
| `REPOSITORY_NOT_ALLOWED` | Run `init --allow-repo OWNER/REPO` again with the same board arguments. Also confirm that the gh account can access that repository. |
| `INCOMPATIBLE_AGENT` | Use `codex` or `claude` as listed by the task. An incompatible choice does not consume a claim attempt. |
| `TASK_NOT_OPEN` / `NOT_OWNER` / `ATTEMPT_EXPIRED` / `STALE_ATTEMPT` | Check the current claimant, claim validity, and attempt count. An old execution cannot submit using a newer attempt. Do not edit the state branch manually. |
| `RESOURCE_HASH_MISMATCH` / `RESOURCE_COLLISION` / `UNSAFE_PATH` | Check the pinned commit, file hash at that version, and resource destination. The current execution directory does not support symbolic links, including those created during builds. Resources cannot overwrite existing files or point into `.git`. |
| `VERIFICATION_FAILED` / `WRITE_SCOPE_VIOLATION` / `OUTPUT_MISSING` | Inspect the local directory printed by the terminal, including `checks/` and `artifacts/verification.json`. Failed work is not submitted as a successful result. Correct the task scope or dependencies, then retry through the normal workflow. |
| Network interruption during upload or submission | Keep the local directory and rerun the same `run` command. If results were already prepared, the tool reuses the artifacts. A Release asset with the same name but different content is not overwritten. |
| `EXECUTION_UNKNOWN` / `LOCAL_BUSY` | An execution or lock may already exist. Do not blindly start another instance. Check the relevant local processes and records; stop and release the task if needed, then wait for a new valid claim. |
| `PUBLISH_UNKNOWN` / `TASK_CONFLICT` | Check GitHub for an existing task with the same ID. The same ID and content can be reused; changed content requires a new task ID. Do not delete local state to bypass an uncertain write. |
| `sync` reports no results | Confirm that the current gh account is the publisher, Actions has confirmed the results as submitted/accepted, and the configuration points to the correct board. |
| No session binding, or delivery is `unknown` | Downloaded results remain usable. You can explicitly add a binding with the original task package. Check the original session before handling an unknown delivery; it is not replayed automatically. |
| Clicking **Claim and run (领取并运行)** does nothing | On macOS, complete initialization and `install-launcher`, then check application and Terminal permissions. On Linux, or without the launcher, copy the CLI command. Before reinstalling from a new directory, remove only the old `Taskboard Launcher.app`. |

<a id="development"></a>
## 9. Development checks and optional preview

These steps are for development and verification only. Normal users open GitHub Pages directly.

```sh
python3 -m unittest discover -s tests -v
node --test tests/test_site.mjs
python3 -m compileall -q taskboard scripts
```

Optional local preview:

```sh
python3 -m http.server 8080 --bind 127.0.0.1 --directory site
```

Open the [local development preview](http://127.0.0.1:8080/) and click **Try the demo (先体验演示)**. Demo mode does not actually publish, claim, or execute tasks and is not a deployment of live data.

<a id="scope"></a>
## 10. Current scope and file guide

- Supports `subtask` delegation only, without full session migration, uncommitted workspace patches, or arbitrary recursive delegation.
- Each member keeps their own GitHub and agent login. Original session associations remain local.
- The local tool checks resource and artifact hashes, allowed changes, and the current attempt and result IDs. `write_paths` is a check before submission, not an operating-system path ACL. Acceptance commands run with local machine permissions and should come from authorized, trusted tasks.
- Programmatic execution does not prepare every project's dependencies automatically or guarantee compatibility with every older provider CLI.
- Tests use temporary Git repositories, simulated GitHub boundaries, and simulated provider subprocesses without consuming real model quota. Company SSO, real runner/Pages publishing, execution by two actual accounts, and billing still require validation on the company network.

| File | Contents |
| --- | --- |
| [README.md](README.md) | Complete Chinese user guide |
| [docs/github-only.md](docs/github-only.md) | Additional deployment and recovery details |
| [resources/publisher-instructions.md](resources/publisher-instructions.md) | Delegation instructions added automatically to managed original sessions |
| [task-v1.json](docs/examples/task-v1.json) / [result-v1.json](docs/examples/result-v1.json) | Task and result structure examples |
| [taskboard.yml](.github/workflows/taskboard.yml) | Controller and Pages update workflow |
| [protocol.py](taskboard/protocol.py) / [state.py](taskboard/state.py) | Task protocol and state transitions |
| [cli.py](taskboard/cli.py) | Local CLI implementation |

`docs/superpowers/` retains early design records. Its separate-backend proposal has been superseded by the current GitHub-only implementation.
