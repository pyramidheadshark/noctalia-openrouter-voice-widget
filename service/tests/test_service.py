# pyright: reportUninitializedInstanceVariable=false

from __future__ import annotations

import json
import os
import signal
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any, cast

import service.noctalia_service as noctalia_service
from service import ipc_client
from service.noctalia_service import ServiceError, VoiceWidgetService


SERVICE_FILE = Path(__file__).resolve().parents[1] / "noctalia_service.py"
REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_AUDIO_PATH = REPO_ROOT / "tests" / "fixtures" / "audio" / "deterministic-sample.wav"
FIXTURE_WAV_BYTES = FIXTURE_AUDIO_PATH.read_bytes()


class ServiceFixtureTest(unittest.TestCase):
    temp_home: tempfile.TemporaryDirectory[str]
    temp_runtime: tempfile.TemporaryDirectory[str]
    original_home: str | None
    original_runtime: str | None
    original_live_config_path: Path
    original_secret_path: Path

    def setUp(self) -> None:
        self.temp_home = tempfile.TemporaryDirectory()
        self.temp_runtime = tempfile.TemporaryDirectory()
        self.original_home = os.environ.get("HOME")
        self.original_runtime = os.environ.get("XDG_RUNTIME_DIR")
        os.environ["HOME"] = self.temp_home.name
        os.environ["XDG_RUNTIME_DIR"] = self.temp_runtime.name
        self.original_live_config_path = noctalia_service.LIVE_CONFIG_PATH
        self.original_secret_path = noctalia_service.SECRET_PATH
        noctalia_service.LIVE_CONFIG_PATH = (
            Path(self.temp_home.name) / ".config" / "noctalia-openrouter-voice-widget" / "config.json"
        )
        noctalia_service.SECRET_PATH = (
            Path(self.temp_home.name) / ".local" / "state" / "noctalia-openrouter-voice-widget" / "openrouter.key"
        )

    def tearDown(self) -> None:
        noctalia_service.LIVE_CONFIG_PATH = self.original_live_config_path
        noctalia_service.SECRET_PATH = self.original_secret_path
        if self.original_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self.original_home

        if self.original_runtime is None:
            os.environ.pop("XDG_RUNTIME_DIR", None)
        else:
            os.environ["XDG_RUNTIME_DIR"] = self.original_runtime

        self.temp_runtime.cleanup()
        self.temp_home.cleanup()

    def connect_db(self) -> sqlite3.Connection:
        db_path = Path(self.temp_home.name) / ".local" / "state" / "noctalia-openrouter-voice-widget" / "history.sqlite3"
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def write_fixture_audio(self, audio_path: Path) -> None:
        audio_path.write_bytes(FIXTURE_WAV_BYTES)


