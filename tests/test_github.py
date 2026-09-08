import base64
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from taskboard.github import GitHub
from taskboard.protocol import ProtocolError


class GitHubTests(unittest.TestCase):
    def test_utf8_transport_does_not_depend_on_windows_ansi_locale(self):
        payload = json.dumps({'message': '委托完成 ✓'}, ensure_ascii=False)
        script = 'import sys;sys.stdout.buffer.write(sys.stdin.buffer.read())'
        with patch('subprocess._text_encoding', return_value='cp936'):
            returned = GitHub('team/board')._run([sys.executable, '-I', '-c', script], data=payload)
        self.assertEqual(json.loads(returned), {'message': '委托完成 ✓'})

    def test_enterprise_api_uses_hostname_and_json_stdin(self):
        response = subprocess.CompletedProcess([], 0, b'{"id": 4}', b'')
        with patch("subprocess.run", return_value=response) as run:
            result = GitHub("team/board", "git.corp.test").request(
                "POST", "repos/team/board/issues", {"body": "$(touch nope)\n`literal`"}
            )
        self.assertEqual(result, {"id": 4})
        args, kwargs = run.call_args
        self.assertIn("git.corp.test", args[0])
        self.assertEqual(
            json.loads(kwargs["input"])["body"], "$(touch nope)\n`literal`"
        )
        self.assertFalse(kwargs.get("shell", False))

    def test_invalid_repo_and_hostname_are_rejected_before_gh(self):
        for repo, host in [
            ("../board", "github.com"),
            ("team/board", "bad;host"),
            ("team/board", "https://git.example"),
            ("team/board/extra", "github.com"),
        ]:
            with self.subTest(repo=repo, host=host):
                with self.assertRaises(ProtocolError):
                    GitHub(repo, host)

    def test_error_does_not_echo_credentials_or_untrusted_stderr(self):
        failed = subprocess.CompletedProcess(
            [], 1, b'', b'Authorization: Bearer secret-value\nHTTP 403'
        )
        with patch("subprocess.run", return_value=failed):
            with self.assertRaises(ProtocolError) as caught:
                GitHub("team/board").request("GET", "user")
        self.assertNotIn("secret-value", str(caught.exception))
        self.assertEqual(caught.exception.code, "GITHUB_403")

    def test_malformed_native_utf8_is_an_uncertain_response_in_the_caller(self):
        for stdout, stderr, code in ((b'\xff', b'', 0), (b'', b'\xff', 1)):
            with self.subTest(code=code), patch('subprocess.run', return_value=subprocess.CompletedProcess([], code, stdout, stderr)) as boundary:
                with self.assertRaises(ProtocolError) as caught:
                    GitHub('team/board').request('POST', 'repos/team/board/issues', {'body': '委托'})
                self.assertEqual(caught.exception.code, 'GITHUB_OUTCOME_UNKNOWN')
                self.assertFalse(boundary.call_args.kwargs.get('text', False))

    def test_paginated_comments_are_not_dropped(self):
        client = GitHub("team/board")
        pages = [[{"id": n} for n in range(100)], [{"id": 100}]]
        with patch.object(client, "request", side_effect=pages) as request:
            self.assertEqual(len(client.comments(7)), 101)
        self.assertIn("page=2", request.call_args.args[1])

    def test_state_write_keeps_original_blob_sha_for_conflict_detection(self):
        state = {"schema_version": 1, "tasks": {}, "updated_at": 0}
        client = GitHub("team/board")
        encoded = base64.b64encode(json.dumps(state).encode()).decode()
        with patch.object(
            client,
            "request",
            side_effect=[
                {"sha": "old-blob", "content": encoded},
                {"content": {"sha": "new-blob"}},
            ],
        ) as request:
            self.assertEqual(client.read_state(), state)
            client.write_state({**state, "updated_at": 1})
        self.assertEqual(request.call_args.args[2]["sha"], "old-blob")
        self.assertEqual(request.call_args.args[2]["branch"], "taskboard-state")

    def test_download_rejects_foreign_host_before_auth(self):
        client = GitHub("team/board", "git.corp.test")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ProtocolError):
                client.download(
                    "https://evil.test/team/board/releases/download/x/result.md",
                    Path(directory) / "r",
                )

    def test_download_accepts_canonical_repository_case(self):
        client = GitHub("Team/Board", "git.corp.test")
        release = {"assets": [{"id": 5, "name": "result.md", "size": 5}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.md"
            with (
                patch.object(client, "request", return_value=release),
                patch.object(client, "_run", return_value=b"hello"),
            ):
                client.download(
                    "https://git.corp.test/team/board/releases/download/x/result.md",
                    path,
                )
            self.assertEqual(path.read_bytes(), b"hello")


if __name__ == "__main__":
    unittest.main()
