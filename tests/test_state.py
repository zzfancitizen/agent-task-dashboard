"""Reducer tests for ownership, recovery, immutable content and delayed commands."""

import copy
import unittest
import uuid

from taskboard.protocol import ProtocolError, task_digest
from taskboard.state import apply_command, expire_task, new_task


def task_fixture(max_attempts=2):
    return {
        "schema_version": 1,
        "task_id": "722f36c5-4a57-4bd2-a5c5-e15a644a82c7",
        "revision": 1,
        "mode": "subtask",
        "title": "Boundary tests",
        "prompt": "Add tests.",
        "delegation_reason": "Independent bounded work.",
        "source": {"repository": "https://git.internal/org/project", "base_commit": "a" * 40,
                   "workspace_patch": None, "write_paths": ["tests/"]},
        "resources": [],
        "execution": {"compatible_agents": ["codex"], "timeout_seconds": 60,
                      "max_attempts": max_attempts},
        "acceptance": {"commands": [], "required_outputs": ["summary.md"], "review_notes": "Review."},
    }


def record_fixture(max_attempts=2):
    issue = {"number": 7, "html_url": "https://git.internal/org/project/issues/7",
             "user": {"login": "Author"}, "created_at": "2026-09-08T00:00:00Z"}
    return new_task(issue, task_fixture(max_attempts), now=1000)


def command(op, **fields):
    return {"op": op, "request_id": str(uuid.uuid4()), "revision": 1, **fields}


def apply(record, request, actor="runner", now=1001, comment_id=10):
    return apply_command(record, request, actor=actor, comment_id=comment_id, now=now)


def claimed(max_attempts=2):
    return apply(record_fixture(max_attempts), command("claim"))


def manifest(record):
    return {
        "schema_version": 1, "task_id": record["spec"]["task_id"], "revision": 1,
        "task_digest": record["digest"], "attempt_id": record["attempt"]["id"],
        "base_commit": "a" * 40, "summary": "Added tests.",
        "artifacts": [{"name": "summary.md", "uri": "https://git.internal/org/project/releases/download/a/summary.md",
                       "sha256": "b" * 64}],
        "verification": [], "assumptions": [], "unresolved": [],
    }


def submitted(max_attempts=2):
    record = claimed(max_attempts)
    return apply(record, command("submit", attempt_id=record["attempt"]["id"], result=manifest(record)), now=1002)