class IpcClientContractTest(unittest.TestCase):
    def _serve_once(
        self,
        socket_path: Path,
        response_chunks: list[bytes],
        delay_before_response: float = 0.0,
        inter_chunk_delay: float = 0.0,
        delay_after_response: float = 0.0,
    ) -> threading.Thread:
        def run_server() -> None:
            if socket_path.exists():
                socket_path.unlink()
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(str(socket_path))
                server.listen(1)
                connection, _ = server.accept()
                with connection:
                    connection.recv(65536)
                    if delay_before_response > 0:
                        time.sleep(delay_before_response)
                    for index, chunk in enumerate(response_chunks):
                        try:
                            connection.sendall(chunk)
                        except BrokenPipeError:
                            return
                        if inter_chunk_delay > 0 and index < len(response_chunks) - 1:
                            time.sleep(inter_chunk_delay)
                    if delay_after_response > 0:
                        time.sleep(delay_after_response)

        thread = threading.Thread(target=run_server)
        thread.start()
        deadline = time.time() + 2
        while time.time() < deadline:
            if socket_path.exists():
                return thread
            time.sleep(0.01)
        self.fail("test IPC server did not create socket in time")

    def test_send_request_reads_multichunk_newline_terminated_response(self) -> None:
        with tempfile.TemporaryDirectory() as temp_runtime:
            socket_path = Path(temp_runtime) / "ipc-test.sock"
            response = {
                "ok": True,
                "requestId": "test-request",
                "command": "listHistory",
                "result": {"items": [{"jobId": "job-1", "rawTranscript": "x" * 70000}]},
            }
            payload = (json.dumps(response) + "\n").encode("utf-8")
            thread = self._serve_once(socket_path, [payload[:32000], payload[32000:]])
            self.addCleanup(thread.join, 1)

            result = ipc_client.send_request(
                str(socket_path),
                {"requestId": "test-request", "command": "listHistory", "params": {}},
                timeout_seconds=1.0,
            )

            thread.join(timeout=1)
            self.assertTrue(result["ok"])
            items = cast(list[dict[str, Any]], cast(dict[str, Any], result["result"])["items"])
            self.assertEqual(items[0]["jobId"], "job-1")
            self.assertEqual(len(cast(str, items[0]["rawTranscript"])), 70000)

    def test_send_request_times_out_waiting_for_response(self) -> None:
        with tempfile.TemporaryDirectory() as temp_runtime:
            socket_path = Path(temp_runtime) / "ipc-timeout.sock"
            thread = self._serve_once(
                socket_path,
                [b'{"ok": true}\n'],
                delay_before_response=0.3,
            )
            self.addCleanup(thread.join, 1)

            with self.assertRaises(RuntimeError) as captured:
                ipc_client.send_request(
                    str(socket_path),
                    {"requestId": "test-request", "command": "snapshot", "params": {}},
                    timeout_seconds=0.1,
                )

            thread.join(timeout=1)
            self.assertIn("Timed out waiting for helper response", str(captured.exception))

    def test_send_request_enforces_absolute_deadline_for_trickled_partial_response(self) -> None:
        with tempfile.TemporaryDirectory() as temp_runtime:
            socket_path = Path(temp_runtime) / "ipc-trickle-timeout.sock"
            thread = self._serve_once(
                socket_path,
                [b'{"ok": ', b'true'],
                inter_chunk_delay=0.07,
                delay_after_response=0.2,
            )
            self.addCleanup(thread.join, 1)

            started_at = time.monotonic()
            with self.assertRaises(RuntimeError) as captured:
                ipc_client.send_request(
                    str(socket_path),
                    {"requestId": "test-request", "command": "snapshot", "params": {}},
                    timeout_seconds=0.1,
                )
            elapsed_seconds = time.monotonic() - started_at

            thread.join(timeout=1)
            self.assertIn("Timed out waiting for helper response", str(captured.exception))
            self.assertLess(elapsed_seconds, 0.18)


