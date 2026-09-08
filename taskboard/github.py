"""GitHub transport backed by the user's existing gh authentication."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from .protocol import ProtocolError

STATE_BRANCH = "taskboard-state"
STATE_PATH = "state.json"


class GitHub:
    def __init__(self, repo: str, hostname: str = "github.com"):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo or "") or any(
            p in {".", ".."} for p in repo.split("/")
        ):
            raise ProtocolError("INVALID_REPOSITORY", "仓库必须是 owner/repo。")
        if (
            not re.fullmatch(
                r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", hostname or ""
            )
            or ".." in hostname
        ):
            raise ProtocolError(
                "INVALID_HOSTNAME", "请输入 GitHub 域名，不含协议或路径。"
            )
        self.repo, self.hostname = repo, hostname.lower()
        self._state_sha = None
        self._state_loaded = False

    def _run(self, argv, *, data=None, binary=False):
        try:
            result = subprocess.run(
                argv, input=data, text=not binary, capture_output=True, timeout=120
            )
        except FileNotFoundError as error:
            raise ProtocolError(
                "GH_NOT_INSTALLED", "请先安装 GitHub CLI（gh）。"
            ) from error
        except subprocess.TimeoutExpired as error:
            raise ProtocolError(
                "GITHUB_OUTCOME_UNKNOWN", "GitHub 请求超时；写入结果需要核对后重试。"
            ) from error
        if result.returncode:
            stderr = result.stderr.decode(errors="replace") if binary else result.stderr
            match = re.search(r"HTTP\s+(\d{3})", stderr or "")
            code = f"GITHUB_{match.group(1)}" if match else "GITHUB_REQUEST_FAILED"
            raise ProtocolError(
                code,
                f"GitHub 请求失败；请检查 {self.hostname} 的 gh 登录、权限和网络。",
            )
        return result.stdout

    def request(self, method: str, path: str, body: dict | None = None):
        if path.startswith(("/", "-")) or "://" in path:
            raise ProtocolError(
                "INVALID_API_PATH", "API 路径必须相对于当前 GitHub 主机。"
            )
        args = ["gh", "api", "--hostname", self.hostname, "--method", method, path]
        data = None
        if body is not None:
            args.extend(["--input", "-"])
            data = json.dumps(body, ensure_ascii=False)
        output = self._run(args, data=data)
        try:
            return json.loads(output) if output.strip() else {}
        except json.JSONDecodeError as error:
            raise ProtocolError(
                "GITHUB_INVALID_RESPONSE", "GitHub 返回了无法解析的数据。"
            ) from error

    def _optional(self, path):
        try:
            return self.request("GET", path)
        except ProtocolError as error:
            if error.code == "GITHUB_404":
                return None
            raise

    def _list(self, path):
        values, page = [], 1
        while True:
            separator = "&" if "?" in path else "?"
            result = self.request("GET", f"{path}{separator}per_page=100&page={page}")
            if not isinstance(result, list):
                raise ProtocolError(
                    "GITHUB_INVALID_RESPONSE", "GitHub 列表响应格式错误。"
                )
            values.extend(result)
            if len(result) < 100:
                return values
            page += 1

    def user(self):
        return self.request("GET", "user")["login"]

    def issue(self, number):
        return self.request("GET", f"repos/{self.repo}/issues/{int(number)}")

    def issues(self):
        return [
            issue
            for issue in self._list(
                f"repos/{self.repo}/issues?state=all&sort=created&direction=asc"
            )
            if "pull_request" not in issue
        ]

    def comments(self, number):
        return self._list(f"repos/{self.repo}/issues/{int(number)}/comments")

    def create_issue(self, title, body):
        return self.request(
            "POST", f"repos/{self.repo}/issues", {"title": title, "body": body}
        )

    def comment(self, number, body):
        return self.request(
            "POST", f"repos/{self.repo}/issues/{int(number)}/comments", {"body": body}
        )

    def permission(self, actor):
        response = self.request(
            "GET", f"repos/{self.repo}/collaborators/{quote(actor, safe='')}/permission"
        )
        return response.get("permission", "none")

    def read_state(self):
        blob = self._optional(
            f"repos/{self.repo}/contents/{STATE_PATH}?ref={STATE_BRANCH}"
        )
        self._state_loaded = True
        if blob is None:
            self._state_sha = None
            return {"schema_version": 1, "tasks": {}, "updated_at": 0}
        self._state_sha = blob["sha"]
        if blob.get("encoding") == "none":
            blob = self.request("GET", f"repos/{self.repo}/git/blobs/{self._state_sha}")
        try:
            state = json.loads(base64.b64decode(blob["content"]))
            if state.get("schema_version") != 1 or not isinstance(
                state.get("tasks"), dict
            ):
                raise ValueError("unsupported state")
            return state
        except (KeyError, ValueError, TypeError) as error:
            raise ProtocolError(
                "INVALID_BOARD_STATE", "仓库状态损坏或版本不受支持；不会覆盖原文件。"
            ) from error

    def write_state(self, state):
        if not self._state_loaded:
            raise ProtocolError("STATE_NOT_READ", "更新前必须读取原状态。")
        if self._state_sha is None:
            branch = self._optional(f"repos/{self.repo}/git/ref/heads/{STATE_BRANCH}")
            if branch is None:
                repo = self.request("GET", f"repos/{self.repo}")
                head = self.request(
                    "GET",
                    f"repos/{self.repo}/git/ref/heads/{quote(repo['default_branch'], safe='')}",
                )
                self.request(
                    "POST",
                    f"repos/{self.repo}/git/refs",
                    {"ref": f"refs/heads/{STATE_BRANCH}", "sha": head["object"]["sha"]},
                )
        payload = {
            "message": "Preserve acknowledged task transitions for recoverable delegation\n\nScope-risk: narrow\nTested: Controller protocol validation",
            "content": base64.b64encode(
                json.dumps(
                    state, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode()
            ).decode(),
            "branch": STATE_BRANCH,
        }
        if self._state_sha:
            payload["sha"] = self._state_sha
        response = self.request(
            "PUT", f"repos/{self.repo}/contents/{STATE_PATH}", payload
        )
        self._state_sha = response["content"]["sha"]

    def upload_artifacts(self, tag, files):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", tag):
            raise ProtocolError("INVALID_ARTIFACT_TAG", "产物 tag 不合法。")
        files = [Path(file) for file in files]
        if len({file.name for file in files}) != len(files):
            raise ProtocolError("DUPLICATE_ARTIFACT", "产物文件名重复。")
        for file in files:
            if (
                not file.is_file()
                or file.is_symlink()
                or file.stat().st_size > 20 * 1024 * 1024
            ):
                raise ProtocolError(
                    "INVALID_ARTIFACT", "产物必须为不超过 20 MiB 的普通文件。"
                )
        if sum(file.stat().st_size for file in files) > 100 * 1024 * 1024:
            raise ProtocolError("ARTIFACTS_TOO_LARGE", "产物合计不得超过 100 MiB。")
        path = f"repos/{self.repo}/releases/tags/{quote(tag, safe='')}"
        release = self._optional(path)
        if release is None:
            release = self.request(
                "POST",
                f"repos/{self.repo}/releases",
                {
                    "tag_name": tag,
                    "name": tag,
                    "body": "Agent Task Board execution artifacts",
                    "make_latest": "false",
                },
            )
        existing = {asset["name"]: asset for asset in release.get("assets", [])}
        for file in files:
            if file.name in existing:
                with tempfile.TemporaryDirectory() as directory:
                    downloaded = Path(directory) / "asset"
                    self.download(
                        existing[file.name]["browser_download_url"], downloaded
                    )
                    if (
                        hashlib.sha256(downloaded.read_bytes()).digest()
                        != hashlib.sha256(file.read_bytes()).digest()
                    ):
                        raise ProtocolError(
                            "ARTIFACT_CONFLICT", "已上传的同名产物不同；不会覆盖。"
                        )
            else:
                self._run(
                    [
                        "gh",
                        "release",
                        "upload",
                        tag,
                        str(file.resolve()),
                        "--repo",
                        f"{self.hostname}/{self.repo}",
                    ]
                )
        final = self.request("GET", path)
        urls = {
            asset["name"]: asset["browser_download_url"]
            for asset in final.get("assets", [])
        }
        if not all(file.name in urls for file in files):
            raise ProtocolError(
                "ARTIFACT_UPLOAD_INCOMPLETE", "部分产物上传未确认，请核对后重试。"
            )
        return {file.name: urls[file.name] for file in files}

    def download(self, url, destination):
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != self.hostname
            or parsed.username
            or parsed.password
            or parsed.port
            or parsed.query
            or parsed.fragment
        ):
            raise ProtocolError(
                "INVALID_ARTIFACT_URL", "只能读取当前 GitHub 主机的成果链接。"
            )
        parts = [unquote(part) for part in parsed.path.strip("/").split("/")]
        if (
            len(parts) != 6
            or "/".join(parts[:2]).casefold() != self.repo.casefold()
            or parts[2:4] != ["releases", "download"]
        ):
            raise ProtocolError(
                "INVALID_ARTIFACT_URL", "成果必须是任务仓库的 Release 附件。"
            )
        tag, name = parts[4:]
        if any(not part or "/" in part or part in {".", ".."} for part in (tag, name)):
            raise ProtocolError("INVALID_ARTIFACT_URL", "附件路径无效。")
        release = self.request(
            "GET", f"repos/{self.repo}/releases/tags/{quote(tag, safe='')}"
        )
        asset = next(
            (item for item in release.get("assets", []) if item["name"] == name), None
        )
        if not asset:
            raise ProtocolError("ARTIFACT_NOT_FOUND", "找不到已提交的成果附件。")
        if asset.get("size", 0) > 20 * 1024 * 1024:
            raise ProtocolError("ARTIFACT_TOO_LARGE", "成果附件超过 20 MiB。")
        output = self._run(
            [
                "gh",
                "api",
                "--hostname",
                self.hostname,
                f"repos/{self.repo}/releases/assets/{int(asset['id'])}",
                "-H",
                "Accept: application/octet-stream",
            ],
            binary=True,
        )
        if len(output) > 20 * 1024 * 1024:
            raise ProtocolError("ARTIFACT_TOO_LARGE", "成果附件超过 20 MiB。")
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(output)

    def publish_directory(self, directory: Path, branch: str = "gh-pages"):
        """Replace the generated Pages tree with a fast-forward Git commit."""
        entries = []
        for file in sorted(Path(directory).rglob("*")):
            if file.is_symlink():
                raise ProtocolError("SYMLINK_IN_SITE", "站点发布目录不能包含符号链接。")
            if not file.is_file():
                continue
            blob = self.request(
                "POST",
                f"repos/{self.repo}/git/blobs",
                {
                    "content": base64.b64encode(file.read_bytes()).decode(),
                    "encoding": "base64",
                },
            )
            entries.append(
                {
                    "path": file.relative_to(directory).as_posix(),
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob["sha"],
                }
            )
        ref = self._optional(f"repos/{self.repo}/git/ref/heads/{branch}")
        tree = self.request("POST", f"repos/{self.repo}/git/trees", {"tree": entries})
        parents = [] if ref is None else [ref["object"]["sha"]]
        commit = self.request(
            "POST",
            f"repos/{self.repo}/git/commits",
            {
                "message": "Keep the public board aligned with acknowledged task state\n\nScope-risk: narrow\nTested: Snapshot validation",
                "tree": tree["sha"],
                "parents": parents,
            },
        )
        if ref is None:
            self.request(
                "POST",
                f"repos/{self.repo}/git/refs",
                {"ref": f"refs/heads/{branch}", "sha": commit["sha"]},
            )
        else:
            self.request(
                "PATCH",
                f"repos/{self.repo}/git/refs/heads/{branch}",
                {"sha": commit["sha"], "force": False},
            )
        return commit["sha"]
