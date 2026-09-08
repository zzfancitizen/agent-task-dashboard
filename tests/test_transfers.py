"""Real ZIP/hash/lease tests with only the GitHub boundary replaced."""

import base64
import copy
import hashlib
import io
import json
import os
import stat
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

from taskboard import protocol
from taskboard.protocol import ProtocolError, parse_command
from tests.test_state import claimed


class ArtifactGitHub:
    repo = "team/board"
    hostname = "git.corp.test"

    def __init__(self):
        self.rows = []
        self.uploaded = {}
        self.upload_calls = 0
        self.actor = "runner"
        self.lose_comment = False

    def user(self):
        return self.actor

    def comments(self, issue):
        return copy.deepcopy(self.rows)

    def comment(self, issue, body):
        row = {"id": len(self.rows) + 10, "body": body,
               "user": {"login": self.actor, "type": "User"},
               "created_at": "2026-09-08T00:00:00Z", "updated_at": "2026-09-08T00:00:00Z",
               "issue_url": f"https://{self.hostname}/api/v3/repos/{self.repo}/issues/{issue}"}
        self.rows.append(row)
        if self.lose_comment:
            self.lose_comment = False
            raise ProtocolError("GITHUB_OUTCOME_UNKNOWN", "lost response after post")
        return copy.deepcopy(row)

    def upload_artifacts(self, tag, files):
        self.upload_calls += 1
        urls = {}
        for file in files:
            value = file.read_bytes()
            key = (tag, file.name)
            if key in self.uploaded:
                assert self.uploaded[key] == value, "never replace prior asset bytes"
            self.uploaded[key] = value
            urls[file.name] = f"https://{self.hostname}/{self.repo}/releases/download/{tag}/{file.name}"
        return urls


