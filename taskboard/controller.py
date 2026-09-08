"""Short-lived Actions controller; durable state lives in a GitHub branch."""

from __future__ import annotations

import argparse
import copy
import json
import logging
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from .download_assets import build_download_assets
from .github import GitHub
from .protocol import ProtocolError, _integer, _require, _text, _validate_command, parse_command, parse_task_issue, validate_publication
from .state import apply_command, expire_task, new_task
from .transfers import command_fingerprint, resolve_bundle

STATUS_MARKER = "<!-- taskboard:status:v1 -->"
CONTROLLER_LOGIN = "github-actions[bot]"


def _infrastructure_error(error):
    return (error.code.startswith("GITHUB_") or error.code in {
        "GH_NOT_INSTALLED", "ARTIFACT_UPLOAD_INCOMPLETE",
    })


def _reject(record, command, actor, comment_id, code, message):
    record.setdefault("command_errors", {})[str(comment_id)] = {
        "code": code,
        "message": message,
    }
    request_id = command.get("request_id") if isinstance(command, dict) else None
    if request_id and request_id not in record["processed"]:
        record["processed"][request_id] = {
            "ok": False,
            "code": code,
            "message": message,
            "comment_id": comment_id,
            "fingerprint": command_fingerprint(command, actor),
        }


def process_command(record, command, comments, api, *, actor, comment_id, now):
    """Resolve a bundle at the GitHub boundary, then use the pure task reducer.

    Raw request fingerprints fence retries before reading chunks or uploading.
    Transient transport errors leave the entire batch retryable.
    """
    command = _validate_command(command)
    _text(actor, "actor")
    _integer(comment_id, "comment_id", minimum=1)
    current = expire_task(record, now)
    fingerprint = command_fingerprint(command, actor)
    previous = record["processed"].get(command["request_id"])
    if previous is not None:
        _require(previous.get("raw_fingerprint", previous.get("fingerprint")) == fingerprint,
                 "Request ID was already used for different content or actor.", "IDEMPOTENCY_CONFLICT")
        return copy.deepcopy(record)
    try:
        resolved = (resolve_bundle(current, command, comments, api, actor=actor, now=now)
                    if command["op"] == "submit_bundle" else command)
        result = apply_command(current, resolved, actor=actor, comment_id=comment_id, now=now)
    except ProtocolError as error:
        if _infrastructure_error(error) or error.code == "IDEMPOTENCY_CONFLICT":
            raise
        _reject(current, command, actor, comment_id, error.code, str(error))
        current["updated_at"] = now
        return current
    if command["op"] == "submit_bundle":
        result["processed"][command["request_id"]]["raw_fingerprint"] = fingerprint
    return result


def _own_bot(comment):
    user = comment.get("user") or {}
    return user.get("type") == "Bot" and user.get("login") == CONTROLLER_LOGIN


def _notification(record, comments, api, now):
    if record["status"] != "submitted" or not record.get("result"):
        return
    result = record["result"]
    receipts = record.setdefault("result_notifications", {})
    if result["id"] in receipts:
        return
    marker = f"<!-- taskboard:result:v2:{result['id']} -->"
    existing = next((comment for comment in comments if _own_bot(comment) and
                     (comment.get("body") or "").startswith(marker + "\n")), None)
    if existing is None:
        lines = [marker, f"@{record['author']} @{record['attempt']['actor']} 成果已提交，待发起者验收。",
                 f"成果编号：`{result['id']}`"]
        for artifact in result["manifest"]["artifacts"]:
            label = re.sub(r"([\\`*_{}\[\]()#+.!|<>])", r"\\\1", artifact["name"]).replace("@", "&#64;")
            uri = artifact["uri"].replace("<", "%3C").replace(">", "%3E")
            lines.append(f"- [{label}](<{uri}>)")
        lines.append("发起者可在原 Agent 会话中读取成果并决定是否验收；提交不等于自动合并或验收。")
        existing = api.comment(record["number"], "\n\n".join(lines))
        _require(_own_bot(existing), "Notification response must identify the controller bot.",
                 "GITHUB_INVALID_RESPONSE")
    _integer(existing.get("id"), "notification comment ID", minimum=1)
    receipts[result["id"]] = {"comment_id": existing["id"], "notified_at": now}


