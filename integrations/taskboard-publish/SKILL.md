---
name: taskboard-publish
description: Use when an existing Codex or Claude session has independent work suitable for a company GitHub task board, the user asks to delegate work there, or a bound task result is ready for review.
---

# Publish an independent task

Prepare the handoff as you would delegate to a sub-agent that has never seen this conversation. **Prompt closure** means the complete execution prompt, pinned source/resources, and declared prerequisites are enough to carry out the task without the source chat. The original session ID only routes returned results; it supplies no missing context to the executor. The user decides whether that concrete task is published. Keep work local when requirements are unsettled, waiting is unacceptable, or packaging and review would cost more than finishing it.

## Prepare before asking

1. Read the relevant source and current conversation. State the independent goal, context, allowed write paths, committed inputs, required outputs, and observable acceptance criteria with actual argv verification commands. Carry over established decisions and invariants that affect this work. Resolve references such as "the approach above" or "the other task's result" into supplied facts or available pinned inputs. If a necessary decision, file, result, dependency, or access prerequisite is missing, record the exact blocker and keep the task local. Do not invent facts or authorize extra work to fill a field.
2. Use the **exact callback ID and helper argv supplied by the native Taskboard hook in this session**. Append `context --callback <callback>` to that argv to obtain the local goal directory, board, integration exclusions, `helper_argv`, and `taskboard_argv`. Both arrays include the absolute Python interpreter, isolated UTF-8 flags (`-I -X utf8`), and installed runtime path; retain the complete arrays for this task. Never assume `python` or `taskboard` is on PATH, search for a latest session, copy another session's callback, manufacture an ID, or upload a transcript. If no callback was supplied, have the user trust the installed hooks and resume this same native session.
3. Write UTF-8 goal JSON in that returned local goal directory, outside source control. Required fields are `goal` (string), `context` (string), `delegation_reason` (string), `acceptance` (nonempty string array), and `handoff` with exactly the following keys:

   | Field | Required content |
   | --- | --- |
   | `non_goals` | String array of work excluded from this delegation |
   | `constraints` | String array of preserved behavior, established decisions, and limits |
   | `assumptions` | String array of explicit assumptions supported by the task |
   | `environment` | Nonempty string declaring tools/versions, dependency preparation, and external access prerequisites; no credentials |
   | `stop_conditions` | Nonempty string array of specific missing inputs or other conditions that require stopping and reporting the gap |
   | `review` | Object with exactly `first_step`, `inputs`, `completion`, and `blocking_questions` |

   Write the three review answers as nonempty strings: a concrete first action, the location and purpose of each necessary input in the declared source/resources, and how required outputs and checks establish completion. `blocking_questions` is a string array and must be empty before preparation/publication. The first three handoff arrays may be explicitly empty when there is nothing additional to declare; every present list item must be nonempty. Do not clear real blockers or insert generic filler to pass validation.

   Optional `required_outputs` defaults to `changes.patch`, `summary.md`, and `verification.json`. Copy the returned exact `excluded_paths` into the goal JSON only for unchanged installer-managed files. Those exclusions cannot hide changed source. A helper created inside source must itself be explicitly excluded and untracked.
4. Run the helper's `prepare` with `--callback`, `--goal-file`, `--title`, repeated `--resource <committed-relative-file>` and `--write-path <relative-path>`, and repeated `--command-json '<JSON argv array>'`. Optional `--size S|M|L` and `--category code|docs|research` describe the task. The helper discovers the GitHub source remote and commit, checks remote reachability and dirty source, hashes resource bytes at that commit, and saves a stable local proposal. Inputs copied to `taskboard-inputs/` are listed in its generated prompt. A failure means the proposal is incomplete; do not bypass the check. Do not commit or push user changes merely to satisfy it.
5. Re-read the returned **`execution_prompt`**, which is derived from the task and contains the same declared context the runner supplies. Mentally execute its first step using only the package, not your memory of the chat. Check that every necessary input is available at its declared location, decisions and invariants are explicit, prerequisites are actionable, and outputs/checks are unambiguous. If you find a hidden dependency, fix the goal or inputs and prepare again before showing the proposal. This is the author agent's review, not an independent blind audit. Schema validation detects missing declarations and known blockers; it does not prove semantic completeness. Use the current session for this review, without an extra model call or approval ladder.

## Ask, then act on the reply

Show the user the title, independent scope, why delegation helps, target GitHub board, pinned source/resources, prerequisites, preserved decisions, acceptance criteria, and what information will be visible to board members. Then ask one direct question: **“Publish this task to the company's GitHub task board?”** Match the user's language when appropriate. Keep technical proposal and callback IDs out of that question.

Wait for an explicit affirmative reply referring to this proposal. Installing the skill, a hook event, general permission to work, urgency, silence, and deciding that delegation is useful do not approve publication. A refusal leaves the proposal local and creates no Issue. If the user changes the scope, prepare and show the revised proposal before asking again.

After approval, append `publish <proposal-id> --callback <callback> --approved` to `helper_argv` and execute that argv. The helper binds the Issue to the captured original session locally. Use this approval-gated helper for this workflow. On a timeout, retry the **same proposal ID and content**; creating a replacement could duplicate an Issue whose response was lost. Report the returned Issue link and the work that depends on its result. Continue useful independent work without polling the model for status.

## Review a returned result

When the user asks this existing agent to fetch or review a result, append `sync` to the exact `taskboard_argv` from `context` and execute it. No global installation or manual user command is needed. GitHub provides notifications; the native hook reports locally pending result references for this exact session, without polling GitHub or reading transcripts. Inspect the matching task revision, source commit, manifest, artifact hashes, changes, checks, assumptions, and unresolved items. Verify against the current workspace before integrating. After review, use the same `taskboard_argv` for `accept` or `reject` only on a result-specific publisher decision or already established task-specific authorization; a request to fetch or review alone does not authorize either action, and existing authorization does not need to be requested again (consult `--help` for exact arguments). Submission is not acceptance. Resume this original native session; use explicit `sync --resume` only while it is idle, so a second writer does not enter an active session.