class TransferTests(unittest.TestCase):
    def setUp(self):
        try:
            from taskboard import transfers
        except ImportError as error:
            self.fail(f"Artifact bridge is required: {error}")
        self.transfers = transfers
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.record = claimed()
        self.task = self.record["spec"]
        self.attempt = self.record["attempt"]["id"]
        self.api = ArtifactGitHub()
        summary = self.directory / "summary.md"
        summary.write_bytes(b"Completed.\n")
        self.files = {"summary.md": summary}
        self.report = {"summary": "Completed.", "assumptions": [], "unresolved": [],
                       "usage": None, "checks": []}

    def prepare(self):
        return self.transfers.prepare_bundle(self.task, self.attempt, self.files, self.report)

    def request(self, bundle=None):
        bundle = bundle or self.prepare()
        upload = self.transfers.post_bundle_chunks(self.api, 7, self.task, self.attempt, bundle)
        return {"op": "submit_bundle", "request_id": str(uuid.uuid4()), "revision": 1,
                "attempt_id": self.attempt, "upload": upload, "report": bundle["report"]}

    def resolve(self, command, **options):
        return self.transfers.resolve_bundle(self.record, command, self.api.comments(7), self.api,
                                             actor=options.get("actor", "runner"),
                                             now=options.get("now", 1002))

    def replaced_archive(self, data):
        bundle = self.prepare()
        bundle["data"] = data
        bundle["sha256"] = hashlib.sha256(data).hexdigest()
        return self.request(bundle)

    def test_bundle_bytes_and_upload_id_are_deterministic_and_files_are_sorted(self):
        second = self.directory / "second"
        second.write_bytes(b"A nested artifact.")
        self.files["nested/second.txt"] = second
        first = self.prepare()
        os.utime(second, (1900000000, 1900000000))
        self.files = dict(reversed(list(self.files.items())))
        repeated = self.prepare()
        self.assertEqual(first, repeated)
        self.assertEqual(first["sha256"], hashlib.sha256(first["data"]).hexdigest())
        with zipfile.ZipFile(io.BytesIO(first["data"])) as archive:
            self.assertEqual(archive.namelist(), ["nested/second.txt", "summary.md"])
            self.assertEqual(archive.read("summary.md"), b"Completed.\n")
            self.assertTrue(all(item.date_time == (1980, 1, 1, 0, 0, 0)
                                for item in archive.infolist()))

    def test_local_size_and_symlink_limits_reject_before_any_comment(self):
        original = self.files["summary.md"]
        link = self.directory / "link"
        link.symlink_to(original)
        self.files["summary.md"] = link
        with self.assertRaises(ProtocolError):
            self.prepare()
        self.files["summary.md"] = original
        with original.open("wb") as stream:
            stream.truncate(20 * 1024 * 1024 + 1)
        with self.assertRaises(ProtocolError):
            self.prepare()
        original.write_bytes(os.urandom(1024 * 1024 + 1))
        with self.assertRaises(ProtocolError) as caught:
            self.prepare()
        self.assertEqual(caught.exception.code, "BUNDLE_TOO_LARGE")
        self.assertEqual(self.api.rows, [])

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO path is Unix-only")
    def test_nonregular_local_files_are_rejected_before_potentially_blocking_open(self):
        fifo = self.directory / "fifo"
        os.mkfifo(fifo)
        self.files["summary.md"] = fifo
        with patch("taskboard.transfers.os.open", side_effect=AssertionError("Must not open a FIFO")):
            with self.assertRaises(ProtocolError):
                self.prepare()

    def test_parent_symlink_and_logical_path_collisions_are_rejected(self):
        link = self.directory / "linked"
        link.symlink_to(self.directory, target_is_directory=True)
        self.files["summary.md"] = link / "summary.md"
        with self.assertRaises(ProtocolError):
            self.prepare()
        self.files["summary.md"] = self.directory / "summary.md"
        for name in ("../escape", ".git/config", "SUMMARY.md", "summary.md/child"):
            with self.subTest(name=name):
                self.files[name] = self.directory / "summary.md"
                with self.assertRaises(ProtocolError):
                    self.prepare()
                del self.files[name]

    def test_verification_records_require_the_actual_matching_evidence_file(self):
        self.report["checks"] = [{"argv": ["python3", "-m", "unittest"], "exit_code": 0,
                                  "stdout": "OK", "stderr": ""}]
        with self.assertRaises(ProtocolError):
            self.prepare()
        evidence = self.directory / "verification.json"
        evidence.write_text(json.dumps({"commands": self.report["checks"]}))
        self.files["verification.json"] = evidence
        request = self.request()
        resolved = self.resolve(request)
        check = resolved["result"]["verification"][0]
        self.assertEqual(check["argv"], ["python3", "-m", "unittest"])
        self.assertEqual(check["exit_code"], 0)
        evidence_asset = next(a for a in resolved["result"]["artifacts"]
                              if a["name"] == "verification.json")
        self.assertEqual(check["evidence"], evidence_asset["uri"])
        evidence.write_text('{"commands":[]}')
        with self.assertRaises(ProtocolError):
            self.prepare()

    def test_chunks_are_bounded_and_do_not_trigger_command_parser(self):
        self.files["summary.md"].write_bytes(os.urandom(70000))
        request = self.request()
        self.assertEqual(len(request["upload"]["comment_ids"]), 3)
        for index, row in enumerate(self.api.rows):
            self.assertIsNone(parse_command(row["body"]))
            payload = protocol.parse_artifact_chunk(row["body"])
            self.assertEqual(payload["index"], index)
            self.assertEqual(payload["total"], 3)
            self.assertLessEqual(len(base64.b64decode(payload["data"])), 30 * 1024)
            self.assertLessEqual(len(row["body"].encode()), 48 * 1024)

    def test_lost_chunk_response_resumes_identical_parts_without_reposting(self):
        bundle = self.prepare()
        self.api.lose_comment = True
        with self.assertRaises(ProtocolError):
            self.request(bundle)
        request = self.request(bundle)
        self.assertEqual(len(self.api.rows), 1)
        self.assertEqual(request["upload"]["comment_ids"], [10])

    def test_claim_owner_is_checked_before_archive_or_release_work(self):
        request = self.request()
        for actor, now, code in (("intruder", 1002, "UNAUTHORIZED"),
                                  ("runner", self.record["attempt"]["expires_at"], "STALE_ATTEMPT")):
            with self.subTest(actor=actor):
                with self.assertRaises(ProtocolError) as caught:
                    self.resolve(request, actor=actor, now=now)
                self.assertEqual(caught.exception.code, code)
        request["revision"] = 2
        with self.assertRaises(ProtocolError) as caught:
            self.resolve(request)
        self.assertEqual(caught.exception.code, "REVISION_MISMATCH")
        self.assertEqual(self.api.uploaded, {})

    def test_resolved_manifest_preserves_logical_names_with_safe_unique_release_names(self):
        nested = self.directory / "nested"
        nested.write_bytes(b"Nested result")
        self.files["folder/summary.md"] = nested
        request = self.request()
        resolved = self.resolve(request)
        self.assertEqual(resolved["op"], "submit")
        self.assertEqual(resolved["request_id"], request["request_id"])
        manifest = protocol.validate_result(resolved["result"], self.task, self.attempt)
        self.assertEqual({item["name"] for item in manifest["artifacts"]},
                         {"summary.md", "folder/summary.md"})
        self.assertEqual(set(self.api.uploaded.values()), {b"Completed.\n", b"Nested result"})
        self.assertEqual(len({name for _, name in self.api.uploaded}), 2)
        self.assertTrue(all("/" not in name for _, name in self.api.uploaded))

    def test_edited_foreign_missing_repeated_or_misbound_chunks_never_upload(self):
        request = self.request()
        pristine = copy.deepcopy(self.api.rows)
        mutations = (
            lambda row: row.update(updated_at="2026-09-09T00:00:00Z"),
            lambda row: row["user"].update(login="intruder"),
            lambda row: row["user"].update(type="Bot"),
            lambda row: row.update(issue_url="https://git.corp.test/api/v3/repos/team/board/issues/8"),
            lambda row: row.pop("updated_at"),
        )
        for mutate in mutations:
            self.api.rows = copy.deepcopy(pristine)
            mutate(self.api.rows[0])
            with self.assertRaises(ProtocolError):
                self.resolve(request)
        self.api.rows = []
        with self.assertRaises(ProtocolError):
            self.resolve(request)
        self.api.rows = copy.deepcopy(pristine) * 2
        with self.assertRaises(ProtocolError):
            self.resolve(request)
        self.api.rows = copy.deepcopy(pristine)
        payload = protocol.parse_artifact_chunk(self.api.rows[0]["body"])
        payload["attempt_id"] = str(uuid.uuid4())
        self.api.rows[0]["body"] = protocol.format_artifact_chunk(payload)
        with self.assertRaises(ProtocolError):
            self.resolve(request)
        self.assertEqual(self.api.uploaded, {})

    def test_archive_and_file_hash_mismatches_never_upload(self):
        request = self.request()
        request["upload"]["sha256"] = "0" * 64
        with self.assertRaises(ProtocolError):
            self.resolve(request)
        request = self.request()
        request["report"]["artifacts"][0]["sha256"] = "0" * 64
        with self.assertRaises(ProtocolError):
            self.resolve(request)
        self.assertEqual(self.api.uploaded, {})

    def test_zip_traversal_symlinks_duplicates_extra_entries_and_bombs_are_rejected(self):
        for kind in ("traversal", "symlink", "duplicate", "extra", "bomb", "nul", "dosdirectory"):
            with self.subTest(kind=kind):
                self.api.rows = []
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    name = "../summary.md" if kind == "traversal" else "summary.md"
                    if kind == "nul":
                        name = "summary.mdXevil"
                    info = zipfile.ZipInfo(name)
                    info.create_system = 3
                    info.external_attr = ((stat.S_IFLNK | 0o777) if kind == "symlink"
                                          else (stat.S_IFREG | 0o600)) << 16
                    if kind == "dosdirectory":
                        info.external_attr = 0x10
                    info.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(info, b"x" * (20 * 1024 * 1024 + 1)
                                     if kind == "bomb" else b"Completed.\n")
                    if kind == "duplicate":
                        import warnings
                        with warnings.catch_warnings():
                            warnings.simplefilter("ignore", UserWarning)
                            archive.writestr("summary.md", b"Completed.\n")
                    elif kind == "extra":
                        archive.writestr("result.json", b"internal metadata collision")
                data = buffer.getvalue()
                if kind == "nul":
                    data = data.replace(b"summary.mdXevil", b"summary.md\x00evil")
                request = self.replaced_archive(data)
                with self.assertRaises(ProtocolError):
                    self.resolve(request)
        self.assertEqual(self.api.uploaded, {})

    def test_malformed_zip_offsets_and_utf8_are_durable_protocol_rejections(self):
        from taskboard.controller import process_command
        damaged_archives = (
            "504b03041400e100280033b1285d141c34b50d0000000b0000000a00000073756d6d6172792e6d6473cecf2dc8492d494dd1e30200504b0102140314000000080033b1285d141c34b50d0000000b0000000a000000000000000000000080010000000073756d6d6172792e6d64504b0506000000000100010038000000390000000000",
            "504b030414000000080033b1285d141c34b50d0000000b0000000a00000073756d6d6172792e6d6473cecf2dc8492d494dd1e30200504b01021403140000ea080033f0285d141c34b50d0000000b0000000a000000000000000000000080010000000073756d6d6172d12e6d64504b0506000000000100010038000000350000000000",
        )
        for payload in damaged_archives:
            with self.subTest(payload=payload):
                self.api.rows = []
                request = self.replaced_archive(bytes.fromhex(payload))
                result = process_command(self.record, request, self.api.comments(7), self.api,
                                         actor="runner", comment_id=99, now=1002)
                self.assertEqual(result["processed"][request["request_id"]]["code"], "INVALID_ARCHIVE")
        self.assertEqual(self.api.uploaded, {})

    def test_raw_request_replay_skips_missing_chunks_and_changed_payload_conflicts(self):
        from taskboard.controller import process_command
        request = self.request()
        once = process_command(self.record, request, self.api.comments(7), self.api,
                               actor="runner", comment_id=99, now=1002)
        self.assertEqual(once["status"], "submitted")
        self.api.rows = []
        replay = process_command(once, request, [], self.api,
                                 actor="RUNNER", comment_id=100, now=9999)
        self.assertEqual(once, replay)
        self.assertEqual(self.api.upload_calls, 1)
        changed = copy.deepcopy(request)
        changed["report"]["summary"] = "Changed payload"
        with self.assertRaises(ProtocolError) as caught:
            process_command(once, changed, [], self.api, actor="runner", comment_id=101, now=9999)
        self.assertEqual(caught.exception.code, "IDEMPOTENCY_CONFLICT")
        self.assertEqual(self.api.upload_calls, 1)

    def test_idempotent_bundle_replay_still_refuses_mutated_pinned_task_content(self):
        from taskboard.controller import process_command
        request = self.request()
        once = process_command(self.record, request, self.api.comments(7), self.api,
                               actor="runner", comment_id=99, now=1002)
        once["spec"]["prompt"] = "Replace the original task content."
        with self.assertRaises(ProtocolError) as caught:
            process_command(once, request, [], self.api, actor="runner", comment_id=100, now=1003)
        self.assertEqual(caught.exception.code, "IMMUTABLE_TASK")
        with self.assertRaises(ProtocolError) as caught:
            self.transfers.resolve_bundle(once, request, [], self.api, actor="runner", now=1003)
        self.assertEqual(caught.exception.code, "IMMUTABLE_TASK")

    def test_hydrated_manifest_size_is_checked_before_any_release_write(self):
        empty = self.directory / "empty"
        empty.write_bytes(b"")
        self.files.update({f"file-{index:03}.txt": empty for index in range(140)})
        bundle = self.prepare()
        self.assertLess(len(bundle["data"]), 30 * 1024)
        row = self.api.comment(7, protocol.format_artifact_chunk({
            "upload_id": bundle["upload_id"], "task_id": self.task["task_id"], "revision": 1,
            "task_digest": self.record["digest"], "attempt_id": self.attempt,
            "index": 0, "total": 1, "sha256": bundle["sha256"],
            "data": base64.b64encode(bundle["data"]).decode(),
        }))
        request = {"op": "submit_bundle", "request_id": str(uuid.uuid4()), "revision": 1,
                   "attempt_id": self.attempt, "report": bundle["report"],
                   "upload": {"id": bundle["upload_id"], "sha256": bundle["sha256"],
                              "bytes": len(bundle["data"]), "comment_ids": [row["id"]]}}
        with self.assertRaises(ProtocolError) as caught:
            self.resolve(request)
        self.assertEqual(caught.exception.code, "PAYLOAD_TOO_LARGE")
        self.assertEqual(self.api.uploaded, {})

    def test_many_small_artifacts_are_rejected_before_posting_first_chunk(self):
        empty = self.directory / "empty"
        empty.write_bytes(b"")
        self.files.update({f"file-{index:03}.txt": empty for index in range(140)})
        bundle = self.prepare()
        with self.assertRaises(ProtocolError) as caught:
            self.request(bundle)
        self.assertEqual(caught.exception.code, "PAYLOAD_TOO_LARGE")
        self.assertEqual(self.api.rows, [])

    def test_interrupted_release_upload_does_not_acknowledge_or_reexecute_the_attempt(self):
        from taskboard.controller import process_command
        request = self.request()
        original = self.api.upload_artifacts

        def uncertain(tag, files):
            original(tag, files)
            raise ProtocolError("GITHUB_OUTCOME_UNKNOWN", "lost release response")

        with patch.object(self.api, "upload_artifacts", side_effect=uncertain):
            with self.assertRaises(ProtocolError):
                process_command(self.record, request, self.api.comments(7), self.api,
                                actor="runner", comment_id=99, now=1002)
        self.assertEqual(self.record["status"], "claimed")
        self.assertNotIn(request["request_id"], self.record["processed"])
        retained = copy.deepcopy(self.api.uploaded)
        recovered = process_command(self.record, request, self.api.comments(7), self.api,
                                    actor="runner", comment_id=99, now=1003)
        self.assertEqual(recovered["status"], "submitted")
        self.assertEqual(self.api.uploaded, retained)

    def test_stale_bundle_rejection_is_durable_and_idempotent_without_upload(self):
        from taskboard.controller import process_command
        request = self.request()
        deadline = self.record["attempt"]["expires_at"]
        rejected = process_command(self.record, request, [], self.api,
                                   actor="runner", comment_id=99, now=deadline)
        self.assertEqual(rejected["status"], "open")
        self.assertEqual(rejected["processed"][request["request_id"]]["code"], "STALE_ATTEMPT")
        replay = process_command(rejected, request, [], self.api,
                                 actor="runner", comment_id=100, now=deadline + 1)
        self.assertEqual(rejected, replay)
        self.assertEqual(self.api.uploaded, {})

    def test_pure_reducer_refuses_unresolved_bundle_instead_of_cancelling(self):
        from taskboard.state import apply_command
        request = self.request()
        with self.assertRaises(ProtocolError) as caught:
            apply_command(self.record, request, actor="runner", comment_id=99, now=1002)
        self.assertEqual(caught.exception.code, "BUNDLE_NOT_RESOLVED")
        self.assertEqual(self.record["status"], "claimed")


if __name__ == "__main__":
    unittest.main()
