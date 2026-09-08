"""Short-lived Actions controller; durable state lives in a GitHub branch."""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from .github import GitHub
from .protocol import ProtocolError, parse_command, parse_task_issue
from .state import apply_command, expire_task, new_task

STATUS_MARKER = "<!-- taskboard:status:v1 -->"


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
        }


def reconcile(api, *, now=None, allowed_members=()):
    """Drain retained commands, including those whose workflow was superseded.

    All invocations must use the workflow's shared concurrency group. State is
    committed before any derived Issue projection, so interrupted projections
    can be recreated without replaying a successful transition.
    """
    now = int(time.time()) if now is None else int(now)
    state = api.read_state()
    before = copy.deepcopy(state)
    permitted = {}
    roster = {name.casefold() for name in allowed_members}

    def authorized(login):
        normalized = login.lower()
        if normalized not in permitted:
            permitted[normalized] = (
                not roster or normalized in roster
            ) and api.permission(login) in {"write", "maintain", "admin"}
        return permitted[normalized]

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
            if issue.get("user", {}).get("type") == "Bot" or not authorized(author):
                continue
            try:
                spec = parse_task_issue(issue["body"])
                if spec["task_id"] in known_ids:
                    continue  # A duplicate publish never creates a second claimable task.
                record = new_task(issue, spec, now)
                known_ids[spec["task_id"]] = key
            except ProtocolError:
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
                    if comment.get("updated_at") != comment.get("created_at"):
                        _reject(
                            record,
                            command,
                            actor,
                            comment_id,
                            "EDITED_COMMAND",
                            "命令评论已修改，请发送新的请求。",
                        )
                    elif comment.get("user", {}).get("type") == "Bot" or not authorized(
                        actor
                    ):
                        _reject(
                            record,
                            command,
                            actor,
                            comment_id,
                            "NOT_AUTHORIZED",
                            "当前账号未被授权参与此看板。",
                        )
                    else:
                        record = apply_command(
                            record, command, actor=actor, comment_id=comment_id, now=now
                        )
            except ProtocolError as error:
                if error.code.startswith("GITHUB_") or error.code == "GH_NOT_INSTALLED":
                    # Infrastructure failure is not a business rejection. No
                    # cursor from this batch has been durably acknowledged.
                    raise
                _reject(record, command, "", comment_id, error.code, str(error))
            record["last_comment_id"] = comment_id
        state["tasks"][key] = record
        projection.append((issue, record, comments))
    if state != before:
        state["updated_at"] = now
        api.write_state(state)
    projection_errors = {}
    for issue, record, comments in projection:
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
                if comment.get("user", {}).get("login") == "github-actions[bot]"
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
    if state.get("projection_errors", {}) != projection_errors:
        state["projection_errors"] = projection_errors
        api.write_state(state)
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
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--hostname", default="github.com")
    parser.add_argument("--config", type=Path, default=Path(".github/taskboard.json"))
    parser.add_argument("--output", type=Path, default=Path("build/site"))
    parser.add_argument("--publish-pages", action="store_true")
    parser.add_argument("--request-pages-build", action="store_true")
    options = parser.parse_args(argv)
    try:
        config = (
            json.loads(options.config.read_text()) if options.config.exists() else {}
        )
        allowed = config.get("allowed_members", [])
        if not isinstance(allowed, list) or not all(
            isinstance(name, str) for name in allowed
        ):
            raise ProtocolError("INVALID_POLICY", "allowed_members 必须是账号名数组。")
        api = GitHub(options.repo, options.hostname)
        state = reconcile(api, allowed_members=allowed)
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
