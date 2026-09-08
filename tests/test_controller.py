import copy
import json
import unittest
import uuid
from unittest.mock import patch
from pathlib import Path

from taskboard.controller import reconcile, snapshot
from taskboard.protocol import ProtocolError, format_task_issue, format_command


class FakeGitHub:
    repo = "team/board"
    hostname = "git.corp.test"

    def __init__(self):
        self.saved = []
        self.projected = []
        self.state = {"schema_version": 1, "tasks": {}, "updated_at": 0}
        spec = json.loads(Path("docs/examples/task-v1.json").read_text())
        self.task_spec = spec
        self.issue_data = {
            "number": 7,
            "html_url": "https://git.corp.test/team/board/issues/7",
            "title": spec["title"],
            "body": format_task_issue(spec),
            "created_at": "2026-09-08T00:00:00Z",
            "user": {"login": "alice", "type": "User"},
        }
        self.comment_data = []

    def read_state(self):
        return copy.deepcopy(self.state)

    def write_state(self, state):
        self.state = copy.deepcopy(state)
        self.saved.append(copy.deepcopy(state))

    def issues(self):
        return [copy.deepcopy(self.issue_data)]

    def comments(self, number):
        return copy.deepcopy(self.comment_data)

    def permission(self, actor):
        return "write" if actor in {"alice", "bob", "carol"} else "read"

    def comment(self, number, body):
        assert self.saved or self.state["tasks"], (
            "projection cannot precede durable state"
        )
        self.projected.append(body)
        return {"id": 77}

    def request(self, *args):
        self.projected.append(args)
        return {}

    def add(self, actor, op="claim", edited=False):
        command = {"op": op, "request_id": str(uuid.uuid4()), "revision": 1}
        self.comment_data.append(
            {
                "id": len(self.comment_data) + 10,
                "body": format_command(command),
                "created_at": "2026-09-08T00:00:00Z",
                "updated_at": "2026-09-08T00:01:00Z"
                if edited
                else "2026-09-08T00:00:00Z",
                "user": {"login": actor, "type": "User"},
            }
        )
        return command


class ControllerTests(unittest.TestCase):
    def test_roster_does_not_grant_native_write_permission(self):
        api = FakeGitHub()
        command = api.add("outsider")
        state = reconcile(api, now=100, allowed_members=["alice", "outsider"])
        record = state["tasks"]["7"]
        self.assertEqual(record["status"], "open")
        self.assertFalse(record["processed"][command["request_id"]]["ok"])

    def test_nonempty_roster_restricts_otherwise_writable_members(self):
        api = FakeGitHub()
        command = api.add("bob")
        state = reconcile(api, now=100, allowed_members=["alice"])
        self.assertFalse(state["tasks"]["7"]["processed"][command["request_id"]]["ok"])

    def test_temporary_permission_failure_leaves_command_retryable(self):
        api = FakeGitHub()
        command = api.add("bob")
        original = api.permission

        def unavailable(actor):
            if actor == "bob":
                raise ProtocolError("GITHUB_OUTCOME_UNKNOWN", "network timeout")
            return original(actor)

        with patch.object(api, "permission", side_effect=unavailable):
            with self.assertRaises(ProtocolError):
                reconcile(api, now=100)
        self.assertEqual(api.saved, [])
        recovered = reconcile(api, now=101)
        self.assertTrue(
            recovered["tasks"]["7"]["processed"][command["request_id"]]["ok"]
        )

    def test_failed_issue_projection_does_not_block_confirmed_snapshot(self):
        api = FakeGitHub()
        api.add("bob")
        with patch.object(
            api, "comment", side_effect=ProtocolError("GITHUB_403", "comment denied")
        ):
            state = reconcile(api, now=100)
        self.assertEqual(state["tasks"]["7"]["status"], "claimed")
        self.assertEqual(
            snapshot(state, api.repo, api.hostname, now=100)["tasks"][0]["status"],
            "claimed",
        )
        self.assertTrue(state["projection_errors"])

    def test_recent_request_uses_comment_order_after_json_round_trip(self):
        api = FakeGitHub()
        first = api.add("bob")
        second = api.add("carol")
        first["request_id"] = "ffffffff-ffff-4fff-8fff-ffffffffffff"
        second["request_id"] = "00000000-0000-4000-8000-000000000001"
        api.comment_data[0]["body"] = format_command(first)
        api.comment_data[1]["body"] = format_command(second)
        reconcile(api, now=100)
        api.state = json.loads(json.dumps(api.state, sort_keys=True))
        reconcile(api, now=101)
        self.assertIn("TASK_NOT_OPEN", api.projected[-1])

    def test_drain_retained_commands_once_and_first_valid_claim_wins(self):
        api = FakeGitHub()
        bob = api.add("bob")
        carol = api.add("carol")
        state = reconcile(api, now=100)
        record = state["tasks"]["7"]
        self.assertEqual(record["attempt"]["actor"], "bob")
        self.assertTrue(record["processed"][bob["request_id"]]["ok"])
        self.assertFalse(record["processed"][carol["request_id"]]["ok"])
        self.assertEqual(reconcile(api, now=101)["tasks"]["7"]["attempt_count"], 1)

    def test_public_commenter_cannot_claim_by_spoofing_body_actor(self):
        api = FakeGitHub()
        command = api.add("outsider")
        state = reconcile(api, now=100)
        self.assertEqual(state["tasks"]["7"]["status"], "open")
        self.assertFalse(state["tasks"]["7"]["processed"][command["request_id"]]["ok"])

    def test_edited_command_is_rejected_and_cursor_advances(self):
        api = FakeGitHub()
        command = api.add("bob", edited=True)
        state = reconcile(api, now=100)
        record = state["tasks"]["7"]
        self.assertEqual(record["status"], "open")
        self.assertEqual(record["last_comment_id"], 10)
        self.assertFalse(record["processed"][command["request_id"]]["ok"])

    def test_mutating_issue_body_does_not_rewrite_published_prompt(self):
        api = FakeGitHub()
        first = reconcile(api, now=100)
        api.task_spec["prompt"] = "New instructions slipped into a published issue"
        api.issue_data["body"] = format_task_issue(api.task_spec)
        later = reconcile(api, now=101)
        self.assertEqual(later["tasks"]["7"]["digest"], first["tasks"]["7"]["digest"])
        self.assertNotEqual(
            later["tasks"]["7"]["spec"]["prompt"], api.task_spec["prompt"]
        )

    def test_snapshot_has_no_internal_processed_records(self):
        api = FakeGitHub()
        api.add("bob")
        view = snapshot(reconcile(api, now=100), api.repo, api.hostname, now=100)
        self.assertFalse(view["demo"])
        self.assertEqual(view["hostname"], "git.corp.test")
        self.assertNotIn("processed", view["tasks"][0])
        self.assertNotIn("last_comment_id", view["tasks"][0])


if __name__ == "__main__":
    unittest.main()
