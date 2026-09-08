"""Protocol boundary tests: immutable JSON content and untrusted public input."""

import copy
import hashlib
import json
import unittest
import uuid

from taskboard.protocol import (
    ProtocolError,
    format_command,
    format_task_issue,
    parse_command,
    parse_task_issue,
    task_digest,
    validate_result,
    validate_task,
)


def task_fixture():
    return {
        "schema_version": 1,
        "task_id": "722f36c5-4a57-4bd2-a5c5-e15a644a82c7",
        "revision": 1,
        "mode": "subtask",
        "title": "检查边界",
        "prompt": "Read the rules and add boundary tests.",
        "delegation_reason": "The inputs and acceptance command are bounded.",
        "source": {
            "repository": "https://git.example.internal/org/project",
            "base_commit": "a" * 40,
            "workspace_patch": None,
            "write_paths": ["tests/"],
        },
        "resources": [{
            "type": "git_file",
            "repository": "https://git.example.internal/org/rules",
            "commit": "b" * 40,
            "path": "docs/rules.md",
            "destination": "resources/rules.md",
            "sha256": "c" * 64,
        }],
        "execution": {
            "compatible_agents": ["codex", "claude"],
            "timeout_seconds": 60,
            "max_attempts": 2,
        },
        "acceptance": {
            "commands": [["python3", "-m", "unittest"]],
            "required_outputs": ["changes.patch", "summary.md"],
            "review_notes": "Review the patch before integration.",
        },
    }


def result_fixture(task=None, attempt_id="d9ec477b-0a1c-4427-8603-2c031ee0821d"):
    task = task or task_fixture()
    return {
        "schema_version": 1,
        "task_id": task["task_id"],
        "revision": task["revision"],
        "task_digest": task_digest(task),
        "attempt_id": attempt_id,
        "base_commit": task["source"]["base_commit"],
        "summary": "Added the requested tests.",
        "artifacts": [{
            "name": name,
            "uri": "https://git.example.internal/org/project/releases/download/attempt/" + name,
            "sha256": "d" * 64,
        } for name in task["acceptance"]["required_outputs"]],
        "verification": [{
            "argv": ["python3", "-m", "unittest"],
            "exit_code": 0,
            "evidence": "https://git.example.internal/org/project/releases/download/attempt/verification.json",
        }],
        "assumptions": [],
        "unresolved": [],
        "usage": None,
    }


