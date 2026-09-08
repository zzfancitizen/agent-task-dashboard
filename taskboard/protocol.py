"""Versioned public task and command records with deterministic content hashes.

This module validates metadata only. The local executor separately enforces its
configured repository allowlist and verifies downloaded artifact bytes.
"""

import copy
import hashlib
import json
import re
import uuid
from urllib.parse import parse_qsl, urlsplit


MAX_PAYLOAD_BYTES = 48 * 1024
TASK_MARKER = "<!-- taskboard:task:v1 -->"
COMMAND_MARKER = "<!-- taskboard:command:v1 -->"


class ProtocolError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _require(condition, message, code="INVALID_PAYLOAD"):
    if not condition:
        raise ProtocolError(code, message)


def _canonical_json(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ProtocolError("INVALID_JSON", "Payload must contain finite JSON values.") from exc


def _encoded(value):
    try:
        return _canonical_json(value).encode("utf-8")
    except UnicodeError as exc:
        raise ProtocolError("INVALID_JSON", "Payload must contain valid UTF-8 text.") from exc


def _bounded(value):
    _require(len(_encoded(value)) <= MAX_PAYLOAD_BYTES,
             "Payload exceeds the 48 KB UTF-8 limit.", "PAYLOAD_TOO_LARGE")


def _object(value, required, optional=(), *, field="payload"):
    _require(type(value) is dict, f"{field} must be an object.")
    _require(set(required) <= value.keys(), f"{field} is missing required fields.")
    _require(value.keys() <= set(required) | set(optional), f"{field} contains unsupported fields.")


def _text(value, field, *, allow_empty=False):
    _require(type(value) is str, f"{field} must be a string.")
    _require(allow_empty or bool(value.strip()), f"{field} must not be empty.")
    _require("\x00" not in value, f"{field} must not contain NUL.")


def _integer(value, field, *, minimum=None, maximum=None):
    _require(type(value) is int, f"{field} must be an integer.")
    _require(minimum is None or value >= minimum, f"{field} is below its allowed minimum.")
    _require(maximum is None or value <= maximum, f"{field} exceeds its allowed maximum.")


def _uuid(value, field):
    _text(value, field)
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise ProtocolError("INVALID_PAYLOAD", f"{field} must be a UUID.") from exc
    _require(str(parsed) == value.lower(), f"{field} must use the hyphenated UUID form.")


def _hex(value, length, field):
    _require(type(value) is str and re.fullmatch(rf"[0-9a-fA-F]{{{length}}}", value) is not None,
             f"{field} must contain {length} hexadecimal characters.")


def _path(value, field, *, directory=False):
    _text(value, field)
    _require(not any(ord(char) < 32 for char in value) and "\\" not in value,
             f"{field} must be a safe relative POSIX path.")
    parts = value.removesuffix("/").split("/") if directory else value.split("/")
    _require(all(part not in {"", ".", ".."} for part in parts),
             f"{field} must be a normalized relative path.")
    _require(not re.match(r"^[A-Za-z]:", value) and
             all(part.casefold() != ".git" for part in parts),
             f"{field} must not target Git metadata or an absolute path.")


def _https_url(value, field):
    _text(value, field)
    _require(not any(char.isspace() or ord(char) < 32 for char in value) and "\\" not in value,
             f"{field} must be an HTTPS URL without whitespace.")
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme == "https" and bool(parsed.hostname) and parsed.port != 0
    except ValueError as exc:
        raise ProtocolError("INVALID_PAYLOAD", f"{field} is not a valid HTTPS URL.") from exc
    _require(valid and parsed.username is None and parsed.password is None,
             f"{field} must be an HTTPS URL without credentials.")
    _require(not parsed.fragment, f"{field} must not contain a fragment.")
    for key, _ in parse_qsl(parsed.query):
        _require(not _private_key(key), f"{field} must not contain authentication parameters.")
    return parsed


def _repository(value, field):
    parsed = _https_url(value, field)
    _require(not parsed.query, f"{field} must identify a repository without query parameters.")
    parts = parsed.path.strip("/").split("/")
    _require(len(parts) == 2 and all(
        re.fullmatch(r"[A-Za-z0-9_.-]+", part) and part not in {".", ".."}
        for part in parts
    ), f"{field} must identify an owner and repository.")
    _require(not parsed.path.startswith("//"), f"{field} must identify one repository.")


def _list(value, field):
    _require(type(value) is list, f"{field} must be a list.")


def _strings(value, field):
    _list(value, field)
    for item in value:
        _text(item, field + " item")


def _argv(value, field):
    _strings(value, field)
    _require(bool(value), f"{field} must contain an executable.")


def _private_key(key):
    normalized = key.casefold().replace("-", "_")
    return normalized in {"auth", "authorization", "token", "api_key", "apikey",
                          "access_token", "refresh_token", "password", "secret"} or \
        normalized.endswith("session_id")


def _private_fields(value):
    if type(value) is dict:
        for key, child in value.items():
            _require(type(key) is str and not _private_key(key),
                     "Public records must not contain authentication or session fields.")
            _private_fields(child)
    elif type(value) is list:
        for child in value:
            _private_fields(child)


def validate_task(task):
    """Return an independent validated task; do not insert or normalize fields."""
    _bounded(task)
    _object(task, {"schema_version", "task_id", "revision", "mode", "title", "prompt",
                   "delegation_reason", "source", "resources", "execution", "acceptance"},
            {"category", "size", "priority"}, field="task")
    _integer(task["schema_version"], "schema_version", minimum=1, maximum=1)
    _uuid(task["task_id"], "task_id")
    _integer(task["revision"], "revision", minimum=1)
    _require(task["mode"] == "subtask", "Only subtask mode is supported.")
    for key in ("title", "prompt", "delegation_reason"):
        _text(task[key], key)
    for key, choices in {"category": ("code", "docs", "research"),
                         "size": ("S", "M", "L"), "priority": ("normal", "high")}.items():
        if key in task:
            _require(task[key] in choices, f"{key} has an unsupported value.")

    source = task["source"]
    _object(source, {"repository", "base_commit", "workspace_patch", "write_paths"}, field="source")
    _repository(source["repository"], "source.repository")
    _hex(source["base_commit"], 40, "source.base_commit")
    _require(source["workspace_patch"] is None, "Workspace patches are not supported in this version.",
             "UNSUPPORTED_WORKSPACE_PATCH")
    _list(source["write_paths"], "source.write_paths")
    for path in source["write_paths"]:
        _path(path, "source.write_paths item", directory=True)
    _require(len(set(source["write_paths"])) == len(source["write_paths"]),
             "source.write_paths must not contain duplicates.")

    _list(task["resources"], "resources")
    destinations = set()
    for resource in task["resources"]:
        _object(resource, {"type", "repository", "commit", "path", "destination", "sha256"}, field="resource")
        _require(resource["type"] == "git_file", "Only git_file resources are supported.")
        _repository(resource["repository"], "resource.repository")
        _hex(resource["commit"], 40, "resource.commit")
        _hex(resource["sha256"], 64, "resource.sha256")
        _path(resource["path"], "resource.path")
        _path(resource["destination"], "resource.destination")
        _require(resource["destination"] not in destinations, "Resource destinations must be unique.")
        destinations.add(resource["destination"])

    execution = task["execution"]
    _object(execution, {"compatible_agents", "timeout_seconds", "max_attempts"}, field="execution")
    _strings(execution["compatible_agents"], "execution.compatible_agents")
    agents = execution["compatible_agents"]
    _require(bool(agents) and all(agent in {"codex", "claude"} for agent in agents) and
             len(set(agents)) == len(agents), "Compatible agents must be unique supported providers.")
    _integer(execution["timeout_seconds"], "execution.timeout_seconds", minimum=1, maximum=14400)
    _integer(execution["max_attempts"], "execution.max_attempts", minimum=1, maximum=5)

    acceptance = task["acceptance"]
    _object(acceptance, {"commands", "required_outputs", "review_notes"}, field="acceptance")
    _list(acceptance["commands"], "acceptance.commands")
    for argv in acceptance["commands"]:
        _argv(argv, "acceptance command")
    _list(acceptance["required_outputs"], "acceptance.required_outputs")
    for name in acceptance["required_outputs"]:
        _path(name, "required output")
    _require(len(set(acceptance["required_outputs"])) == len(acceptance["required_outputs"]),
             "Required outputs must be unique.")
    _text(acceptance["review_notes"], "acceptance.review_notes", allow_empty=True)
    return copy.deepcopy(task)


def task_digest(task):
    return hashlib.sha256(_encoded(validate_task(task))).hexdigest()


def _result_shape(result):
    _bounded(result)
    _object(result, {"schema_version", "task_id", "revision", "task_digest", "attempt_id",
                     "base_commit", "summary", "artifacts", "verification", "assumptions", "unresolved"},
            {"usage"}, field="result")
    _integer(result["schema_version"], "result.schema_version", minimum=1, maximum=1)
    _uuid(result["task_id"], "result.task_id")
    _uuid(result["attempt_id"], "result.attempt_id")
    _integer(result["revision"], "result.revision", minimum=1)
    _hex(result["task_digest"], 64, "result.task_digest")
    _hex(result["base_commit"], 40, "result.base_commit")
    _text(result["summary"], "result.summary")
    _list(result["artifacts"], "result.artifacts")
    names = set()
    for artifact in result["artifacts"]:
        _object(artifact, {"name", "uri", "sha256"}, field="artifact")
        _path(artifact["name"], "artifact.name")
        _https_url(artifact["uri"], "artifact.uri")
        _hex(artifact["sha256"], 64, "artifact.sha256")
        _require(artifact["name"] not in names, "Artifact names must be unique.")
        names.add(artifact["name"])
    _list(result["verification"], "result.verification")
    for verification in result["verification"]:
        _object(verification, {"argv", "exit_code", "evidence"}, field="verification")
        _argv(verification["argv"], "verification.argv")
        _integer(verification["exit_code"], "verification.exit_code")
        _https_url(verification["evidence"], "verification.evidence")
    _strings(result["assumptions"], "result.assumptions")
    _strings(result["unresolved"], "result.unresolved")
    if result.get("usage") is not None:
        _require(type(result["usage"]) is dict, "result.usage must be an object or null.")
        _private_fields(result["usage"])


def validate_result(result, task, attempt_id):
    """Validate a result against the exact task content and current attempt."""
    task = validate_task(task)
    _uuid(attempt_id, "attempt_id")
    _result_shape(result)
    for field, expected, code in (
        ("task_id", task["task_id"], "TASK_MISMATCH"),
        ("revision", task["revision"], "REVISION_MISMATCH"),
        ("task_digest", task_digest(task), "DIGEST_MISMATCH"),
        ("attempt_id", attempt_id, "STALE_ATTEMPT"),
        ("base_commit", task["source"]["base_commit"], "BASE_COMMIT_MISMATCH"),
    ):
        _require(result[field] == expected, f"Result {field} does not match the current task attempt.", code)
    names = {artifact["name"] for artifact in result["artifacts"]}
    _require(set(task["acceptance"]["required_outputs"]) <= names,
             "Result is missing required artifacts.", "MISSING_ARTIFACT")
    return copy.deepcopy(result)


def _validate_command(command):
    _bounded(command)
    _require(type(command) is dict, "Command must be an object.")
    op = command.get("op")
    fields = {
        "claim": set(), "start": {"attempt_id"},
        "release": {"attempt_id", "reason"}, "submit": {"attempt_id", "result"},
        "accept": {"result_id"}, "reject": {"result_id", "reason"}, "cancel": set(),
    }
    _require(type(op) is str and op in fields, "Command operation is unsupported.")
    _object(command, {"op", "request_id", "revision"} | fields[op], field="command")
    _uuid(command["request_id"], "request_id")
    _integer(command["revision"], "revision", minimum=1)
    for field in ("attempt_id", "result_id"):
        if field in command:
            _uuid(command[field], field)
    if "reason" in command:
        _text(command["reason"], "reason")
    if op == "submit":
        _result_shape(command["result"])
    return copy.deepcopy(command)


def _envelope(marker, value):
    return f"{marker}\n```json\n{_canonical_json(value)}\n```"


def format_task_issue(task):
    return _envelope(TASK_MARKER, validate_task(task))


def format_command(command):
    body = _envelope(COMMAND_MARKER, _validate_command(command))
    _require(len(body.encode("utf-8")) <= MAX_PAYLOAD_BYTES,
             "Command comment exceeds the 48 KB UTF-8 limit.", "PAYLOAD_TOO_LARGE")
    return body


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "JSON objects must not contain duplicate keys.", "INVALID_JSON")
        result[key] = value
    return result