class StateTests(unittest.TestCase):
    def assertRejected(self, record, request, code=None):
        outcome = record["processed"][request["request_id"]]
        self.assertFalse(outcome["ok"])
        if code:
            self.assertEqual(outcome["code"], code)

    def test_new_task_pins_content_and_author_without_aliasing_inputs(self):
        spec = task_fixture()
        issue = {"number": 7, "html_url": "https://git.internal/org/project/issues/7",
                 "user": {"login": "Author"}, "created_at": "2026-09-08T00:00:00Z"}
        record = new_task(issue, spec, 1000)
        self.assertEqual(record["status"], "open")
        self.assertEqual(record["author"], "Author")
        self.assertEqual(record["digest"], task_digest(spec))
        self.assertEqual(record["attempt_count"], 0)
        self.assertIsNone(record["attempt"])
        self.assertIsNone(record["result"])
        self.assertEqual(record["processed"], {})
        spec["source"]["write_paths"].append("src/")
        self.assertEqual(record["spec"]["source"]["write_paths"], ["tests/"])

    def test_duplicate_claims_leave_only_first_owner_and_one_attempt(self):
        record = record_fixture()
        first, second = command("claim"), command("claim")
        before = copy.deepcopy(record)
        won = apply(record, first, actor="runner-a")
        lost = apply(won, second, actor="runner-b", comment_id=11)
        self.assertEqual(record, before)
        self.assertEqual(lost["status"], "claimed")
        self.assertEqual(lost["attempt"]["actor"], "runner-a")
        self.assertEqual(lost["attempt_count"], 1)
        self.assertTrue(won["processed"][first["request_id"]]["ok"])
        self.assertRejected(lost, second)
        self.assertEqual(lost["attempt"]["expires_at"], 1661)
        self.assertEqual(apply(record, first, actor="runner-a")["attempt"]["id"], won["attempt"]["id"])

    def test_exact_replay_is_noop_and_preserves_first_comment_outcome(self):
        request = command("claim")
        once = apply(record_fixture(), request, actor="Runner", comment_id=10)
        reordered = dict(reversed(list(request.items())))
        replayed = apply(once, reordered, actor="runner", now=9999, comment_id=99)
        self.assertEqual(replayed, once)
        self.assertIsNot(replayed, once)
        self.assertEqual(replayed["processed"][request["request_id"]]["comment_id"], 10)

    def test_reused_request_id_with_changed_payload_or_actor_conflicts(self):
        request = command("claim")
        once = apply(record_fixture(), request)
        for changed, actor in (({**request, "op": "cancel"}, "runner"), (request, "other")):
            with self.subTest(changed=changed, actor=actor):
                with self.assertRaises(ProtocolError) as caught:
                    apply(once, changed, actor=actor)
                self.assertEqual(caught.exception.code, "IDEMPOTENCY_CONFLICT")
        self.assertTrue(once["processed"][request["request_id"]]["ok"])

    def test_every_command_must_match_pinned_revision(self):
        request = command("claim", revision=2)
        result = apply(record_fixture(), request)
        self.assertEqual(result["status"], "open")
        self.assertRejected(result, request, "REVISION_MISMATCH")

    def test_start_requires_current_actor_and_attempt(self):
        record = claimed()
        wrong_actor = command("start", attempt_id=record["attempt"]["id"])
        after = apply(record, wrong_actor, actor="intruder")
        self.assertEqual(after["status"], "claimed")
        self.assertRejected(after, wrong_actor, "UNAUTHORIZED")
        wrong_attempt = command("start", attempt_id=str(uuid.uuid4()))
        after = apply(after, wrong_attempt)
        self.assertRejected(after, wrong_attempt, "STALE_ATTEMPT")
        proper = command("start", attempt_id=record["attempt"]["id"])
        after = apply(after, proper, actor="RUNNER")
        self.assertEqual(after["status"], "running")
        self.assertEqual(after["attempt"]["expires_at"], record["attempt"]["expires_at"])

    def test_unauthorized_release_or_submit_cannot_change_ownership(self):
        record = claimed()
        for request in (
            command("release", attempt_id=record["attempt"]["id"], reason="Stop."),
            command("submit", attempt_id=record["attempt"]["id"], result=manifest(record)),
        ):
            with self.subTest(op=request["op"]):
                after = apply(record, request, actor="intruder")
                self.assertEqual(after["attempt"], record["attempt"])
                self.assertEqual(after["status"], "claimed")
                self.assertRejected(after, request, "UNAUTHORIZED")

    def test_expiry_boundary_reopens_only_until_max_attempts(self):
        record = claimed()
        deadline = record["attempt"]["expires_at"]
        self.assertEqual(expire_task(record, deadline - 1), record)
        expired = expire_task(record, deadline)
        self.assertEqual(expired["status"], "open")
        self.assertIsNone(expired["attempt"])
        self.assertEqual(expired["attempt_count"], 1)
        last = claimed(max_attempts=1)
        self.assertEqual(expire_task(last, last["attempt"]["expires_at"])["status"], "failed")
        self.assertEqual(record["status"], "claimed")

    def test_lazy_expiry_rejects_stale_start_release_and_submit(self):
        record = claimed()
        deadline = record["attempt"]["expires_at"]
        requests = [
            command("start", attempt_id=record["attempt"]["id"]),
            command("release", attempt_id=record["attempt"]["id"], reason="Late release."),
            command("submit", attempt_id=record["attempt"]["id"], result=manifest(record)),
        ]
        for request in requests:
            with self.subTest(op=request["op"]):
                expired = apply(record, request, now=deadline)
                self.assertEqual(expired["status"], "open")
                self.assertIsNone(expired["result"])
                self.assertRejected(expired, request, "STALE_ATTEMPT")

    def test_new_claim_after_lazy_expiry_blocks_old_attempt_even_same_actor(self):
        old = claimed()
        deadline = old["attempt"]["expires_at"]
        renewed = apply(old, command("claim"), now=deadline)
        self.assertEqual(renewed["attempt_count"], 2)
        self.assertNotEqual(renewed["attempt"]["id"], old["attempt"]["id"])
        request = command("submit", attempt_id=old["attempt"]["id"], result=manifest(old))
        after = apply(renewed, request, now=deadline + 1)
        self.assertEqual(after["attempt"], renewed["attempt"])
        self.assertIsNone(after["result"])
        self.assertRejected(after, request, "STALE_ATTEMPT")

    def test_release_clears_attempt_and_obeys_retry_budget(self):
        for maximum, expected in ((1, "failed"), (2, "open")):
            with self.subTest(maximum=maximum):
                record = claimed(max_attempts=maximum)
                request = command("release", attempt_id=record["attempt"]["id"], reason="Local execution failed.")
                after = apply(record, request)
                self.assertEqual(after["status"], expected)
                self.assertIsNone(after["attempt"])
                self.assertEqual(after["attempt_count"], 1)

    def test_submit_pins_result_and_does_not_expire_while_awaiting_review(self):
        record = claimed()
        request = command("submit", attempt_id=record["attempt"]["id"], result=manifest(record))
        after = apply(record, request, now=1002)
        self.assertEqual(after["status"], "submitted")
        self.assertEqual(after["result"]["manifest"], request["result"])
        self.assertEqual(apply(record, request, now=1002)["result"]["id"], after["result"]["id"])
        self.assertEqual(expire_task(after, 9999), after)
        request["result"]["summary"] = "Altered after submit."
        self.assertEqual(after["result"]["manifest"]["summary"], "Added tests.")

    def test_invalid_result_does_not_mutate_original_record(self):
        record = claimed()
        before = copy.deepcopy(record)
        result = manifest(record)
        result["task_digest"] = "0" * 64
        with self.assertRaises(ProtocolError):
            apply(record, command("submit", attempt_id=record["attempt"]["id"], result=result))
        self.assertEqual(record, before)

    def test_only_author_can_accept_reject_or_cancel(self):
        record = submitted()
        requests = [
            command("accept", result_id=record["result"]["id"]),
            command("reject", result_id=record["result"]["id"], reason="Missing test."),
            command("cancel"),
        ]
        for request in requests:
            with self.subTest(op=request["op"]):
                after = apply(record, request, actor="runner")
                self.assertEqual(after["status"], "submitted")
                self.assertEqual(after["result"], record["result"])
                self.assertRejected(after, request, "UNAUTHORIZED")

    def test_acceptance_is_bound_to_current_result(self):
        record = submitted()
        wrong = command("accept", result_id=str(uuid.uuid4()))
        after = apply(record, wrong, actor="author")
        self.assertEqual(after["status"], "submitted")
        self.assertRejected(after, wrong, "STALE_RESULT")
        proper = command("accept", result_id=record["result"]["id"])
        self.assertEqual(apply(after, proper, actor="AUTHOR")["status"], "accepted")

    def test_rejected_result_is_retained_but_old_accept_cannot_accept_retry(self):
        first = submitted()
        rejected = apply(first, command("reject", result_id=first["result"]["id"], reason="Add one more edge case."), actor="author")
        self.assertEqual(rejected["status"], "open")
        self.assertIsNone(rejected["result"])
        self.assertIsNone(rejected["attempt"])
        self.assertEqual(rejected["result_history"][0]["id"], first["result"]["id"])
        retry = apply(rejected, command("claim"), now=1003)
        retry = apply(retry, command("submit", attempt_id=retry["attempt"]["id"], result=manifest(retry)), now=1004)
        for op in ("accept", "reject"):
            fields = {"result_id": first["result"]["id"]}
            if op == "reject":
                fields["reason"] = "Delayed old rejection."
            request = command(op, **fields)
            after = apply(retry, request, actor="author", now=1005)
            self.assertEqual(after["status"], "submitted")
            self.assertEqual(after["result"], retry["result"])
            self.assertRejected(after, request, "STALE_RESULT")

    def test_reject_after_final_attempt_fails_task(self):
        record = submitted(max_attempts=1)
        request = command("reject", result_id=record["result"]["id"], reason="Incorrect result.")
        after = apply(record, request, actor="author")
        self.assertEqual(after["status"], "failed")
        self.assertEqual(len(after["result_history"]), 1)

    def test_cancel_immediately_removes_active_attempt_authority(self):
        record = claimed()
        cancelled = apply(record, command("cancel"), actor="author")
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertIsNone(cancelled["attempt"])
        stale = command("submit", attempt_id=record["attempt"]["id"], result=manifest(record))
        after = apply(cancelled, stale)
        self.assertEqual(after["status"], "cancelled")
        self.assertRejected(after, stale)

    def test_altered_pinned_task_is_rejected_before_any_transition(self):
        record = record_fixture()
        record["spec"]["prompt"] = "Changed immutable task."
        with self.assertRaises(ProtocolError) as caught:
            apply(record, command("claim"))
        self.assertEqual(caught.exception.code, "IMMUTABLE_TASK")


if __name__ == "__main__":
    unittest.main()
