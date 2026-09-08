"""Pure, deterministic task transitions for the single GitHub controller writer."""

import copy
import hashlib
import uuid

from .protocol import (
    ProtocolError,
    _canonical_json,
    _https_url,
    _integer,
    _require,
    _text,
    _validate_command,
    task_digest,
    validate_result,
    validate_task,
)


def _clock(now):
    _integer(now, "now", minimum=0)


def _check_content(record):
    _require(type(record) is dict, "Task record must be an object.")
    _require(task_digest(record.get("spec")) == record.get("digest"),
             "Pinned task content has changed.", "IMMUTABLE_TASK")


def new_task(issue, spec, now):
    _clock(now)
    spec = validate_task(spec)
    _require(type(issue) is dict, "Issue must be an object.")
    _integer(issue.get("number"), "issue.number", minimum=1)
    _https_url(issue.get("html_url"), "issue.html_url")
    _require(type(issue.get("user")) is dict, "Issue must identify its author.")
    _text(issue["user"].get("login"), "issue.user.login")
    _text(issue.get("created_at"), "issue.created_at")
    return {
        "number": issue["number"], "url": issue["html_url"],
        "author": issue["user"]["login"], "created_at": issue["created_at"],
        "spec": spec, "digest": task_digest(spec), "status": "open",
        "attempt_count": 0, "attempt": None, "result": None,
        "processed": {}, "result_history": [], "updated_at": now,
    }


def _retry_status(record):
    return "open" if record["attempt_count"] < record["spec"]["execution"]["max_attempts"] else "failed"


def _expire(record, now):
    if record["status"] in {"claimed", "running"} and record["attempt"]["expires_at"] <= now:
        record["attempt"] = None
        record["status"] = _retry_status(record)
        record["updated_at"] = now


def expire_task(record, now):
    _clock(now)
    _check_content(record)
    result = copy.deepcopy(record)
    _expire(result, now)
    return result


def _identifier(record, kind, request_id):
    namespace = uuid.UUID(record["spec"]["task_id"])
    return str(uuid.uuid5(namespace, f"{record['spec']['revision']}:{kind}:{request_id}"))


def apply_command(record, command, *, actor, comment_id, now):
    """Return a copied record, including a durable accepted/rejected outcome.

    Identical request replays preserve the original outcome and timestamp.
    Conflicting IDs raise without replacing that durable first outcome.
    GitHub actor identity and repository permission checks belong to the caller.
    """
    _clock(now)
    _check_content(record)
    _text(actor, "actor")
    _integer(comment_id, "comment_id", minimum=1)
    command = _validate_command(command)
    fingerprint = hashlib.sha256(_canonical_json({
        "actor": actor.casefold(), "command": command,
    }).encode("utf-8")).hexdigest()
    request_id = command["request_id"]
    previous = record["processed"].get(request_id)
    if previous is not None:
        if previous.get("fingerprint") != fingerprint:
            raise ProtocolError("IDEMPOTENCY_CONFLICT", "Request ID was already used for different content or actor.")
        return copy.deepcopy(record)

    result = copy.deepcopy(record)
    _expire(result, now)

    def outcome(ok, code="OK", message="Command applied."):
        result["processed"][request_id] = {
            "ok": ok, "code": code, "message": message,
            "comment_id": comment_id, "fingerprint": fingerprint,
        }
        result["updated_at"] = now
        return result

    if command["revision"] != result["spec"]["revision"]:
        return outcome(False, "REVISION_MISMATCH", "Command does not match the pinned task revision.")

    op = command["op"]
    if op in {"accept", "reject", "cancel"} and actor.casefold() != result["author"].casefold():
        return outcome(False, "UNAUTHORIZED", "Only the task author can review or cancel this task.")

    if op == "claim":
        if result["status"] != "open":
            return outcome(False, "TASK_NOT_OPEN", "Task is not open for a claim.")
        if result["attempt_count"] >= result["spec"]["execution"]["max_attempts"]:
            result["status"] = "failed"
            return outcome(False, "ATTEMPTS_EXHAUSTED", "Task has exhausted its attempt budget.")
        result["attempt_count"] += 1
        result["attempt"] = {
            "id": _identifier(result, "attempt", request_id), "actor": actor,
            "expires_at": now + result["spec"]["execution"]["timeout_seconds"] + 600,
        }
        result["status"] = "claimed"
        return outcome(True)

    if op in {"start", "release", "submit"}:
        attempt = result["attempt"]
        if attempt is None or attempt["id"] != command["attempt_id"]:
            return outcome(False, "STALE_ATTEMPT", "Command does not identify a current unexpired attempt.")
        if actor.casefold() != attempt["actor"].casefold():
            return outcome(False, "UNAUTHORIZED", "Only the current attempt owner can perform this action.")
        if result["status"] not in {"claimed", "running"}:
            return outcome(False, "INVALID_STATE", "Task has no active execution attempt.")
        if op == "start":
            result["status"] = "running"
        elif op == "release":
            result["attempt"] = None
            result["status"] = _retry_status(result)
        else:
            manifest = validate_result(command["result"], result["spec"], attempt["id"])
            result["result"] = {"id": _identifier(result, "result", request_id), "manifest": manifest}
            result["status"] = "submitted"
        return outcome(True)

    if op in {"accept", "reject"}:
        current = result["result"]
        if result["status"] != "submitted" or current is None or current["id"] != command["result_id"]:
            return outcome(False, "STALE_RESULT", "Review does not identify the current submitted result.")
        if op == "accept":
            result["status"] = "accepted"
        else:
            result.setdefault("result_history", []).append({
                **current, "reason": command["reason"], "reviewed_by": actor,
                "reviewed_at": now, "outcome": "rejected",
            })
            result["result"] = None
            result["attempt"] = None
            result["status"] = _retry_status(result)
        return outcome(True)

    if result["status"] in {"accepted", "cancelled"}:
        return outcome(False, "INVALID_STATE", "This task is already terminal.")
    result["attempt"] = None
    result["status"] = "cancelled"
    return outcome(True)
