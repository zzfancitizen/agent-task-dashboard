"""Bounded Issue-comment transport; only the controller uploads Release assets.

Archives contain reported regular files only. They are validated and copied to
controller-generated names, never extracted by archive paths or executed.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import os
import stat
import tempfile
import uuid
import zipfile
import zlib
from pathlib import Path
from urllib.parse import urlsplit

from .local import regular_path
from .protocol import (
    CHUNK_BYTES, MAX_ARTIFACT_BYTES, MAX_ARTIFACT_TOTAL_BYTES, MAX_BUNDLE_BYTES,
    MAX_CHUNKS, ProtocolError, _canonical_json, _integer, _object, _path, _require,
    _text, _uuid, _validate_command, format_artifact_chunk, format_command,
    parse_artifact_chunk, task_digest, validate_bundle_report, validate_result, validate_task,
)
from .state import expire_task


def command_fingerprint(command, actor):
    return hashlib.sha256(_canonical_json({
        "actor": actor.casefold(), "command": command,
    }).encode("utf-8")).hexdigest()


def _read_file(path):
    path = Path(path).absolute()
    regular_path(path)
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
             | getattr(os, "O_NONBLOCK", 0))
    try:
        _require(stat.S_ISREG(path.lstat().st_mode), "Artifact must be a regular file.", "INVALID_ARTIFACT")
        with os.fdopen(os.open(path, flags), "rb") as stream:
            info = os.fstat(stream.fileno())
            _require(stat.S_ISREG(info.st_mode) and info.st_size <= MAX_ARTIFACT_BYTES,
                     "Artifacts must be regular files of at most 20 MiB.", "INVALID_ARTIFACT")
            data = stream.read(MAX_ARTIFACT_BYTES + 1)
            _require(len(data) <= MAX_ARTIFACT_BYTES, "Artifact exceeds 20 MiB.", "INVALID_ARTIFACT")
            _require(len(data) == info.st_size, "Artifact changed while reading.", "ARTIFACT_CHANGED")
            return data
    except OSError as error:
        raise ProtocolError("INVALID_ARTIFACT", f"Cannot read regular artifact: {path.name}") from error


def _verification_matches(report, contents):
    if not report["verification"]:
        return
    try:
        evidence = json.loads(contents["verification.json"].decode("utf-8"))
        checks = evidence["commands"]
        _require(type(checks) is list, "Verification evidence must contain a commands list.")
        typed = [{"argv": check["argv"], "exit_code": check["exit_code"]} for check in checks]
        _require(all(type(item["exit_code"]) is int for item in typed),
                 "Verification exit codes must be integers.")
        _require(typed == report["verification"], "Verification evidence differs from reported checks.",
                 "VERIFICATION_MISMATCH")
    except (KeyError, TypeError, ValueError, RecursionError, UnicodeError) as error:
        if isinstance(error, ProtocolError):
            raise
        raise ProtocolError("INVALID_VERIFICATION", "verification.json must contain the reported checks.") from error


def prepare_bundle(task: dict, attempt_id: str, files: dict[str, Path], report: dict) -> dict:
    task = validate_task(task)
    _uuid(attempt_id, "attempt_id")
    _require(type(files) is dict, "Artifact files must be a logical-name map.")
    _object(report, {"summary"}, {"assumptions", "unresolved", "usage", "checks"}, field="report input")
    contents, artifacts, total = {}, [], 0
    for name, path in sorted(files.items()):
        _path(name, "artifact.name")
        data = _read_file(path)
        total += len(data)
        _require(total <= MAX_ARTIFACT_TOTAL_BYTES, "Artifacts exceed 100 MiB.", "ARTIFACTS_TOO_LARGE")
        contents[name] = data
        artifacts.append({"name": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    checks = report.get("checks", [])
    _require(type(checks) is list and all(type(check) is dict and
             {"argv", "exit_code"} <= check.keys() for check in checks),
             "Checks must contain argv and exit_code records.")
    metadata = validate_bundle_report({
        "schema_version": 1, "task_id": task["task_id"], "task_digest": task_digest(task),
        "base_commit": task["source"]["base_commit"], "summary": report["summary"],
        "assumptions": report.get("assumptions", []), "unresolved": report.get("unresolved", []),
        "usage": report.get("usage"), "artifacts": artifacts,
        "verification": [{"argv": check["argv"], "exit_code": check["exit_code"]} for check in checks],
    }, task)
    _verification_matches(metadata, contents)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in contents.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data, compresslevel=9)
            _require(buffer.tell() <= MAX_BUNDLE_BYTES,
                     "Compressed report exceeds 1 MiB. Keep large outputs locally and submit a smaller report.",
                     "BUNDLE_TOO_LARGE")
    data = buffer.getvalue()
    _require(len(data) <= MAX_BUNDLE_BYTES,
             "Compressed report exceeds 1 MiB. Keep large outputs locally and submit a smaller report.",
             "BUNDLE_TOO_LARGE")
    sha256 = hashlib.sha256(data).hexdigest()
    identity = _canonical_json([task["revision"], attempt_id, sha256, metadata])
    upload_id = str(uuid.uuid5(uuid.UUID(task["task_id"]), identity))
    # Reserve enough envelope space for every returned GitHub comment ID before
    # posting the first chunk, so an oversized report cannot strand an upload.
    format_command({"op": "submit_bundle", "request_id": str(uuid.uuid4()),
                    "revision": task["revision"], "attempt_id": attempt_id,
                    "upload": {"id": upload_id, "sha256": sha256, "bytes": len(data),
                               "comment_ids": list(range(10**19, 10**19 + (len(data) + CHUNK_BYTES - 1) // CHUNK_BYTES))},
                    "report": metadata})
    return {"upload_id": upload_id, "sha256": sha256, "data": data, "report": metadata}


def _unedited_actor(comment, actor):
    user = comment.get("user") or {}
    return (type(user.get("login")) is str and user["login"].casefold() == actor.casefold()
            and user.get("type") == "User" and bool(comment.get("created_at"))
            and comment.get("created_at") == comment.get("updated_at"))


def _same_issue(comment, api, issue):
    # The comments endpoint itself is Issue-scoped. Check the native locator as
    # well when provided, including in fixtures and retained-event adapters.
    if not comment.get("issue_url"):
        return True
    parsed = urlsplit(comment["issue_url"])
    hosts = {api.hostname}
    if api.hostname == "github.com":
        hosts.add("api.github.com")
    endpoint = f"/repos/{api.repo}/issues/{issue}"
    return (parsed.scheme == "https" and parsed.hostname in hosts and
            parsed.path in {endpoint, "/api/v3" + endpoint} and not parsed.query and
            not parsed.fragment and not parsed.username and not parsed.password)


def post_bundle_chunks(api, issue: int, task: dict, attempt_id: str, bundle: dict) -> dict:
    task = validate_task(task)
    _uuid(attempt_id, "attempt_id")
    _uuid(bundle.get("upload_id"), "upload_id")
    _integer(issue, "issue", minimum=1)
    data = bundle.get("data")
    _require(type(data) is bytes and 0 < len(data) <= MAX_BUNDLE_BYTES,
             "Compressed report must be at most 1 MiB.", "BUNDLE_TOO_LARGE")
    _require(hashlib.sha256(data).hexdigest() == bundle.get("sha256"),
             "Bundle digest does not match its bytes.", "ARTIFACT_HASH_MISMATCH")
    validate_bundle_report(bundle["report"], task)
    total = (len(data) + CHUNK_BYTES - 1) // CHUNK_BYTES
    _require(total <= MAX_CHUNKS, "Upload requires too many chunks.", "BUNDLE_TOO_LARGE")
    provisional = {"op": "submit_bundle", "request_id": str(uuid.uuid4()),
                   "revision": task["revision"], "attempt_id": attempt_id,
                   "upload": {"id": bundle["upload_id"], "sha256": bundle["sha256"], "bytes": len(data),
                              "comment_ids": list(range(10**19, 10**19 + total))},
                   "report": bundle["report"]}
    format_command(provisional)
    _, _, expected_urls = _release_targets(task, provisional, api)
    format_command(_submit_manifest(provisional, bundle["report"], expected_urls))
    actor = api.user()
    _text(actor, "GitHub actor")
    retained = {}
    for comment in sorted(api.comments(issue), key=lambda item: item["id"]):
        if not _unedited_actor(comment, actor) or not _same_issue(comment, api, issue):
            continue
        try:
            chunk = parse_artifact_chunk(comment.get("body") or "")
        except ProtocolError:
            continue
        if chunk and chunk["upload_id"] == bundle["upload_id"]:
            retained.setdefault(chunk["index"], []).append((comment, chunk))
    ids = []
    for index in range(total):
        chunk = {"upload_id": bundle["upload_id"], "task_id": task["task_id"],
                 "revision": task["revision"], "task_digest": task_digest(task),
                 "attempt_id": attempt_id, "index": index, "total": total,
                 "sha256": bundle["sha256"],
                 "data": base64.b64encode(data[index * CHUNK_BYTES:(index + 1) * CHUNK_BYTES]).decode("ascii")}
        existing = retained.get(index, [])
        _require(all(value == chunk for _, value in existing),
                 "Retained upload part differs from this bundle.", "ARTIFACT_CONFLICT")
        comment = existing[0][0] if existing else api.comment(issue, format_artifact_chunk(chunk))
        _integer(comment.get("id"), "GitHub comment ID", minimum=1)
        ids.append(comment["id"])
    return {"id": bundle["upload_id"], "sha256": bundle["sha256"],
            "bytes": len(data), "comment_ids": ids}


def _active_attempt(record, command, actor, now):
    current = expire_task(record, now)
    _require(command["revision"] == current["spec"]["revision"],
             "Command does not match the pinned revision.", "REVISION_MISMATCH")
    attempt = current["attempt"]
    _require(attempt is not None and attempt["id"] == command["attempt_id"],
             "Command does not identify a current unexpired attempt.", "STALE_ATTEMPT")
    _require(actor.casefold() == attempt["actor"].casefold(),
             "Only the current attempt owner can submit.", "UNAUTHORIZED")
    _require(current["status"] in {"claimed", "running"},
             "Task has no active execution attempt.", "INVALID_STATE")
    return current


def _archive_contents(data, report):
    expected = {item["name"]: item for item in report["artifacts"]}
    contents = {}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            names = [item.filename for item in entries]
            _require(len(names) == len(set(names)) and set(names) == set(expected),
                     "ZIP entries must exactly match the reported artifacts.", "INVALID_ARCHIVE")
            total = 0
            for item in entries:
                _path(item.filename, "ZIP path")
                _require(item.orig_filename == item.filename,
                         "ZIP filenames must not contain hidden NUL suffixes.", "INVALID_ARCHIVE")
                mode = item.external_attr >> 16
                _require(not item.is_dir() and stat.S_IFMT(mode) in {0, stat.S_IFREG}
                         and not (item.external_attr & 0x10)
                         and not (item.flag_bits & 1) and
                         item.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED},
                         "ZIP entries must be unencrypted regular files.", "INVALID_ARCHIVE")
                _require(item.file_size <= MAX_ARTIFACT_BYTES and
                         item.file_size == expected[item.filename]["bytes"],
                         "ZIP entry size exceeds its declared bounded size.", "INVALID_ARCHIVE")
                total += item.file_size
                _require(total <= MAX_ARTIFACT_TOTAL_BYTES, "ZIP expands beyond 100 MiB.", "INVALID_ARCHIVE")
                with archive.open(item) as stream:
                    value = stream.read(item.file_size + 1)
                _require(len(value) == item.file_size,
                         "ZIP entry has inconsistent size.", "INVALID_ARCHIVE")
                _require(hashlib.sha256(value).hexdigest() == expected[item.filename]["sha256"].lower(),
                         "Artifact hash does not match report.", "ARTIFACT_HASH_MISMATCH")
                contents[item.filename] = value
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError, EOFError,
            zlib.error, ValueError, OverflowError) as error:
        if isinstance(error, ProtocolError):
            raise
        raise ProtocolError("INVALID_ARCHIVE", "Artifact ZIP is malformed or unsupported.") from error
    _verification_matches(report, contents)
    return contents


def _submit_manifest(command, report, urls):
    return {"op": "submit", "request_id": command["request_id"], "revision": command["revision"],
            "attempt_id": command["attempt_id"], "result": {
                "schema_version": 1, "task_id": report["task_id"], "revision": command["revision"],
                "task_digest": report["task_digest"], "base_commit": report["base_commit"],
                "attempt_id": command["attempt_id"], "summary": report["summary"],
                "assumptions": report["assumptions"], "unresolved": report["unresolved"],
                "usage": report["usage"],
                "artifacts": [{"name": item["name"], "sha256": item["sha256"], "uri": urls[item["name"]]}
                              for item in report["artifacts"]],
                "verification": [{**check, "evidence": urls["verification.json"]}
                                 for check in report["verification"]],
            }}


def _release_targets(task, command, api):
    tag = f"taskboard-{task['task_id']}-{command['attempt_id']}-{command['upload']['id']}"
    basenames = {item["name"]: hashlib.sha256(item["name"].encode("utf-8")).hexdigest() + ".bin"
                 for item in command["report"]["artifacts"]}
    urls = {name: f"https://{api.hostname}/{api.repo}/releases/download/{tag}/{basename}"
            for name, basename in basenames.items()}
    return tag, basenames, urls


def resolve_bundle(record: dict, command: dict, comments: list[dict], api, *, actor: str, now: int) -> dict:
    command = _validate_command(command)
    _require(command["op"] == "submit_bundle", "Expected submit_bundle command.")
    _text(actor, "actor")
    current = expire_task(record, now)
    previous = record["processed"].get(command["request_id"])
    if previous is not None:
        _require(previous.get("raw_fingerprint", previous.get("fingerprint")) ==
                 command_fingerprint(command, actor),
                 "Request ID was used for different content or actor.", "IDEMPOTENCY_CONFLICT")
        _require(previous["ok"], previous.get("message", "Submission was rejected."), previous["code"])
        result_id = str(uuid.uuid5(uuid.UUID(record["spec"]["task_id"]),
                                  f"{record['spec']['revision']}:result:{command['request_id']}"))
        result = next((item for item in [record.get("result"), *record.get("result_history", [])]
                       if item and item["id"] == result_id), None)
        _require(result is not None, "Stored result is unavailable.", "INVALID_BOARD_STATE")
        return {"op": "submit", "request_id": command["request_id"], "revision": command["revision"],
                "attempt_id": command["attempt_id"], "result": copy.deepcopy(result["manifest"])}
    current = _active_attempt(current, command, actor, now)
    report = validate_bundle_report(command["report"], current["spec"])
    upload = command["upload"]
    tag, basenames, expected_urls = _release_targets(current["spec"], command, api)
    anticipated = _submit_manifest(command, report, expected_urls)
    validate_result(anticipated["result"], current["spec"], command["attempt_id"])
    format_command(anticipated)
    total = (upload["bytes"] + CHUNK_BYTES - 1) // CHUNK_BYTES
    _require(len(upload["comment_ids"]) == total, "Upload chunk count does not match its size.", "INVALID_CHUNK")
    selected = set(upload["comment_ids"])
    rows = [comment for comment in comments if comment.get("id") in selected]
    _require(len(rows) == total and len({row["id"] for row in rows}) == total,
             "Upload references missing or duplicate comments.", "MISSING_CHUNK")
    parts = {}
    for row in rows:
        _require(_unedited_actor(row, actor) and _same_issue(row, api, current["number"]),
                 "Artifact chunks must be unedited comments by the submitting actor on this Issue.", "UNTRUSTED_CHUNK")
        chunk = parse_artifact_chunk(row.get("body") or "")
        _require(chunk is not None, "Upload comment is not an artifact chunk.", "INVALID_CHUNK")
        for key, value in {"upload_id": upload["id"], "task_id": current["spec"]["task_id"],
                           "revision": current["spec"]["revision"], "task_digest": current["digest"],
                           "attempt_id": command["attempt_id"], "total": total, "sha256": upload["sha256"]}.items():
            _require(chunk[key] == value, f"Artifact chunk {key} does not match submission.", "CHUNK_MISMATCH")
        _require(chunk["index"] not in parts, "Artifact chunk index is duplicated.", "INVALID_CHUNK")
        part = base64.b64decode(chunk["data"], validate=True)
        expected_size = min(CHUNK_BYTES, upload["bytes"] - chunk["index"] * CHUNK_BYTES)
        _require(len(part) == expected_size, "Artifact chunk has an inconsistent size.", "INVALID_CHUNK")
        parts[chunk["index"]] = part
    data = b"".join(parts[index] for index in range(total))
    _require(len(data) == upload["bytes"] and hashlib.sha256(data).hexdigest() == upload["sha256"].lower(),
             "Assembled ZIP digest does not match submission.", "ARTIFACT_HASH_MISMATCH")
    contents = _archive_contents(data, report)
    with tempfile.TemporaryDirectory(prefix="taskboard-artifacts-") as directory:
        files = {}
        for name, value in sorted(contents.items()):
            path = Path(directory) / basenames[name]
            path.write_bytes(value)
            files[name] = path
        urls = api.upload_artifacts(tag, list(files.values()))
        _require(type(urls) is dict and all(file.name in urls for file in files.values()),
                 "Some artifact uploads are not confirmed.", "ARTIFACT_UPLOAD_INCOMPLETE")
        resolved = _submit_manifest(command, report, {name: urls[file.name] for name, file in files.items()})
    validate_result(resolved["result"], current["spec"], command["attempt_id"])
    format_command(resolved)
    return resolved
