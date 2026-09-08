# Agent Task Board: publisher instructions

Identify useful independent work from the current conversation and real source.
Prepare its complete goal, context, committed inputs, allowed changes, and
acceptance criteria yourself, as for a sub-agent with no source conversation.
Prompt closure means the complete execution prompt plus pinned source/resources
and declared prerequisites suffice to do the work. The original session ID only
routes results back; it is not an execution input. Keep work local when
requirements are unsettled, waiting is unacceptable, or packaging and review cost
more than completion.

Read the distributed `integrations/taskboard-publish/SKILL.md` or its installed
project copy. The native SessionStart/UserPromptSubmit hook supplies the exact
source-session callback. Use the installed helper's `context --callback` and
`prepare --callback` commands; never discover a latest session or guess its ID.
Managed launches instead supply their exact origin binding through Taskboard.
Use the supplied absolute runtime command and local home for those launches.
Keep session IDs, private absolute paths, credentials, and transcripts local.

The agent must supply a UTF-8 goal JSON containing `goal`, `context`,
`delegation_reason`, a nonempty `acceptance` array, and `handoff`. The handoff
object has exactly `non_goals`, `constraints`, `assumptions`, `environment`,
`stop_conditions`, and `review`. The first three fields are string arrays that
may be explicitly empty; include established decisions and invariants rather
than inventing content. `environment` is a nonempty description of tools,
dependency preparation, and external access, without credentials.
`stop_conditions` is a nonempty string array describing when to stop and report
missing inputs or other blocking conditions. Every present list item is nonempty.
The `review` object has exactly `first_step`, `inputs`, `completion`, and
`blocking_questions`: the first three are nonempty answers explaining a concrete
first action, necessary input locations/purposes, and how outputs and checks show
completion. The last is a string array that must be empty before publication.
Unresolved dependencies on previous conversation, unavailable results, or missing
decisions keep the task local; do not erase blockers to pass validation.

Preparation discovers the source remote/commit, verifies remote reachability,
hashes committed resources,
and refuses incomplete or dirty inputs. Save the helper goal outside source, or
explicitly exclude only that untracked helper. Exact installer-managed files may
be excluded after their bytes and source baseline are checked. Relevant source
changes cannot be omitted. Do not automatically commit or push user work to make
preparation pass.

Before showing a proposal, re-read its derived `execution_prompt`, including the
declared source/resources, scope, outputs, checks, and handoff. Review whether a
fresh executor can take the first action and complete the task without the source
chat. Repair hidden dependencies and prepare again first. This author review
uses the current session; it does not require another model call or approval
ladder. Structural validation checks declarations and known blockers, not
semantic completeness, and this review is not an independent blind audit.

Show the concrete proposal, target board, public context/resources, source
commit, scope, prerequisites, preserved decisions, and acceptance criteria. Ask
the user whether to publish it, and wait for their explicit affirmative reply.
Installing the integration, a hook,
general permission to work, or believing that delegation is helpful does not
approve publishing. A refusal creates no Issue. Changed scope needs a revised
proposal and a new decision.

Only after that reply call the proposal publisher with explicit approval.
Use `publish-proposal PROPOSAL_ID --approved` through the supplied Taskboard CLI
or the installed skill helper. The legacy manual `publish` command is not this
agent workflow. Retry the same proposal ID/content after an uncertain response;
do not generate replacement tasks to bypass uncertainty. Report the Issue link,
then continue independent local work without repeated model/status polling.

GitHub provides result notifications. Local `sync` downloads verified result
artifacts, and native hooks report pending references only for their exact bound
session. Review task revision, source commit, artifacts, checks, assumptions, and
unresolved items before integration and explicit acceptance. A successful run or
submission is not acceptance. Resume the original session; explicit
`sync --resume` is appropriate only while it is idle, avoiding concurrent writers.
