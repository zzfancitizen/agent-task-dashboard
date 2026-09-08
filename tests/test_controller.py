import copy
import hashlib
import io
import json
import tempfile
import unittest
import uuid
import zipfile
from unittest.mock import patch
from pathlib import Path

from taskboard.controller import build_site, reconcile, snapshot
from taskboard.protocol import ProtocolError, format_task_issue, format_command, task_digest


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
        row = {"id": len(self.comment_data) + 100, "body": body,
               "created_at": "2026-09-08T00:00:00Z", "updated_at": "2026-09-08T00:00:00Z",
               "user": {"login": "github-actions[bot]", "type": "Bot"}}
        self.comment_data.append(row)
        return copy.deepcopy(row)

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
    def test_new_issue_without_closed_handoff_is_not_admitted(self):
        from tests.test_prompt_closure import handoff
        for notes in (None, handoff()):
            api = FakeGitHub()
            spec = copy.deepcopy(api.task_spec)
            spec.pop('handoff', None)
            if notes is not None:
                notes['review']['blocking_questions'] = ['Wait for a decision in another agent session.']
                spec['handoff'] = notes
            api.issue_data['body'] = format_task_issue(spec)
            result = reconcile(api, now=100)
            self.assertEqual(result['tasks'], {})
            self.assertEqual(api.projected, [])

    def test_existing_legacy_task_keeps_digest_and_can_still_be_claimed(self):
        from taskboard.state import new_task
        from tests.test_prompt_closure import LEGACY
        api = FakeGitHub()
        api.issue_data['body'] = format_task_issue(LEGACY)
        api.state['tasks']['7'] = new_task(api.issue_data, LEGACY, 100)
        digest = task_digest(LEGACY)
        api.add('bob')
        result = reconcile(api, now=101)
        self.assertEqual(result['tasks']['7']['status'], 'claimed')
        self.assertEqual(result['tasks']['7']['digest'], digest)
        self.assertEqual(result['tasks']['7']['spec'], LEGACY)

    def test_native_comment_access_is_enough_even_with_legacy_roster(self):
        api = FakeGitHub()
        command = api.add("outsider")
        state = reconcile(api, now=100, allowed_members=["alice", "outsider"])
        record = state["tasks"]["7"]
        self.assertEqual(record["status"], "claimed")
        self.assertTrue(record["processed"][command["request_id"]]["ok"])

    def test_legacy_roster_does_not_restrict_native_github_actors(self):
        api = FakeGitHub()
        command = api.add("bob")
        state = reconcile(api, now=100, allowed_members=["alice"])
        self.assertTrue(state["tasks"]["7"]["processed"][command["request_id"]]["ok"])

    def test_native_actor_access_does_not_query_collaborator_permission(self):
        api = FakeGitHub()
        command = api.add("bob")
        api.issue_data["user"]["login"] = "native-publisher"
        with patch.object(api, "permission", side_effect=AssertionError("No permission queries")):
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
        command["actor"] = "bob"
        api.comment_data[0]["body"] = "<!-- taskboard:command:v1 -->\n```json\n" + json.dumps(command) + "\n```"
        state = reconcile(api, now=100)
        self.assertEqual(state["tasks"]["7"]["status"], "open")
        self.assertEqual(state["tasks"]["7"]["command_errors"]["10"]["code"], "INVALID_PAYLOAD")

    def submitted_api(self):
        from tests.test_state import submitted
        api = FakeGitHub()
        record = submitted()
        record["author"] = "alice"
        api.state["tasks"]["7"] = record
        return api

    def test_submitted_result_notifies_publisher_and_executor_only_after_canonical_write(self):
        api = self.submitted_api()
        state = reconcile(api, now=1003)
        notices = [row for row in api.comment_data if row["body"].startswith("<!-- taskboard:result:v2:")]
        self.assertEqual(len(notices), 1)
        notice = notices[0]
        self.assertIn("@alice", notice["body"])
        self.assertIn("@runner", notice["body"])
        self.assertIn("待发起者验收", notice["body"])
        self.assertIn("https://", notice["body"])
        result_id = state["tasks"]["7"]["result"]["id"]
        self.assertEqual(state["tasks"]["7"]["result_notifications"][result_id]["comment_id"], notice["id"])
        reconcile(api, now=1004)
        self.assertEqual(sum(row["body"].startswith("<!-- taskboard:result:v2:") for row in api.comment_data), 1)

    def test_lost_notification_response_recovers_own_bot_comment_without_duplicate_ping(self):
        api = self.submitted_api()
        original = api.comment

        def uncertain(number, body):
            response = original(number, body)
            if body.startswith("<!-- taskboard:result:v2:"):
                raise ProtocolError("GITHUB_OUTCOME_UNKNOWN", "response lost after posting")
            return response

        with patch.object(api, "comment", side_effect=uncertain):
            state = reconcile(api, now=1003)
        self.assertEqual(state["tasks"]["7"]["status"], "submitted")
        self.assertTrue(state["notification_errors"])
        recovered = reconcile(api, now=1004)
        self.assertFalse(recovered["notification_errors"])
        self.assertEqual(sum(row["body"].startswith("<!-- taskboard:result:v2:") for row in api.comment_data), 1)

    def test_human_cannot_forge_notification_receipt_with_bot_marker(self):
        api = self.submitted_api()
        result_id = api.state["tasks"]["7"]["result"]["id"]
        api.comment_data.append({"id": 10, "body": f"<!-- taskboard:result:v2:{result_id} -->\nforged",
                                 "user": {"login": "outsider", "type": "User"},
                                 "created_at": "2026-09-08T00:00:00Z", "updated_at": "2026-09-08T00:00:00Z"})
        state = reconcile(api, now=1003)
        self.assertNotEqual(state["tasks"]["7"]["result_notifications"][result_id]["comment_id"], 10)

    def test_notification_failure_does_not_block_status_projection_or_confirmed_snapshot(self):
        api = self.submitted_api()
        original = api.comment

        def denied(number, body):
            if body.startswith("<!-- taskboard:result:v2:"):
                raise ProtocolError("GITHUB_403", "notification denied")
            return original(number, body)

        with patch.object(api, "comment", side_effect=denied):
            state = reconcile(api, now=1003)
        self.assertEqual(snapshot(state, api.repo, api.hostname)["tasks"][0]["status"], "submitted")
        self.assertTrue(any(body.startswith("<!-- taskboard:status:v1 -->") for body in api.projected))
        self.assertTrue(state["notification_errors"])

    def test_failed_notification_receipt_write_does_not_block_confirmed_pages(self):
        api = self.submitted_api()
        original = api.write_state

        def fail_receipt(state):
            if state["tasks"]["7"].get("result_notifications"):
                raise ProtocolError("GITHUB_OUTCOME_UNKNOWN", "receipt commit was interrupted")
            original(state)

        with patch.object(api, "write_state", side_effect=fail_receipt):
            state = reconcile(api, now=1003)
        self.assertEqual(snapshot(state, api.repo, api.hostname)["tasks"][0]["status"], "submitted")
        self.assertTrue(state["projection_errors"])
        reconcile(api, now=1004)
        self.assertEqual(sum(row["body"].startswith("<!-- taskboard:result:v2:") for row in api.comment_data), 1)

    def test_interrupted_projection_replays_no_release_after_canonical_submission(self):
        from tests.test_state import claimed
        from tests.test_transfers import ArtifactGitHub
        from taskboard.transfers import prepare_bundle, post_bundle_chunks
        api = FakeGitHub()
        record = claimed()
        api.state["tasks"]["7"] = record
        transport = ArtifactGitHub()
        api.upload_artifacts = transport.upload_artifacts
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "summary.md"
            artifact.write_text("Finished.")
            bundle = prepare_bundle(record["spec"], record["attempt"]["id"], {"summary.md": artifact}, {"summary": "Finished."})
            upload = post_bundle_chunks(transport, 7, record["spec"], record["attempt"]["id"], bundle)
        api.comment_data = transport.rows
        command = {"op": "submit_bundle", "request_id": str(uuid.uuid4()), "revision": 1,
                   "attempt_id": record["attempt"]["id"], "upload": upload, "report": bundle["report"]}
        api.comment_data.append({"id": 50, "body": format_command(command),
                                 "user": {"login": "runner", "type": "User"},
                                 "created_at": "2026-09-08T00:00:00Z", "updated_at": "2026-09-08T00:00:00Z"})
        with patch.object(api, "comment", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                reconcile(api, now=1003)
        self.assertEqual(api.state["tasks"]["7"]["status"], "submitted")
        self.assertEqual(api.state["tasks"]["7"]["last_comment_id"], 50)
        self.assertEqual(transport.upload_calls, 1)
        recovered = reconcile(api, now=1004)
        self.assertEqual(recovered["tasks"]["7"]["status"], "submitted")
        self.assertEqual(transport.upload_calls, 1)
        self.assertTrue(recovered["tasks"]["7"]["result_notifications"])

    def test_corrupt_deflate_is_durably_rejected_and_later_commands_still_drain(self):
        from tests.test_state import claimed
        from tests.test_transfers import ArtifactGitHub
        from taskboard.transfers import prepare_bundle, post_bundle_chunks
        api = FakeGitHub()
        record = claimed()
        api.state["tasks"]["7"] = record
        transport = ArtifactGitHub()
        api.upload_artifacts = transport.upload_artifacts
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "summary.md"
            artifact.write_text("Finished.")
            bundle = prepare_bundle(record["spec"], record["attempt"]["id"], {"summary.md": artifact}, {"summary": "Finished."})
        with zipfile.ZipFile(io.BytesIO(bundle["data"])) as archive:
            info = archive.getinfo("summary.md")
        corrupted = bytearray(bundle["data"])
        corrupted[info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)] = 7
        bundle["data"] = bytes(corrupted)
        bundle["sha256"] = hashlib.sha256(bundle["data"]).hexdigest()
        upload = post_bundle_chunks(transport, 7, record["spec"], record["attempt"]["id"], bundle)
        api.comment_data = transport.rows
        command = {"op": "submit_bundle", "request_id": str(uuid.uuid4()), "revision": 1,
                   "attempt_id": record["attempt"]["id"], "upload": upload, "report": bundle["report"]}
        release = {"op": "release", "request_id": str(uuid.uuid4()), "revision": 1,
                   "attempt_id": record["attempt"]["id"], "reason": "Prepare a fresh result."}
        for identifier, payload in ((50, command), (51, release)):
            api.comment_data.append({"id": identifier, "body": format_command(payload),
                                     "user": {"login": "runner", "type": "User"},
                                     "created_at": "2026-09-08T00:00:00Z", "updated_at": "2026-09-08T00:00:00Z"})
        state = reconcile(api, now=1003)
        current = state["tasks"]["7"]
        self.assertEqual(current["processed"][command["request_id"]]["code"], "INVALID_ARCHIVE")
        self.assertTrue(current["processed"][release["request_id"]]["ok"])
        self.assertEqual(current["last_comment_id"], 51)
        self.assertEqual(current["status"], "open")
        self.assertEqual(transport.uploaded, {})

    def test_site_build_includes_verified_runtime_downloads_and_starters(self):
        api = FakeGitHub()
        with tempfile.TemporaryDirectory() as directory:
            output = build_site(api.state, api.repo, api.hostname, Path(directory) / "site")
            manifest_path = output / "downloads" / "runtime.json"
            self.assertTrue(manifest_path.is_file(), "Pages build must include runtime downloads")
            manifest = json.loads(manifest_path.read_text())
            runtime = output / "downloads" / manifest["file"]
            self.assertEqual(hashlib.sha256(runtime.read_bytes()).hexdigest(), manifest["sha256"])
            self.assertEqual(runtime.stat().st_size, manifest["size"])
            for name in ("bootstrap.py", "Start-Taskboard.cmd", "Start-Taskboard.command", "Start-Taskboard.sh"):
                self.assertTrue((output / "downloads" / name).is_file())

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