def reconcile(api, *, now=None, allowed_members=()):
    """Drain retained commands, including those whose workflow was superseded.

    All invocations must use the workflow's shared concurrency group. State is
    committed before any derived Issue projection, so interrupted projections
    can be recreated without replaying a successful transition.
    """
    now = int(time.time()) if now is None else int(now)
    state = api.read_state()
    before = copy.deepcopy(state)
    # Legacy callers may still pass allowed_members; native GitHub Issue and
    # comment access now authorizes participation without an application roster.

    projection = []
    known_ids = {
        record["spec"]["task_id"]: key for key, record in state["tasks"].items()
    }
    for issue in api.issues():
        key = str(issue["number"])
        record = state["tasks"].get(key)
        if record is None:
            if "<!-- taskboard:task:v1 -->" not in (issue.get("body") or ""):
                continue
            author = issue.get("user", {}).get("login", "")
            if issue.get("user", {}).get("type") != "User" or not author:
                continue
            try:
                spec = validate_publication(parse_task_issue(issue["body"]))
                if spec["task_id"] in known_ids:
                    continue  # A duplicate publish never creates a second claimable task.
                record = new_task(issue, spec, now)
                known_ids[spec["task_id"]] = key
            except ProtocolError as error:
                logging.getLogger(__name__).warning('Issue #%s was not admitted (%s): %s', issue['number'], error.code, error.message)
                continue
        record = expire_task(record, now)
        comments = sorted(api.comments(issue["number"]), key=lambda item: item["id"])
        cursor = record.get("last_comment_id", 0)
        for comment in comments:
            comment_id = comment["id"]
            if comment_id <= cursor:
                continue
            command = None
            try:
                command = parse_command(comment.get("body") or "")
                if command is not None:
                    actor = comment.get("user", {}).get("login", "")
                    if not comment.get("created_at") or comment.get("updated_at") != comment.get("created_at"):
                        _reject(
                            record,
                            command,
                            actor,
                            comment_id,
                            "EDITED_COMMAND",
                            "命令评论已修改，请发送新的请求。",
                        )
                    elif comment.get("user", {}).get("type") != "User" or not actor:
                        _reject(
                            record,
                            command,
                            actor,
                            comment_id,
                            "NOT_AUTHORIZED",
                            "命令必须由 GitHub 已认证的用户账号发送。",
                        )
                    else:
                        record = process_command(
                            record, command, comments, api, actor=actor, comment_id=comment_id, now=now
                        )
            except ProtocolError as error:
                if _infrastructure_error(error):
                    # Infrastructure failure is not a business rejection. No
                    # cursor from this batch has been durably acknowledged.
                    raise
                _reject(record, command, comment.get("user", {}).get("login", ""),
                        comment_id, error.code, str(error))
            record["last_comment_id"] = comment_id
        state["tasks"][key] = record
        projection.append((issue, record, comments))
    if state != before:
        state["updated_at"] = now
        api.write_state(state)
    canonical = copy.deepcopy(state)
    projection_errors = {}
    notification_errors = {}
    for issue, record, comments in projection:
        try:
            _notification(record, comments, api, now)
        except ProtocolError as error:
            notification_errors[str(issue["number"])] = {"code": error.code, "message": str(error)}
        attempt = record.get("attempt")
        last = sorted(
            record.get("processed", {}).values(),
            key=lambda item: item.get("comment_id", 0),
        )[-1:]
        lines = [
            STATUS_MARKER,
            "### Agent Task Board",
            f"状态：**{record['status']}** · 版本：{record['spec']['revision']}",
        ]
        if attempt:
            lines.append(f"当前认领：@{attempt['actor']} · 执行编号：`{attempt['id']}`")
        if record.get("result"):
            lines.append(
                f"成果编号：`{record['result']['id']}` · 提交后仍需发起者验收。"
            )
        if last:
            lines.append(
                f"最近请求：{last[0].get('code', 'OK')} — {last[0].get('message', '')}"
            )
        lines.append(
            "此状态由 Actions 从已确认的任务记录生成。执行前请由本地工具重新确认认领。"
        )
        body = "\n\n".join(lines)
        existing = next(
            (
                comment
                for comment in comments
                if _own_bot(comment)
                and (comment.get("body") or "").startswith(STATUS_MARKER)
            ),
            None,
        )
        if existing and existing.get("body") == body:
            continue
        try:
            if existing:
                api.request(
                    "PATCH",
                    f"repos/{api.repo}/issues/comments/{existing['id']}",
                    {"body": body},
                )
            else:
                api.comment(issue["number"], body)
        except ProtocolError as error:
            projection_errors[str(issue["number"])] = {
                "code": error.code,
                "message": str(error),
            }
    state["projection_errors"] = projection_errors
    state["notification_errors"] = notification_errors
    if state != canonical:
        try:
            api.write_state(state)
        except ProtocolError as error:
            if not _infrastructure_error(error):
                raise
            # Canonical transitions were already persisted. A notification or
            # projection receipt can be recovered from the next native read.
            state["projection_errors"]["state"] = {"code": error.code, "message": str(error)}
    return state