class ProtocolTests(unittest.TestCase):
    def test_task_roundtrip_preserves_enterprise_repository_and_unicode(self):
        task = task_fixture()
        body = format_task_issue(task)
        self.assertTrue(body.startswith("<!-- taskboard:task:v1 -->\n"))
        self.assertIn("检查边界", body)
        self.assertEqual(parse_task_issue(body), task)

    def test_digest_uses_canonical_utf8_json_not_insertion_order(self):
        task = task_fixture()
        canonical = json.dumps(task, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        self.assertEqual(task_digest(task), expected)
        self.assertEqual(task_digest(dict(reversed(list(task.items())))), expected)
        task["prompt"] += " Changed instruction."
        self.assertNotEqual(task_digest(task), expected)

    def test_validated_task_does_not_alias_callers_nested_input(self):
        task = task_fixture()
        validated = validate_task(task)
        validated["source"]["write_paths"].append("src/")
        self.assertEqual(task["source"]["write_paths"], ["tests/"])

    def test_optional_board_metadata_is_validated_without_inserting_defaults(self):
        task = task_fixture()
        self.assertEqual(validate_task(task), task)
        task.update(category="code", size="M", priority="high")
        self.assertEqual(validate_task(task), task)
        task["priority"] = "urgent"
        with self.assertRaises(ProtocolError):
            validate_task(task)

    def test_non_null_workspace_patch_is_unsupported(self):
        task = task_fixture()
        task["source"]["workspace_patch"] = {"uri": "https://example.com/patch"}
        with self.assertRaises(ProtocolError) as caught:
            validate_task(task)
        self.assertEqual(caught.exception.code, "UNSUPPORTED_WORKSPACE_PATCH")

    def test_unknown_fields_prevent_session_ids_or_auth_in_structured_payload(self):
        for field in ("session_id", "token", "unexpected"):
            with self.subTest(field=field):
                task = task_fixture()
                task[field] = "private-value"
                with self.assertRaises(ProtocolError):
                    validate_task(task)
        task = task_fixture()
        task["source"]["auth"] = "private-value"
        with self.assertRaises(ProtocolError):
            validate_task(task)

    def test_task_bounds_reject_boolean_integers_and_invalid_identifiers(self):
        mutations = [
            ("schema_version", True), ("schema_version", 2),
            ("revision", True), ("revision", 0),
            ("task_id", "not-a-uuid"), ("mode", "whole-session"),
            ("title", ""), ("prompt", "   "),
        ]
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                task = task_fixture()
                task[key] = value
                with self.assertRaises(ProtocolError):
                    validate_task(task)
        for key, value in [
            ("timeout_seconds", 0), ("timeout_seconds", 14401),
            ("timeout_seconds", True), ("max_attempts", 0), ("max_attempts", 6),
            ("compatible_agents", []), ("compatible_agents", ["unknown"]),
        ]:
            with self.subTest(key=key, value=value):
                task = task_fixture()
                task["execution"][key] = value
                with self.assertRaises(ProtocolError):
                    validate_task(task)

    def test_paths_cannot_escape_or_target_git_metadata(self):
        for path in ("../outside", "/tmp/outside", "C:\\outside", "a/../b", ".git/config", "a//b", "a\x00b"):
            with self.subTest(path=path):
                task = task_fixture()
                task["resources"][0]["destination"] = path
                with self.assertRaises(ProtocolError):
                    validate_task(task)
        task = task_fixture()
        task["source"]["write_paths"] = ["../"]
        with self.assertRaises(ProtocolError):
            validate_task(task)

    def test_repository_urls_cannot_embed_credentials_or_change_protocol(self):
        for url in (
            "http://git.example.internal/org/repo",
            "https://token@git.example.internal/org/repo",
            "https://git.example.internal/org/repo?access_token=secret",
            "https://git.example.internal/org/repo/tree/main",
            "https://git.example.internal/org/%2e%2e",
        ):
            with self.subTest(url=url):
                task = task_fixture()
                task["source"]["repository"] = url
                with self.assertRaises(ProtocolError):
                    validate_task(task)

    def test_duplicate_resource_destinations_are_rejected(self):
        task = task_fixture()
        task["resources"].append(copy.deepcopy(task["resources"][0]))
        with self.assertRaises(ProtocolError):
            validate_task(task)

    def test_acceptance_is_argv_arrays_and_relative_output_names(self):
        for commands in ("python3 -m unittest", ["python3 -m unittest"], [[]], [["python3", 4]]):
            with self.subTest(commands=commands):
                task = task_fixture()
                task["acceptance"]["commands"] = commands
                with self.assertRaises(ProtocolError):
                    validate_task(task)
        task = task_fixture()
        task["acceptance"]["required_outputs"] = ["../secret"]
        with self.assertRaises(ProtocolError):
            validate_task(task)

    def test_task_payload_limit_counts_utf8_bytes(self):
        task = task_fixture()
        task["prompt"] = "界" * 17000
        with self.assertRaises(ProtocolError) as caught:
            validate_task(task)
        self.assertEqual(caught.exception.code, "PAYLOAD_TOO_LARGE")

    def test_task_parser_rejects_duplicate_keys_and_nonfinite_json(self):
        marker = "<!-- taskboard:task:v1 -->\n```json\n"
        for payload in ('{"task_id": "one", "task_id": "two"}', '{"usage": NaN}'):
            with self.subTest(payload=payload):
                with self.assertRaises(ProtocolError):
                    parse_task_issue(marker + payload + "\n```")

    def test_task_marker_requires_one_json_record(self):
        body = format_task_issue(task_fixture())
        for text in ("unrelated issue", body + "\n" + body, "<!-- taskboard:task:v1 -->\nnot-json"):
            with self.subTest(text=text[:80]):
                with self.assertRaises(ProtocolError):
                    parse_task_issue(text)

    def test_unrelated_comments_are_ignored_but_malformed_commands_fail(self):
        self.assertIsNone(parse_command("Thanks for the update."))
        with self.assertRaises(ProtocolError):
            parse_command("<!-- taskboard:command:v1 -->\n```json\n{bad}\n```")

    def test_marked_json_null_is_a_malformed_command_not_an_unrelated_comment(self):
        with self.assertRaises(ProtocolError):
            parse_command("<!-- taskboard:command:v1 -->\n```json\nnull\n```")

    def test_oversized_integer_is_reported_as_protocol_error(self):
        payload = '{"revision":' + "1" * 5000 + "}"
        with self.assertRaises(ProtocolError):
            parse_command("<!-- taskboard:command:v1 -->\n```json\n" + payload + "\n```")

    def test_deeply_nested_private_session_metadata_is_rejected(self):
        task = task_fixture()
        result = result_fixture(task)
        result["usage"] = {"provider": {"original_session_id": "local-only-session"}}
        with self.assertRaises(ProtocolError):
            validate_result(result, task, result["attempt_id"])

    def test_all_commands_roundtrip_with_their_required_binding(self):
        for op in ("claim", "start", "release", "submit", "accept", "reject", "cancel"):
            with self.subTest(op=op):
                command = {"op": op, "request_id": str(uuid.uuid4()), "revision": 1}
                if op in {"start", "release", "submit"}:
                    command["attempt_id"] = "d9ec477b-0a1c-4427-8603-2c031ee0821d"
                if op == "submit":
                    command["result"] = result_fixture()
                if op in {"accept", "reject"}:
                    command["result_id"] = "e9898cd6-e7fb-4c42-b0aa-8b9085bb093f"
                if op in {"release", "reject"}:
                    command["reason"] = "Needs another attempt."
                self.assertEqual(parse_command(format_command(command)), command)

    def test_command_cannot_omit_binding_or_smuggle_actor(self):
        for command in (
            {"op": "start", "request_id": str(uuid.uuid4()), "revision": 1},
            {"op": "accept", "request_id": str(uuid.uuid4()), "revision": 1},
            {"op": "claim", "request_id": str(uuid.uuid4()), "revision": 1, "actor": "author"},
            {"op": "claim", "request_id": str(uuid.uuid4()), "revision": True},
        ):
            with self.subTest(command=command):
                with self.assertRaises(ProtocolError):
                    format_command(command)

    def test_command_comment_payload_limit_includes_the_envelope(self):
        command = {"op": "reject", "request_id": str(uuid.uuid4()), "revision": 1,
                   "result_id": str(uuid.uuid4()), "reason": "界" * 17000}
        with self.assertRaises(ProtocolError) as caught:
            format_command(command)
        self.assertEqual(caught.exception.code, "PAYLOAD_TOO_LARGE")

    def test_result_is_detached_and_all_exact_task_bindings_are_checked(self):
        task = task_fixture()
        result = result_fixture(task)
        validated = validate_result(result, task, result["attempt_id"])
        validated["artifacts"][0]["name"] = "changed.patch"
        self.assertEqual(result["artifacts"][0]["name"], "changes.patch")
        for key, value in (
            ("task_id", str(uuid.uuid4())), ("revision", 2),
            ("task_digest", "0" * 64), ("attempt_id", str(uuid.uuid4())),
            ("base_commit", "e" * 40),
        ):
            with self.subTest(key=key):
                broken = copy.deepcopy(result)
                broken[key] = value
                with self.assertRaises(ProtocolError):
                    validate_result(broken, task, result["attempt_id"])

    def test_required_artifacts_are_present_unique_and_https(self):
        task = task_fixture()
        result = result_fixture(task)
        missing = copy.deepcopy(result)
        missing["artifacts"].pop()
        duplicate = copy.deepcopy(result)
        duplicate["artifacts"].append(copy.deepcopy(result["artifacts"][0]))
        local = copy.deepcopy(result)
        local["artifacts"][0]["uri"] = "artifact:sha256/" + "d" * 64
        authenticated = copy.deepcopy(result)
        authenticated["artifacts"][0]["uri"] = "https://user:token@example.com/patch"
        for broken in (missing, duplicate, local, authenticated):
            with self.subTest(broken=broken["artifacts"]):
                with self.assertRaises(ProtocolError):
                    validate_result(broken, task, result["attempt_id"])

    def test_verification_requires_typed_exit_codes_and_https_evidence(self):
        task = task_fixture()
        result = result_fixture(task)
        for key, value in (
            ("argv", "python3 -m unittest"), ("argv", []),
            ("exit_code", True), ("exit_code", "0"),
            ("evidence", "artifact:sha256/" + "d" * 64),
        ):
            with self.subTest(key=key, value=value):
                broken = copy.deepcopy(result)
                broken["verification"][0][key] = value
                with self.assertRaises(ProtocolError):
                    validate_result(broken, task, result["attempt_id"])


if __name__ == "__main__":
    unittest.main()
