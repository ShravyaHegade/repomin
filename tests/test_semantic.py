import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

from repomin.model import FailureSpec, ReductionStats, RunResult
from repomin.oracle import FailureOracle
from repomin.semantic import (
    HttpSemanticBackend,
    NoopSemanticBackend,
    SemanticError,
    SemanticReducer,
    build_prompt,
    collect_text_files,
    edits_to_candidates,
    parse_edits,
)
from repomin.session import IgnoreSet, ReductionSession


class NoopSemanticBackendTest(unittest.TestCase):
    def test_noop_returns_no_candidates(self) -> None:
        backend = NoopSemanticBackend()
        self.assertEqual("none", backend.name)
        self.assertEqual((), backend.propose(SimpleNamespace()))


class PromptAndCollectionTest(unittest.TestCase):
    def test_prompt_contains_failure_and_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.txt").write_text("alpha\n", encoding="utf-8")
            session = SimpleNamespace(
                current=root,
                identity={"command": "python repro.py"},
                oracle=SimpleNamespace(
                    spec=FailureSpec("ORIGINAL_FAILURE", 1),
                    java_exception_signature=None,
                    python_exception_signature=None,
                    process_failure_signature=None,
                ),
                ignores=IgnoreSet(),
            )
            prompt = build_prompt(session)
            self.assertIn("python repro.py", prompt)
            self.assertIn("ORIGINAL_FAILURE", prompt)
            self.assertIn("a.txt", prompt)
            self.assertIn("alpha", prompt)

    def test_collect_text_files_skips_ignored_and_binary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "keep.txt").write_text("kept\n", encoding="utf-8")
            (root / "skip.txt").write_text("skipped\n", encoding="utf-8")
            (root / "binary.dat").write_bytes(b"\x00\x01\x02")
            session = SimpleNamespace(
                current=root,
                ignores=IgnoreSet(paths=("skip.txt",)),
            )
            collected = dict(collect_text_files(session))
            self.assertIn("keep.txt", collected)
            self.assertNotIn("skip.txt", collected)
            self.assertNotIn("binary.dat", collected)