def snapshot(state, repository, hostname, *, now=None):
    now = int(time.time()) if now is None else now
    fields = (
        "number",
        "url",
        "author",
        "created_at",
        "spec",
        "digest",
        "status",
        "attempt_count",
        "attempt",
        "result",
        "updated_at",
    )
    tasks = [
        {key: copy.deepcopy(record.get(key)) for key in fields}
        for record in state["tasks"].values()
    ]
    tasks.sort(key=lambda task: task["number"], reverse=True)
    return {
        "schema_version": 1,
        "repository": repository,
        "hostname": hostname,
        "generated_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "demo": False,
        "tasks": tasks,
    }


def build_site(state, repository, hostname, destination, *, source=Path("site")):
    destination, source = Path(destination).resolve(), Path(source).resolve()
    if destination == source or source in destination.parents:
        raise ProtocolError(
            "INVALID_SITE_DESTINATION", "生成目录必须独立于站点源目录。"
        )
    destination.mkdir(parents=True, exist_ok=True)
    for file in source.rglob("*"):
        if file.is_symlink():
            raise ProtocolError("SYMLINK_IN_SITE", "站点源目录不能包含符号链接。")
        if file.is_file():
            target = destination / file.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file, target)
    (destination / "tasks.json").write_text(
        json.dumps(snapshot(state, repository, hostname), ensure_ascii=False, indent=2)
        + "\n"
    )
    (destination / ".nojekyll").touch()
    build_download_assets(destination, Path(__file__).resolve().parent.parent)
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--hostname", default="github.com")
    parser.add_argument("--config", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path, default=Path("build/site"))
    parser.add_argument("--publish-pages", action="store_true")
    parser.add_argument("--request-pages-build", action="store_true")
    options = parser.parse_args(argv)
    try:
        api = GitHub(options.repo, options.hostname)
        state = reconcile(api)
        build_site(state, api.repo, api.hostname, options.output)
        if options.publish_pages:
            api.publish_directory(options.output)
        if options.request_pages_build:
            api.request("POST", f"repos/{api.repo}/pages/builds", {})
        print(
            json.dumps(
                {
                    "tasks": len(state["tasks"]),
                    "site": str(options.output),
                    "pages_published": options.publish_pages,
                    "projection_errors": state.get("projection_errors", {}),
                    "notification_errors": state.get("notification_errors", {}),
                },
                ensure_ascii=False,
            )
        )
        return 0
    except (ProtocolError, OSError, ValueError) as error:
        import sys

        print(
            json.dumps(
                {
                    "error": {
                        "code": getattr(error, "code", "CONTROLLER_FAILED"),
                        "message": str(error),
                    }
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