class ServiceRuntimeTest(unittest.TestCase):
    temp_home: tempfile.TemporaryDirectory[str]
    temp_runtime: tempfile.TemporaryDirectory[str]
    env: dict[str, str]
    process: subprocess.Popen[str]
    socket_path: Path

    def setUp(self) -> None:
        self.temp_home = tempfile.TemporaryDirectory()
        self.temp_runtime = tempfile.TemporaryDirectory()
        self.env = os.environ.copy()
        self.env["HOME"] = self.temp_home.name
        self.env["XDG_RUNTIME_DIR"] = self.temp_runtime.name
        self.env["NOCTALIA_OPENROUTER_STT_FIXTURE_TEXT"] = "runtime fixture transcript"
        self.env["NOCTALIA_OPENROUTER_CLEANUP_FIXTURE_TEXT"] = "runtime cleaned transcript"
        self.process = subprocess.Popen(
            ["python3", str(SERVICE_FILE)],
            env=self.env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self.socket_path = Path(self.temp_runtime.name) / "noctalia-openrouter-voice-widget.sock"
        deadline = time.time() + 10
        while time.time() < deadline:
            if self.socket_path.exists():
                return
            time.sleep(0.05)
        self.fail("service did not create socket in time")

    def tearDown(self) -> None:
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
            self.process.wait(timeout=10)
        self.temp_runtime.cleanup()
        self.temp_home.cleanup()

    def send(self, command: str, params: dict[str, object] | None = None) -> dict[str, Any]:
        payload = {
            "requestId": "test-request",
            "command": command,
            "params": params or {},
        }
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(self.socket_path))
            client.sendall((json.dumps(payload) + "\n").encode("utf-8"))
            response = client.recv(65536)
        return cast(dict[str, Any], json.loads(response.decode("utf-8")))

    def write_fixture_audio(self, audio_path: Path) -> None:
        audio_path.write_bytes(FIXTURE_WAV_BYTES)

    def test_snapshot_reports_idle_service(self) -> None:
        response = self.send("snapshot")
        self.assertTrue(response["ok"])
        result = cast(dict[str, Any], response["result"])
        self.assertEqual(result["state"]["lifecycleState"], "idle")
        self.assertEqual(result["protocolVersion"], "v1")
        self.assertTrue(result["health"]["historyDbReady"])

    def test_recording_state_machine_reaches_ready_and_cleans_temp_audio(self) -> None:
        settings_response = self.send(
            "saveSettings",
            {"settings": {"defaultPromptPresetId": "custom-preset"}},
        )
        self.assertTrue(settings_response["ok"])

        secret_response = self.send("saveSecret", {"secret": "test-key"})
        self.assertTrue(secret_response["ok"])

        start_response = self.send("startRecording")
        self.assertTrue(start_response["ok"])
        start_result = cast(dict[str, Any], start_response["result"])
        job = cast(dict[str, Any], start_result["job"])
        job_id = cast(str, job["jobId"])
        audio_path = Path(cast(str, job["audioPath"]))
        self.assertTrue(audio_path.exists())
        self.write_fixture_audio(audio_path)

        stop_response = self.send("stopRecording")
        self.assertTrue(stop_response["ok"])
        stop_result = cast(dict[str, Any], stop_response["result"])
        self.assertEqual(stop_result["transitionPath"], ["finalizing", "transcribing", "postprocessing", "ready"])
        self.assertEqual(stop_result["state"]["lifecycleState"], "ready")
        self.assertTrue(stop_result["job"]["tempAudioRemoved"])
        self.assertFalse(audio_path.exists())

        history_response = self.send("listHistory", {"limit": 5})
        self.assertTrue(history_response["ok"])
        history_result = cast(dict[str, Any], history_response["result"])
        history_items = cast(list[dict[str, Any]], history_result["items"])
        self.assertEqual(history_result["count"], 1)
        self.assertEqual(history_items[0]["jobId"], job_id)
        self.assertEqual(history_items[0]["status"], "ready")

        db_path = Path(self.temp_home.name) / ".local" / "state" / "noctalia-openrouter-voice-widget" / "history.sqlite3"
        with sqlite3.connect(db_path) as connection:
            status, raw_transcript, processed_transcript, prompt_preset_id, prompt_snapshot_hash, stt_model_id, cleanup_model_id, metadata_json = connection.execute(
                "SELECT status, raw_transcript, processed_transcript, prompt_preset_id, prompt_snapshot_hash, stt_model_id, cleanup_model_id, request_metadata_json FROM transcript_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        self.assertEqual(status, "ready")
        self.assertEqual(raw_transcript, "runtime fixture transcript")
        self.assertEqual(processed_transcript, "runtime cleaned transcript")
        self.assertEqual(prompt_preset_id, "custom-preset")
        self.assertTrue(cast(str, prompt_snapshot_hash))
        self.assertEqual(stt_model_id, "openai/whisper-large-v3")
        self.assertEqual(cleanup_model_id, "google/gemini-3-flash-preview")
        metadata = cast(dict[str, Any], json.loads(cast(str, metadata_json)))
        self.assertNotIn("audioPath", metadata)
        self.assertEqual(metadata["stt"]["audioFormat"], "wav")
        self.assertEqual(metadata["stt"]["audioBytes"], len(FIXTURE_WAV_BYTES))
        self.assertIn('"transport": "fixture"', cast(str, metadata_json))

    def test_mock_provider_qa_mode_runs_without_secret_or_manual_audio_and_supports_export(self) -> None:
        start_response = self.send(
            "startRecording",
            {"sessionId": "qa-session", "mockProvider": "deterministic"},
        )
        self.assertTrue(start_response["ok"])
        job = cast(dict[str, Any], cast(dict[str, Any], start_response["result"])["job"])
        job_id = cast(str, job["jobId"])
        audio_path = Path(cast(str, job["audioPath"]))
        self.assertTrue(audio_path.exists())
        self.assertEqual(audio_path.read_bytes(), FIXTURE_WAV_BYTES)

        stop_response = self.send("stopRecording")
        self.assertTrue(stop_response["ok"])
        stop_result = cast(dict[str, Any], stop_response["result"])
        self.assertEqual(stop_result["transitionPath"], ["finalizing", "transcribing", "postprocessing", "ready"])
        self.assertEqual(stop_result["state"]["lifecycleState"], "ready")
        self.assertTrue(cast(dict[str, Any], stop_result["job"])["tempAudioRemoved"])
        self.assertFalse(audio_path.exists())

        export_response = self.send("exportItem", {"jobId": job_id})
        self.assertTrue(export_response["ok"])
        export_path = Path(cast(str, cast(dict[str, Any], export_response["result"])["exportPath"]))
        export_content = export_path.read_text(encoding="utf-8")
        self.assertIn("## Raw Transcript\n\nruntime fixture transcript", export_content)
        self.assertIn("## Processed Transcript\n\nruntime cleaned transcript", export_content)

        db_path = Path(self.temp_home.name) / ".local" / "state" / "noctalia-openrouter-voice-widget" / "history.sqlite3"
        with sqlite3.connect(db_path) as connection:
            metadata_json = connection.execute(
                "SELECT request_metadata_json FROM transcript_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()[0]
        metadata = cast(dict[str, Any], json.loads(cast(str, metadata_json)))
        self.assertEqual(metadata["qa"]["mockProvider"], "deterministic")
        self.assertEqual(metadata["qa"]["fixtureAudioFile"], "deterministic-sample.wav")
        self.assertEqual(metadata["stt"]["transport"], "mockProvider")
        self.assertEqual(metadata["cleanup"]["transport"], "mockProvider")

    def test_second_start_is_rejected_idempotently_with_state_details(self) -> None:
        first_response = self.send("startRecording", {"sessionId": "session-a"})
        self.assertTrue(first_response["ok"])

        second_response = self.send("startRecording", {"sessionId": "session-b"})
        self.assertFalse(second_response["ok"])
        error = cast(dict[str, Any], second_response["error"])
        details = cast(dict[str, Any], error["details"])
        self.assertEqual(error["code"], "recording_active")
        self.assertTrue(details["idempotent"])
        self.assertEqual(details["state"]["lifecycleState"], "recording")
        self.assertEqual(details["state"]["activeSessionId"], "session-a")

    def test_cancel_job_marks_terminal_cancelled_state(self) -> None:
        start_response = self.send("startRecording", {"sessionId": "cancel-session"})
        self.assertTrue(start_response["ok"])
        job = cast(dict[str, Any], cast(dict[str, Any], start_response["result"])["job"])
        job_id = cast(str, job["jobId"])
        audio_path = Path(cast(str, job["audioPath"]))

        cancel_response = self.send("cancelJob", {"jobId": job_id})
        self.assertTrue(cancel_response["ok"])
        cancel_result = cast(dict[str, Any], cancel_response["result"])
        self.assertEqual(cancel_result["state"]["lifecycleState"], "cancelled")
        self.assertEqual(cancel_result["job"]["status"], "cancelled")
        self.assertTrue(cancel_result["job"]["tempAudioRemoved"])
        self.assertFalse(audio_path.exists())

        history_response = self.send("listHistory", {"limit": 5})
        history_items = cast(list[dict[str, Any]], cast(dict[str, Any], history_response["result"])["items"])
        self.assertEqual(history_items[0]["jobId"], job_id)
        self.assertEqual(history_items[0]["status"], "cancelled")

    def test_client_retries_until_restarted_service_socket_is_ready(self) -> None:
        self.process.send_signal(signal.SIGTERM)
        self.process.wait(timeout=10)

        restart_deadline = time.time() + 0.2
        while time.time() < restart_deadline:
            if not self.socket_path.exists():
                break
            time.sleep(0.01)

        delayed_restart = subprocess.Popen(
            [
                "bash",
                "-lc",
                "sleep 0.2 && exec python3 \"$SERVICE_FILE\"",
            ],
            env={**self.env, "SERVICE_FILE": str(SERVICE_FILE)},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self.addCleanup(self._cleanup_process, delayed_restart)

        response = ipc_client.send_request(
            str(self.socket_path),
            {
                "requestId": "retry-test",
                "command": "snapshot",
                "params": {},
            },
            timeout_seconds=2.0,
        )
        self.process = delayed_restart

        self.assertTrue(response["ok"])
        result = cast(dict[str, Any], response["result"])
        self.assertEqual(result["state"]["lifecycleState"], "idle")

    def _cleanup_process(self, process: subprocess.Popen[str]) -> None:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            process.wait(timeout=10)


class ServiceStateMachineUnitTest(ServiceFixtureTest):
    def test_snapshot_history_summary_reports_terminal_counts(self) -> None:
        service = VoiceWidgetService()
        with self.connect_db() as connection:
            now = "2026-05-05T12:34:56Z"
            connection.execute(
                "INSERT INTO transcript_sessions (id, created_at, updated_at, client_source) VALUES (?, ?, ?, ?)",
                ("summary-session", now, now, "plugin"),
            )
            connection.executemany(
                "INSERT INTO transcript_jobs (id, session_id, status, stt_model_id, cleanup_model_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    ("job-ready", "summary-session", "ready", "openai/whisper-large-v3", "google/gemini-3-flash-preview", now, now),
                    ("job-failed", "summary-session", "failed", "openai/whisper-large-v3", "google/gemini-3-flash-preview", now, now),
                    ("job-cancelled", "summary-session", "cancelled", "openai/whisper-large-v3", "google/gemini-3-flash-preview", now, now),
                    ("job-recording", "summary-session", "recording", "openai/whisper-large-v3", "google/gemini-3-flash-preview", now, now),
                ],
            )
            connection.commit()

        history_summary = cast(dict[str, int], service.snapshot()["historySummary"])
        self.assertEqual(history_summary["jobCount"], 4)
        self.assertEqual(history_summary["readyCount"], 1)
        self.assertEqual(history_summary["completedCount"], 3)
        self.assertEqual(history_summary["failedCount"], 1)
        self.assertEqual(history_summary["cancelledCount"], 1)

    def test_prompt_preset_duplicate_creates_editable_copy(self) -> None:
        service = VoiceWidgetService()

        prompt_list = cast(dict[str, Any], service.list_prompt_presets({}))
        self.assertEqual(prompt_list["defaultPromptPresetId"], "ml-dictation-default")
        self.assertEqual(prompt_list["count"], 1)

        duplicate_result = cast(
            dict[str, Any],
            service.duplicate_prompt_preset(
                {"sourcePresetId": "ml-dictation-default", "label": "Custom ML copy"}
            ),
        )
        duplicated = cast(dict[str, Any], duplicate_result["preset"])
        self.assertEqual(duplicated["label"], "Custom ML copy")
        self.assertFalse(duplicated["isBuiltin"])
        self.assertEqual(duplicated["duplicatedFromPromptId"], "ml-dictation-default")

        updated = cast(
            dict[str, Any],
            service.save_prompt_preset(
                {
                    "presetId": duplicated["presetId"],
                    "label": "Edited ML copy",
                    "promptText": "Keep ML terms. Remove filler.",
                }
            ),
        )
        preset = cast(dict[str, Any], updated["preset"])
        self.assertEqual(preset["label"], "Edited ML copy")
        self.assertEqual(preset["promptText"], "Keep ML terms. Remove filler.")

    def test_stop_recording_runs_default_cleanup_and_preserves_raw_and_processed_transcripts(self) -> None:
        service = VoiceWidgetService()
        service.save_secret({"secret": "fixture-key"})
        start_result = cast(dict[str, Any], service.start_recording({"sessionId": "fixture-session"}))
        job = cast(dict[str, Any], start_result["job"])
        job_id = cast(str, job["jobId"])
        self.write_fixture_audio(Path(cast(str, job["audioPath"])))

        def fake_post_stt_request(_api_key: str, payload: dict[str, object]) -> tuple[dict[str, object], dict[str, str]]:
            self.assertEqual(payload["model"], "openai/whisper-large-v3")
            return (
                {
                    "text": "fixture raw transcript",
                    "usage": {"seconds": 1.1, "total_tokens": 7, "input_tokens": 5, "output_tokens": 2, "cost": 0.0001},
                },
                {"X-Generation-Id": "gen-success-1"},
            )

        def fake_post_cleanup_request(_api_key: str, payload: dict[str, object]) -> tuple[dict[str, object], dict[str, str]]:
            self.assertEqual(payload["model"], "google/gemini-3-flash-preview")
            messages = cast(list[dict[str, str]], payload["messages"])
            self.assertIn("ML engineering workflow", messages[0]["content"])
            self.assertEqual(messages[1]["content"], "fixture raw transcript")
            return (
                {
                    "choices": [{"message": {"content": "Cleaned ML transcript"}}],
                    "usage": {"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40},
                },
                {"X-Generation-Id": "gen-cleanup-1"},
            )

        service._post_stt_request = fake_post_stt_request  # type: ignore[method-assign]
        service._post_cleanup_request = fake_post_cleanup_request  # type: ignore[method-assign]

        stop_result = cast(dict[str, Any], service.stop_recording())
        self.assertEqual(stop_result["transitionPath"], ["finalizing", "transcribing", "postprocessing", "ready"])

        with self.connect_db() as connection:
            row = connection.execute(
                "SELECT status, raw_transcript, processed_transcript, prompt_preset_id, prompt_snapshot_text, prompt_snapshot_hash, stt_model_id, cleanup_model_id, stt_request_session_id, cleanup_request_session_id, request_metadata_json FROM transcript_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()

        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["status"], "ready")
        self.assertEqual(row["raw_transcript"], "fixture raw transcript")
        self.assertEqual(row["processed_transcript"], "Cleaned ML transcript")
        self.assertEqual(row["prompt_preset_id"], "ml-dictation-default")
        self.assertIn("ML engineering workflow", cast(str, row["prompt_snapshot_text"]))
        self.assertEqual(
            row["prompt_snapshot_hash"],
            noctalia_service.hash_prompt_text(cast(str, row["prompt_snapshot_text"])),
        )
        self.assertEqual(row["stt_model_id"], "openai/whisper-large-v3")
        self.assertEqual(row["cleanup_model_id"], "google/gemini-3-flash-preview")
        self.assertEqual(row["stt_request_session_id"], "gen-success-1")
        self.assertEqual(row["cleanup_request_session_id"], "gen-cleanup-1")
        metadata = cast(dict[str, Any], json.loads(cast(str, row["request_metadata_json"])))
        self.assertEqual(metadata["stt"]["model"], "openai/whisper-large-v3")
        self.assertEqual(metadata["stt"]["attemptCount"], 1)
        self.assertEqual(metadata["stt"]["generationId"], "gen-success-1")
        self.assertEqual(metadata["cleanup"]["model"], "google/gemini-3-flash-preview")
        self.assertEqual(metadata["cleanup"]["promptPresetId"], "ml-dictation-default")
        self.assertEqual(metadata["cleanup"]["generationId"], "gen-cleanup-1")
        self.assertNotIn("audioPath", metadata)

    def test_stop_recording_retries_transient_stt_failure_once(self) -> None:
        service = VoiceWidgetService()
        service.save_secret({"secret": "fixture-key"})
        start_result = cast(dict[str, Any], service.start_recording({"sessionId": "retry-session"}))
        job = cast(dict[str, Any], start_result["job"])
        self.write_fixture_audio(Path(cast(str, job["audioPath"])))
        attempts = {"count": 0}

        def flaky_post_stt_request(_api_key: str, _payload: dict[str, object]) -> tuple[dict[str, object], dict[str, str]]:
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise service._classify_stt_http_error(503)
            return ({"text": "retried transcript"}, {"X-Generation-Id": "gen-retry-1"})

        def successful_cleanup(_api_key: str, _payload: dict[str, object]) -> tuple[dict[str, object], dict[str, str]]:
            return ({"choices": [{"message": {"content": "retried transcript cleaned"}}]}, {"X-Generation-Id": "gen-cleanup-retry-1"})

        service._post_stt_request = flaky_post_stt_request  # type: ignore[method-assign]
        service._post_cleanup_request = successful_cleanup  # type: ignore[method-assign]

        stop_result = cast(dict[str, Any], service.stop_recording())
        self.assertEqual(attempts["count"], 2)
        self.assertEqual(stop_result["state"]["lifecycleState"], "ready")

    def test_stop_recording_auth_failure_fails_closed_without_retry(self) -> None:
        service = VoiceWidgetService()
        service.save_secret({"secret": "invalid-key"})
        start_result = cast(dict[str, Any], service.start_recording({"sessionId": "auth-session"}))
        job = cast(dict[str, Any], start_result["job"])
        job_id = cast(str, job["jobId"])
        self.write_fixture_audio(Path(cast(str, job["audioPath"])))
        attempts = {"count": 0}

        def auth_failure_post_stt_request(_api_key: str, _payload: dict[str, object]) -> tuple[dict[str, object], dict[str, str]]:
            attempts["count"] += 1
            raise service._classify_stt_http_error(401)

        service._post_stt_request = auth_failure_post_stt_request  # type: ignore[method-assign]

        with self.assertRaises(ServiceError) as captured:
            service.stop_recording()

        self.assertEqual(attempts["count"], 1)
        self.assertEqual(captured.exception.code, "stt_auth_failed")
        self.assertEqual(
            captured.exception.message,
            "OpenRouter authentication failed. Check the stored API key.",
        )

        with self.connect_db() as connection:
            row = connection.execute(
                "SELECT status, error_code, error_message, request_metadata_json FROM transcript_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()

        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["error_code"], "stt_auth_failed")
        self.assertEqual(
            row["error_message"],
            "OpenRouter authentication failed. Check the stored API key.",
        )
        metadata = cast(dict[str, Any], json.loads(cast(str, row["request_metadata_json"])))
        self.assertEqual(metadata["stt"]["attemptCount"], 1)
        self.assertEqual(metadata["stt"]["lastErrorCode"], "stt_auth_failed")

    def test_cleanup_failure_preserves_raw_transcript_and_marks_explicit_error_state(self) -> None:
        service = VoiceWidgetService()
        service.save_secret({"secret": "fixture-key"})
        start_result = cast(dict[str, Any], service.start_recording({"sessionId": "cleanup-failure-session"}))
        job = cast(dict[str, Any], start_result["job"])
        job_id = cast(str, job["jobId"])
        audio_path = Path(cast(str, job["audioPath"]))
        self.write_fixture_audio(audio_path)

        def fake_post_stt_request(_api_key: str, _payload: dict[str, object]) -> tuple[dict[str, object], dict[str, str]]:
            return ({"text": "raw transcript survives"}, {"X-Generation-Id": "gen-stt-keep-1"})

        def fail_cleanup(_api_key: str, _payload: dict[str, object]) -> tuple[dict[str, object], dict[str, str]]:
            raise service._classify_cleanup_http_error(503)

        service._post_stt_request = fake_post_stt_request  # type: ignore[method-assign]
        service._post_cleanup_request = fail_cleanup  # type: ignore[method-assign]

        with self.assertRaises(ServiceError) as captured:
            service.stop_recording()

        self.assertEqual(captured.exception.code, "cleanup_provider_unavailable")
        self.assertTrue(audio_path.exists())

        with self.connect_db() as connection:
            row = connection.execute(
                "SELECT status, error_code, raw_transcript, processed_transcript, request_metadata_json FROM transcript_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()

        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["error_code"], "cleanup_provider_unavailable")
        self.assertEqual(row["raw_transcript"], "raw transcript survives")
        self.assertIsNone(row["processed_transcript"])
        metadata = cast(dict[str, Any], json.loads(cast(str, row["request_metadata_json"])))
        self.assertEqual(metadata["stt"]["generationId"], "gen-stt-keep-1")
        self.assertEqual(metadata["cleanup"]["lastErrorCode"], "cleanup_provider_unavailable")

    def test_stop_failure_marks_job_failed(self) -> None:
        service = VoiceWidgetService()
        start_result = cast(dict[str, Any], service.start_recording({"sessionId": "fixture-session"}))
        job = cast(dict[str, Any], start_result["job"])
        job_id = cast(str, job["jobId"])

        def fail_completion(_job_id: str, _session_id: str) -> tuple[list[str], bool]:
            raise RuntimeError("synthetic pipeline crash")

        service._complete_job = fail_completion  # type: ignore[method-assign]

        with self.assertRaisesRegex(Exception, "synthetic pipeline crash"):
            service.stop_recording()

        with self.connect_db() as connection:
            row = connection.execute(
                "SELECT status, error_code FROM transcript_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()

        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["error_code"], "post_recording_pipeline_failed")
        self.assertEqual(service.snapshot()["state"]["lifecycleState"], "failed")

    def test_export_item_writes_expected_markdown_shape_with_deterministic_filename(self) -> None:
        service = VoiceWidgetService()
        with self.connect_db() as connection:
            now = "2026-05-05T12:34:56Z"
            connection.execute(
                "INSERT INTO transcript_sessions (id, created_at, updated_at, client_source) VALUES (?, ?, ?, ?)",
                ("export-session", now, now, "plugin"),
            )
            connection.execute(
                """
                INSERT INTO transcript_jobs (
                    id, session_id, status, raw_transcript, processed_transcript, prompt_preset_id,
                    prompt_snapshot_hash, stt_model_id, cleanup_model_id, created_at, updated_at, completed_at
                ) VALUES (?, ?, 'ready', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "export-job",
                    "export-session",
                    "raw line one\nraw line two",
                    "processed line one\nprocessed line two",
                    "ml-dictation-default",
                    "hash-123",
                    "openai/whisper-large-v3",
                    "google/gemini-3-flash-preview",
                    now,
                    now,
                    now,
                ),
            )
            connection.commit()

        result = cast(dict[str, Any], service.export_item({"jobId": "export-job"}))
        export_path = Path(cast(str, result["exportPath"]))
        self.assertEqual(export_path.name, "2026-05-05T12-34-56Z-export-job.md")
        content = export_path.read_text(encoding="utf-8")
        self.assertIn("# Voice Transcript Export", content)
        self.assertIn("- Session ID: `export-session`", content)
        self.assertIn("- Job ID: `export-job`", content)
        self.assertIn("- Prompt preset ID: `ml-dictation-default`", content)
        self.assertIn("## Raw Transcript\n\nraw line one\nraw line two", content)
        self.assertIn("## Processed Transcript\n\nprocessed line one\nprocessed line two", content)

    def test_export_item_does_not_leak_secret_or_audio_path_metadata(self) -> None:
        service = VoiceWidgetService()
        service.save_secret({"secret": "super-secret-api-key"})
        audio_path = Path(self.temp_home.name) / ".cache" / "noctalia-openrouter-voice-widget" / "jobs" / "secret.wav"
        with self.connect_db() as connection:
            now = "2026-05-05T12:34:56Z"
            connection.execute(
                "INSERT INTO transcript_sessions (id, created_at, updated_at, client_source) VALUES (?, ?, ?, ?)",
                ("secret-session", now, now, "plugin"),
            )
            connection.execute(
                """
                INSERT INTO transcript_jobs (
                    id, session_id, status, raw_transcript, processed_transcript, prompt_preset_id,
                    prompt_snapshot_hash, stt_model_id, cleanup_model_id, request_metadata_json,
                    created_at, updated_at, completed_at
                ) VALUES (?, ?, 'ready', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "secret-export-job",
                    "secret-session",
                    "raw transcript",
                    "processed transcript",
                    "ml-dictation-default",
                    "hash-secret",
                    "openai/whisper-large-v3",
                    "google/gemini-3-flash-preview",
                    json.dumps(
                        {
                            "authorization": "Bearer super-secret-api-key",
                            "audioPath": str(audio_path),
                            "cleanup": {"apiKey": "super-secret-api-key"},
                        },
                        sort_keys=True,
                    ),
                    now,
                    now,
                    now,
                ),
            )
            connection.commit()

        result = cast(dict[str, Any], service.export_item({"jobId": "secret-export-job"}))
        content = Path(cast(str, result["exportPath"])).read_text(encoding="utf-8")
        self.assertNotIn("super-secret-api-key", content)
        self.assertNotIn("Authorization", content)
        self.assertNotIn(str(audio_path), content)
        self.assertNotIn("audioPath", content)

    def test_successful_session_does_not_retain_raw_audio_by_default(self) -> None:
        service = VoiceWidgetService()
        service.save_secret({"secret": "fixture-key"})
        start_result = cast(dict[str, Any], service.start_recording({"sessionId": "no-audio-retention-session"}))
        job = cast(dict[str, Any], start_result["job"])
        job_id = cast(str, job["jobId"])
        audio_path = Path(cast(str, job["audioPath"]))
        self.write_fixture_audio(audio_path)

        def fake_post_stt_request(_api_key: str, _payload: dict[str, object]) -> tuple[dict[str, object], dict[str, str]]:
            return ({"text": "retained raw transcript"}, {"X-Generation-Id": "gen-stt-no-audio"})

        def fake_post_cleanup_request(_api_key: str, _payload: dict[str, object]) -> tuple[dict[str, object], dict[str, str]]:
            return ({"choices": [{"message": {"content": "retained processed transcript"}}]}, {"X-Generation-Id": "gen-cleanup-no-audio"})

        service._post_stt_request = fake_post_stt_request  # type: ignore[method-assign]
        service._post_cleanup_request = fake_post_cleanup_request  # type: ignore[method-assign]

        stop_result = cast(dict[str, Any], service.stop_recording())
        self.assertTrue(cast(dict[str, Any], stop_result["job"])["tempAudioRemoved"])
        self.assertFalse(audio_path.exists())

        with self.connect_db() as connection:
            row = connection.execute(
                "SELECT raw_transcript, processed_transcript, request_metadata_json FROM transcript_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()

        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["raw_transcript"], "retained raw transcript")
        self.assertEqual(row["processed_transcript"], "retained processed transcript")
        metadata = cast(dict[str, Any], json.loads(cast(str, row["request_metadata_json"])))
        self.assertNotIn("audioPath", metadata)

    def test_start_recording_rejects_unknown_mock_provider(self) -> None:
        service = VoiceWidgetService()

        with self.assertRaises(ServiceError) as captured:
            service.start_recording({"mockProvider": "live-provider"})

        self.assertEqual(captured.exception.code, "invalid_request")
        self.assertIn("mockProvider", captured.exception.message)


if __name__ == "__main__":
    unittest.main()
