"""Compose the worker input from the complete immutable task package."""
from __future__ import annotations

import json

from .protocol import validate_task


def execution_prompt(task: dict) -> str:
    """Include declared context without depending on the publisher session."""
    task = validate_task(task)
    context = {key: value for key, value in task.items() if key != 'prompt'}
    return (
        task['prompt']
        + '\n\nTaskboard task context:\n'
        + json.dumps(context, ensure_ascii=False, indent=2, sort_keys=True)
        + '\n\nTaskboard execution instructions:\n'
        'You are executing an independent subtask. The publisher conversation, '
        'session, local files, and unstated decisions are not available inputs. '
        'Use the pinned source and declared resources together with this task context. '
        'Follow the stated constraints, assumptions, environment prerequisites, and stop conditions. '
        'If a required input or decision is missing or contradictory, stop the affected work '
        'and report the exact blocker in unresolved; do not invent a fact or assume access '
        'to the original session. Investigation needed to answer the task itself is allowed '
        'within its declared scope.\n'
        'Only edit permitted source.write_paths. Resources are read-only. '
        'The host exports changes.patch, summary.md and verification.json outside this checkout; '
        'do not create those three files here. The host runs acceptance.commands after you finish. '
        'Return your final response as a JSON object with exactly summary (string), '
        'assumptions (array of strings), and unresolved (array of strings). '
        'State limitations honestly; an empty unresolved array means you are explicitly reporting none.'
    )