def _nonfinite(_):
    raise ProtocolError("INVALID_JSON", "JSON must not contain nonfinite numbers.")


def _parse_envelope(body, marker, *, optional=False):
    _text(body, "body", allow_empty=True)
    if marker not in body:
        if optional:
            return None
        raise ProtocolError("MISSING_TASK_MARKER", "Issue is missing the task marker.")
    try:
        size = len(body.encode("utf-8"))
    except UnicodeError as exc:
        raise ProtocolError("INVALID_JSON", "Body must contain valid UTF-8 text.") from exc
    limit = MAX_PAYLOAD_BYTES if optional else MAX_PAYLOAD_BYTES + 128
    _require(size <= limit, "Public record exceeds its UTF-8 size limit.", "PAYLOAD_TOO_LARGE")
    _require(body.count(marker) == 1, "A public record must contain one protocol marker.")
    pattern = rf"(?m)^{re.escape(marker)}[ \t]*\r?\n```json[ \t]*\r?\n(.*?)\r?\n```[ \t]*(?=\r?\n|$)"
    match = re.search(pattern, body, flags=re.DOTALL)
    _require(match is not None, "Protocol marker must be followed by a fenced JSON object.")
    try:
        payload = json.loads(match.group(1), object_pairs_hook=_unique_pairs, parse_constant=_nonfinite)
    except (ValueError, RecursionError) as exc:
        raise ProtocolError("INVALID_JSON", "Protocol record contains malformed JSON.") from exc
    _require(type(payload) is dict, "Protocol record must contain a JSON object.")
    return payload


def parse_task_issue(body):
    return validate_task(_parse_envelope(body, TASK_MARKER))


def parse_command(body):
    command = _parse_envelope(body, COMMAND_MARKER, optional=True)
    return None if command is None else _validate_command(command)