class EditParsingTest(unittest.TestCase):
    def test_parse_edits_accepts_object_and_list(self) -> None:
        self.assertEqual(
            [{"path": "a.txt", "delete": True}],
            parse_edits('{"edits":[{"path":"a.txt","delete":true}]}'),
        )
        self.assertEqual(
            [{"path": "a.txt", "replace": "x"}],
            parse_edits('[{"path":"a.txt","replace":"x"}]'),
        )

    def test_parse_edits_rejects_malformed_json(self) -> None:
        with self.assertRaises(SemanticError):
            parse_edits("not json")

    def test_parse_edits_accepts_markdown_fenced_json(self) -> None:
        self.assertEqual(
            [{"path": "a.txt", "delete": True}],
            parse_edits(
                '```json\n{"edits":[{"path":"a.txt","delete":true}]}\n```'
            ),
        )

    def test_edits_reject_unsafe_paths(self) -> None:
        with self.assertRaises(SemanticError):
            edits_to_candidates(
                SimpleNamespace(keeps=lambda relative: False),
                [{"path": "../outside.txt", "delete": True}],
            )

    def test_edits_reject_ambiguous_operations(self) -> None:
        with self.assertRaises(SemanticError):
            edits_to_candidates(
                SimpleNamespace(keeps=lambda relative: False),
                [{"path": "a.txt", "delete": True, "replace": "x"}],
            )

    def test_replace_candidate_writes_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.txt").write_text("old\n", encoding="utf-8")
            session = SimpleNamespace(keeps=lambda relative: False)
            candidate = edits_to_candidates(
                session,
                [{"path": "a.txt", "replace": "new\n"}],
            )[0]
            self.assertTrue(candidate.mutation(root))
            self.assertEqual("new\n", (root / "a.txt").read_text(encoding="utf-8"))

    def test_delete_candidate_respects_keep(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.txt").write_text("old\n", encoding="utf-8")
            session = SimpleNamespace(keeps=lambda relative: relative.as_posix() == "a.txt")
            candidate = edits_to_candidates(
                session,
                [{"path": "a.txt", "delete": True}],
            )[0]
            self.assertFalse(candidate.mutation(root))
            self.assertTrue((root / "a.txt").exists())


class HttpSemanticBackendTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = self._serve(_MockCompletionsHandler)

    def _serve(self, handler) -> ThreadingHTTPServer:
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def stop() -> None:
            server.shutdown()
            server.server_close()
            thread.join()

        self.addCleanup(stop)
        return server

    def test_complete_returns_message_content(self) -> None:
        backend = HttpSemanticBackend(
            "http://127.0.0.1:%d/v1/chat/completions" % self.server.server_port,
            "test-model",
        )
        content = backend._complete("hello")
        self.assertEqual('{"edits":[]}', content)

    def test_complete_rejects_non_choice_payload(self) -> None:
        server = self._serve(_EmptyChoicesHandler)
        backend = HttpSemanticBackend(
            "http://127.0.0.1:%d/v1/chat/completions" % server.server_port,
            "test-model",
        )
        with self.assertRaises(SemanticError):
            backend._complete("hello")


class _MockCompletionsHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        payload = {
            "choices": [
                {
                    "message": {
                        "content": '{"edits":[]}',
                    }
                }
            ],
            "model": body.get("model"),
        }
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        return


class _EmptyChoicesHandler(_MockCompletionsHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        data = json.dumps({"choices": []}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class SemanticReducerIntegrationTest(unittest.TestCase):
    def test_empty_semantic_proposal_still_counts_a_call(self) -> None:
        class AlwaysFailRunner:
            def run(self, cwd: Path) -> RunResult:
                return RunResult(1, "ORIGINAL_FAILURE", "", 0.0)

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.mkdir()
            (source / "required.txt").write_text("required\n", encoding="utf-8")
            oracle = FailureOracle(
                AlwaysFailRunner(),
                FailureSpec("ORIGINAL_FAILURE"),
            )
            session = ReductionSession(
                source,
                oracle,
                ReductionStats(source_files=1, source_bytes=9),
            )
            try:
                session.verify_baseline(1)

                class EmptyBackend:
                    name = "fake"

                    def propose(self, session_obj):
                        return []

                reducer = SemanticReducer(session, EmptyBackend())
                reducer.reduce()
                self.assertEqual(1, session.stats.semantic_calls)
                self.assertEqual(0, session.stats.semantic_accepted)
            finally:
                session.close()

    def test_accepted_semantic_candidate_updates_stats(self) -> None:
        class MarkerRunner:
            def run(self, cwd: Path) -> RunResult:
                failed = "ORIGINAL_FAILURE" if (cwd / "marker.txt").exists() else "OK"
                return RunResult(1 if failed == "ORIGINAL_FAILURE" else 0, failed, "", 0.0)

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.mkdir()
            (source / "marker.txt").write_text("needed\n", encoding="utf-8")
            (source / "unused.txt").write_text("unused\n", encoding="utf-8")
            oracle = FailureOracle(
                MarkerRunner(),
                FailureSpec("ORIGINAL_FAILURE"),
            )
            session = ReductionSession(
                source,
                oracle,
                ReductionStats(source_files=2, source_bytes=13),
            )
            try:
                session.verify_baseline(1)

                class FakeBackend:
                    name = "fake"

                    def propose(self, session_obj):
                        return edits_to_candidates(
                            session_obj,
                            [{"path": "unused.txt", "delete": True}],
                        )

                reducer = SemanticReducer(session, FakeBackend())
                reducer.reduce()
                self.assertEqual(1, session.stats.semantic_calls)
                self.assertEqual(1, session.stats.semantic_accepted)
                self.assertFalse((session.current / "unused.txt").exists())
            finally:
                session.close()


if __name__ == "__main__":
    unittest.main()
