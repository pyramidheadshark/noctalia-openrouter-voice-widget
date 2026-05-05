from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import signal
import socket
import sqlite3
import stat
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib import error as urllib_error
from urllib import request as urllib_request

SERVICE_NAME = "noctalia-openrouter-voice-widget"
SERVICE_VERSION = "0.1.0"
PROTOCOL_VERSION = "v1"
OPENROUTER_STT_ENDPOINT = "https://openrouter.ai/api/v1/audio/transcriptions"
OPENROUTER_CLEANUP_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_TRANSIENT_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
OPENROUTER_AUTH_STATUS_CODES = {401, 403}
STT_MAX_ATTEMPTS = 2
STT_RETRY_DELAY_SECONDS = 0.2
PUBLIC_COMMANDS = {
    "snapshot",
    "startRecording",
    "stopRecording",
    "cancelJob",
    "listHistory",
    "listPromptPresets",
    "exportItem",
    "savePromptPreset",
    "saveSettings",
    "saveSecret",
    "duplicatePromptPreset",
    "ping",
}
ACTIVE_JOB_STATUSES = {"recording", "finalizing", "transcribing", "postprocessing"}
TERMINAL_JOB_STATUSES = {"ready", "failed", "cancelled"}
SUPPORTED_JOB_STATUSES = ACTIVE_JOB_STATUSES | TERMINAL_JOB_STATUSES
LEGACY_JOB_STATUS_MAP = {
    "queued": "finalizing",
    "cleaning": "postprocessing",
    "completed": "ready",
}
SERVICE_DIR = Path(__file__).resolve().parent
REPO_ROOT = SERVICE_DIR.parent
CONFIG_EXAMPLE_PATH = SERVICE_DIR / "config.example.json"
SCHEMA_PATH = SERVICE_DIR / "history.schema.sql"
EXPORT_TEMPLATE_PATH = SERVICE_DIR / "export-template.md"
LIVE_CONFIG_PATH = Path.home() / ".config" / SERVICE_NAME / "config.json"
SECRET_PATH = Path.home() / ".local" / "state" / SERVICE_NAME / "openrouter.key"
QA_MODE_ENV_VAR = "NOCTALIA_OPENROUTER_QA_MODE"
QA_FIXTURE_AUDIO_ENV_VAR = "NOCTALIA_OPENROUTER_QA_FIXTURE_AUDIO"
QA_MOCK_PROVIDER = "deterministic"
QA_FIXTURE_AUDIO_PATH = REPO_ROOT / "tests" / "fixtures" / "audio" / "deterministic-sample.wav"
QA_DEFAULT_STT_TEXT = "deterministic qa raw transcript"
QA_DEFAULT_CLEANUP_TEXT = "Deterministic QA cleaned transcript"
BUILTIN_PROMPT_PRESETS: tuple[dict[str, str], ...] = (
    {
        "id": "ml-dictation-default",
        "label": "Preset 1: ML-aware near-literal cleanup",
        "promptText": (
            "You are cleaning a speech-to-text transcript for an ML engineering workflow. "
            "Preserve the speaker's intent, meaning, ordering, and technical nuance as closely as possible. "
            "Remove filler words, false starts, and obvious recognition noise. "
            "Correct punctuation, capitalization, and clearly mistaken terminology when the ML context makes the correction obvious. "
            "Keep domain-specific terms such as model names, libraries, metrics, architectures, prompts, datasets, and code identifiers accurate. "
            "Do not summarize, omit important details, or add new content. "
            "Return only the cleaned transcript text."
        ),
    },
)
DEFAULT_PROMPT_PRESET_ID = BUILTIN_PROMPT_PRESETS[0]["id"]
TRANSCRIPT_JOBS_TABLE_SQL = """
CREATE TABLE transcript_jobs (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('recording', 'finalizing', 'transcribing', 'postprocessing', 'ready', 'failed', 'cancelled')
    ),
    error_code TEXT,
    error_message TEXT,
    raw_transcript TEXT,
    processed_transcript TEXT,
    prompt_preset_id TEXT,
    prompt_snapshot_text TEXT,
    prompt_snapshot_hash TEXT,
    stt_model_id TEXT NOT NULL,
    cleanup_model_id TEXT NOT NULL,
    stt_request_session_id TEXT,
    cleanup_request_session_id TEXT,
    request_metadata_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    recording_started_at TEXT,
    recording_stopped_at TEXT,
    transcribed_at TEXT,
    cleanup_completed_at TEXT,
    completed_at TEXT,
    FOREIGN KEY (session_id) REFERENCES transcript_sessions(id) ON DELETE CASCADE
)
"""


class ServiceError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.code: str = code
        self.message: str = message
        self.details: dict[str, object] = details or {}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def hash_prompt_text(prompt_text: str) -> str:
    return hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()


def isoformat_for_filename(value: str) -> str:
    return value.replace(":", "-")


def expand_runtime_path(raw_value: str) -> str:
    return os.path.expanduser(os.path.expandvars(raw_value))


def ensure_xdg_runtime_dir() -> Path:
    raw_value = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if not raw_value:
        raise ServiceError(
            "missing_runtime_dir",
            "XDG_RUNTIME_DIR is required for the local Unix socket and is not set.",
        )

    runtime_dir = Path(raw_value)
    if not runtime_dir.exists() or not runtime_dir.is_dir():
        raise ServiceError(
            "invalid_runtime_dir",
            f"XDG_RUNTIME_DIR does not point to a writable directory: {runtime_dir}",
        )
    if not os.access(runtime_dir, os.W_OK):
        raise ServiceError(
            "runtime_dir_not_writable",
            f"XDG_RUNTIME_DIR is not writable: {runtime_dir}",
        )
    return runtime_dir


@dataclass
class RuntimeState:
    lifecycle_state: str = "idle"
    active_job_id: str | None = None
    active_session_id: str | None = None
    last_job_id: str | None = None


class VoiceWidgetService:
    def __init__(self) -> None:
        self.started_at: str = utc_now()
        self.process_started_monotonic: float = time.monotonic()
        self.runtime_dir: Path = ensure_xdg_runtime_dir()
        self.defaults: dict[str, object] = self._load_defaults()
        self._ensure_live_config()
        self.config: dict[str, object] = self._load_live_config()
        self.socket_path: Path = Path(expand_runtime_path(str(self.config["socketPath"])))
        self.history_db_path: Path = Path(expand_runtime_path(str(self.config["historyDbPath"])))
        self.jobs_cache_dir: Path = Path(expand_runtime_path(str(self.config["jobsCacheDir"])))
        self.export_directory: Path = Path(expand_runtime_path(str(self.config["exportDirectory"])))
        self.state: RuntimeState = RuntimeState()
        self.server: asyncio.base_events.Server | None = None
        self.shutdown_event: asyncio.Event = asyncio.Event()
        self._ensure_storage_paths()
        self._initialize_database()
        self._reconcile_unfinished_jobs()

    def _load_defaults(self) -> dict[str, object]:
        with CONFIG_EXAMPLE_PATH.open("r", encoding="utf-8") as handle:
            defaults = json.load(handle)
        if not isinstance(defaults, dict):
            raise ServiceError("invalid_defaults", "service/config.example.json must contain a JSON object")
        return {str(key): value for key, value in defaults.items()}

    def _ensure_live_config(self) -> None:
        LIVE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if LIVE_CONFIG_PATH.exists():
            return
        LIVE_CONFIG_PATH.write_text(
            json.dumps(self.defaults, indent=2) + "\n",
            encoding="utf-8",
        )

    def _load_live_config(self) -> dict[str, object]:
        with LIVE_CONFIG_PATH.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        if not isinstance(loaded, dict):
            raise ServiceError("invalid_settings", "Live config must contain a JSON object")
        merged = dict(self.defaults)
        merged.update({str(key): value for key, value in loaded.items()})
        self._validate_settings(merged)
        return merged

    def _ensure_storage_paths(self) -> None:
        self.history_db_path.parent.mkdir(parents=True, exist_ok=True)
        self.jobs_cache_dir.mkdir(parents=True, exist_ok=True)
        self.export_directory.mkdir(parents=True, exist_ok=True)
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.parent != self.runtime_dir and self.runtime_dir not in self.socket_path.parents:
            raise ServiceError(
                "invalid_socket_path",
                f"Socket path must live under XDG_RUNTIME_DIR: {self.socket_path}",
            )

    def _initialize_database(self) -> None:
        with sqlite3.connect(self.history_db_path) as connection:
            connection.row_factory = sqlite3.Row
            self._migrate_legacy_job_schema(connection)
            connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            connection.execute(
                "CREATE VIEW IF NOT EXISTS transcripts AS SELECT * FROM transcript_jobs"
            )
            self._seed_builtin_prompts(connection)
            connection.commit()

    def _seed_builtin_prompts(self, connection: sqlite3.Connection) -> None:
        now = utc_now()
        for preset in BUILTIN_PROMPT_PRESETS:
            existing = connection.execute(
                "SELECT id FROM prompts WHERE id = ?",
                (preset["id"],),
            ).fetchone()
            if existing is not None:
                continue
            connection.execute(
                """
                INSERT INTO prompts (
                    id,
                    label,
                    prompt_text,
                    prompt_hash,
                    is_builtin,
                    duplicated_from_prompt_id,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, 1, NULL, ?, ?)
                """,
                (
                    preset["id"],
                    preset["label"],
                    preset["promptText"],
                    hash_prompt_text(preset["promptText"]),
                    now,
                    now,
                ),
            )

    def _migrate_legacy_job_schema(self, connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'transcript_jobs'"
        ).fetchone()
        if row is None:
            return

        table_sql = str(row["sql"] or "")
        if all(status in table_sql for status in SUPPORTED_JOB_STATUSES):
            return

        connection.executescript(
            f"""
            PRAGMA foreign_keys = OFF;
            DROP INDEX IF EXISTS idx_transcript_jobs_session_id;
            DROP INDEX IF EXISTS idx_transcript_jobs_status;
            DROP INDEX IF EXISTS idx_transcript_jobs_completed_at;
            ALTER TABLE transcript_jobs RENAME TO transcript_jobs_legacy;
            {TRANSCRIPT_JOBS_TABLE_SQL};
            INSERT INTO transcript_jobs (
                id,
                session_id,
                status,
                error_code,
                error_message,
                raw_transcript,
                processed_transcript,
                prompt_preset_id,
                prompt_snapshot_text,
                prompt_snapshot_hash,
                stt_model_id,
                cleanup_model_id,
                stt_request_session_id,
                cleanup_request_session_id,
                request_metadata_json,
                created_at,
                updated_at,
                recording_started_at,
                recording_stopped_at,
                transcribed_at,
                cleanup_completed_at,
                completed_at
            )
            SELECT id,
                   session_id,
                   CASE status
                       WHEN 'queued' THEN 'finalizing'
                       WHEN 'cleaning' THEN 'postprocessing'
                       WHEN 'completed' THEN 'ready'
                       ELSE status
                   END,
                   error_code,
                   error_message,
                   raw_transcript,
                   processed_transcript,
                   prompt_preset_id,
                   prompt_snapshot_text,
                   prompt_snapshot_hash,
                   stt_model_id,
                   cleanup_model_id,
                   stt_request_session_id,
                   cleanup_request_session_id,
                   request_metadata_json,
                   created_at,
                   updated_at,
                   recording_started_at,
                   recording_stopped_at,
                   transcribed_at,
                   cleanup_completed_at,
                   completed_at
            FROM transcript_jobs_legacy;
            DROP TABLE transcript_jobs_legacy;
            CREATE INDEX idx_transcript_jobs_session_id ON transcript_jobs(session_id);
            CREATE INDEX idx_transcript_jobs_status ON transcript_jobs(status);
            CREATE INDEX idx_transcript_jobs_completed_at ON transcript_jobs(completed_at);
            PRAGMA foreign_keys = ON;
            """
        )

    def _reconcile_unfinished_jobs(self) -> None:
        now = utc_now()
        with sqlite3.connect(self.history_db_path) as connection:
            connection.row_factory = sqlite3.Row
            pending = connection.execute(
                """
                SELECT id
                FROM transcript_jobs
                WHERE status IN ('recording', 'finalizing', 'transcribing', 'postprocessing')
                ORDER BY created_at DESC
                """
            ).fetchall()
            if pending:
                connection.execute(
                    """
                    UPDATE transcript_jobs
                    SET status = 'failed',
                        error_code = 'service_restarted',
                        error_message = 'The helper service restarted before this job finished.',
                        updated_at = ?,
                        completed_at = COALESCE(completed_at, ?)
                    WHERE status IN ('recording', 'finalizing', 'transcribing', 'postprocessing')
                    """,
                    (now, now),
                )
                connection.commit()

            last_job = connection.execute(
                "SELECT id, status FROM transcript_jobs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if last_job:
                self.state.last_job_id = str(last_job["id"])
                self.state.lifecycle_state = LEGACY_JOB_STATUS_MAP.get(
                    str(last_job["status"]),
                    str(last_job["status"]),
                )

    def _validate_settings(self, settings: dict[str, object]) -> None:
        allowed_keys = set(self.defaults)
        provided_keys = set(settings)
        extra_keys = sorted(provided_keys - allowed_keys)
        if extra_keys:
            raise ServiceError(
                "invalid_settings",
                f"Unsupported settings keys: {', '.join(extra_keys)}",
            )

        required_string_keys = {
            "socketPath",
            "historyDbPath",
            "jobsCacheDir",
            "exportDirectory",
            "defaultPromptPresetId",
            "sttModel",
            "cleanupModel",
            "exportFileNamePattern",
        }
        required_int_keys = {
            "completedJobRetentionDays",
            "failedJobRetentionDays",
            "failedJobAudioTtlHours",
        }

        for key in required_string_keys:
            value = settings.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ServiceError("invalid_settings", f"{key} must be a non-empty string")

        for key in required_int_keys:
            value = settings.get(key)
            if not isinstance(value, int) or value < 0:
                raise ServiceError("invalid_settings", f"{key} must be a non-negative integer")

    def _write_live_config(self, settings: dict[str, object]) -> None:
        self._validate_settings(settings)
        LIVE_CONFIG_PATH.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
        self.config = settings
        self.socket_path = Path(expand_runtime_path(str(self.config["socketPath"])))
        self.history_db_path = Path(expand_runtime_path(str(self.config["historyDbPath"])))
        self.jobs_cache_dir = Path(expand_runtime_path(str(self.config["jobsCacheDir"])))
        self.export_directory = Path(expand_runtime_path(str(self.config["exportDirectory"])))
        self._ensure_storage_paths()

    def _connect_db(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.history_db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _default_prompt_preset(self, connection: sqlite3.Connection) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM prompts WHERE id = ?",
            (DEFAULT_PROMPT_PRESET_ID,),
        ).fetchone()
        if row is None:
            raise ServiceError("internal_error", "The built-in default prompt preset is missing.")
        return row

    def _read_prompt_preset(self, connection: sqlite3.Connection, preset_id: str) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM prompts WHERE id = ?",
            (preset_id,),
        ).fetchone()

    def _coerce_prompt_preset_label(self, preset_id: str) -> str:
        return preset_id.replace("-", " ").replace("_", " ").strip() or preset_id

    def _ensure_prompt_preset_exists(self, connection: sqlite3.Connection, preset_id: str) -> sqlite3.Row:
        row = self._read_prompt_preset(connection, preset_id)
        if row is not None:
            return row

        default_row = self._default_prompt_preset(connection)
        now = utc_now()
        connection.execute(
            """
            INSERT INTO prompts (
                id,
                label,
                prompt_text,
                prompt_hash,
                is_builtin,
                duplicated_from_prompt_id,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (
                preset_id,
                self._coerce_prompt_preset_label(preset_id),
                str(default_row["prompt_text"]),
                str(default_row["prompt_hash"]),
                str(default_row["id"]),
                now,
                now,
            ),
        )
        created = self._read_prompt_preset(connection, preset_id)
        if created is None:
            raise ServiceError("internal_error", f"Failed to create prompt preset {preset_id}.")
        return created

    def _serialize_prompt_preset(self, row: sqlite3.Row) -> dict[str, object]:
        return {
            "presetId": row["id"],
            "label": row["label"],
            "promptText": row["prompt_text"],
            "promptHash": row["prompt_hash"],
            "isBuiltin": bool(row["is_builtin"]),
            "duplicatedFromPromptId": row["duplicated_from_prompt_id"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def _job_audio_path(self, job_id: str) -> Path:
        return self.jobs_cache_dir / f"{job_id}.wav"

    def _read_secret(
        self,
        auth_error_code: str = "stt_auth_failed",
        auth_error_message: str = "OpenRouter authentication failed. Check the stored API key.",
    ) -> str:
        if not SECRET_PATH.exists():
            raise ServiceError(
                auth_error_code,
                auth_error_message,
                details={"retryable": False},
            )
        secret = SECRET_PATH.read_text(encoding="utf-8").strip()
        if not secret:
            raise ServiceError(
                auth_error_code,
                auth_error_message,
                details={"retryable": False},
            )
        return secret

    def _audio_format_for_path(self, audio_path: Path) -> str:
        suffix = audio_path.suffix.lower().lstrip(".")
        return suffix or "wav"

    def _fixture_stt_text(self) -> str | None:
        fixture_text = os.environ.get("NOCTALIA_OPENROUTER_STT_FIXTURE_TEXT", "").strip()
        return fixture_text or None

    def _fixture_cleanup_text(self) -> str | None:
        fixture_text = os.environ.get("NOCTALIA_OPENROUTER_CLEANUP_FIXTURE_TEXT", "").strip()
        return fixture_text or None

    def _qa_mode_value(self) -> str | None:
        raw_value = os.environ.get(QA_MODE_ENV_VAR, "").strip().lower()
        return raw_value or None

    def _resolve_qa_fixture_audio_path(self) -> Path:
        raw_value = os.environ.get(QA_FIXTURE_AUDIO_ENV_VAR, "").strip()
        if raw_value:
            return Path(expand_runtime_path(raw_value))
        return QA_FIXTURE_AUDIO_PATH

    def _deterministic_qa_requested(self, params: dict[str, object] | None = None) -> bool:
        mock_provider = None
        if params is not None and "mockProvider" in params:
            raw_mock_provider = params.get("mockProvider")
            if not isinstance(raw_mock_provider, str) or not raw_mock_provider.strip():
                raise ServiceError(
                    "invalid_request",
                    "startRecording params.mockProvider must be a non-empty string when provided.",
                )
            mock_provider = raw_mock_provider.strip().lower()
            if mock_provider != QA_MOCK_PROVIDER:
                raise ServiceError(
                    "invalid_request",
                    "startRecording params.mockProvider only supports 'deterministic'.",
                )

        if mock_provider == QA_MOCK_PROVIDER:
            return True
        return self._qa_mode_value() == QA_MOCK_PROVIDER

    def _job_request_metadata(self, row: sqlite3.Row | None) -> dict[str, object]:
        if row is None:
            return {}
        raw_metadata = row["request_metadata_json"]
        if not isinstance(raw_metadata, str) or not raw_metadata.strip():
            return {}
        try:
            decoded = json.loads(raw_metadata)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}

    def _job_qa_metadata(self, row: sqlite3.Row | None) -> dict[str, object] | None:
        metadata = self._job_request_metadata(row)
        qa_metadata = metadata.get("qa")
        return qa_metadata if isinstance(qa_metadata, dict) else None

    def _qa_mock_provider_enabled(self, qa_metadata: dict[str, object] | None) -> bool:
        if not isinstance(qa_metadata, dict):
            return False
        return str(qa_metadata.get("mockProvider") or "").strip().lower() == QA_MOCK_PROVIDER

    def _materialize_qa_fixture_audio(self, audio_path: Path) -> Path:
        fixture_audio_path = self._resolve_qa_fixture_audio_path()
        if not fixture_audio_path.exists() or not fixture_audio_path.is_file():
            raise ServiceError(
                "qa_fixture_missing",
                f"Deterministic QA audio fixture is missing: {fixture_audio_path}",
                details={"retryable": False},
            )
        audio_path.write_bytes(fixture_audio_path.read_bytes())
        return fixture_audio_path

    def _export_filename(self, job_id: str, completed_at: str) -> str:
        pattern = str(self.config["exportFileNamePattern"])
        return (
            pattern.replace("{{completedAt}}", isoformat_for_filename(completed_at))
            .replace("{{jobId}}", job_id)
        )

    def _classify_stt_http_error(self, status_code: int) -> ServiceError:
        if status_code in OPENROUTER_AUTH_STATUS_CODES:
            return ServiceError(
                "stt_auth_failed",
                "OpenRouter authentication failed. Check the stored API key.",
                details={"retryable": False, "statusCode": status_code},
            )
        if status_code in OPENROUTER_TRANSIENT_STATUS_CODES:
            return ServiceError(
                "stt_provider_unavailable",
                "OpenRouter STT was temporarily unavailable. Please retry the job.",
                details={"retryable": True, "statusCode": status_code},
            )
        return ServiceError(
            "stt_request_failed",
            f"OpenRouter STT request failed with status {status_code}.",
            details={"retryable": False, "statusCode": status_code},
        )

    def _classify_cleanup_http_error(self, status_code: int) -> ServiceError:
        if status_code in OPENROUTER_AUTH_STATUS_CODES:
            return ServiceError(
                "cleanup_auth_failed",
                "OpenRouter cleanup authentication failed. Check the stored API key.",
                details={"retryable": False, "statusCode": status_code},
            )
        if status_code in OPENROUTER_TRANSIENT_STATUS_CODES:
            return ServiceError(
                "cleanup_provider_unavailable",
                "OpenRouter cleanup was temporarily unavailable. Please retry the job.",
                details={"retryable": True, "statusCode": status_code},
            )
        return ServiceError(
            "cleanup_request_failed",
            f"OpenRouter cleanup request failed with status {status_code}.",
            details={"retryable": False, "statusCode": status_code},
        )

    def _post_stt_request(
        self,
        api_key: str,
        payload: dict[str, object],
    ) -> tuple[dict[str, object], dict[str, str]]:
        request = urllib_request.Request(
            OPENROUTER_STT_ENDPOINT,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib_request.urlopen(request, timeout=60) as response:
                raw_body = response.read().decode("utf-8")
                body = json.loads(raw_body)
                if not isinstance(body, dict):
                    raise ServiceError(
                        "stt_request_failed",
                        "OpenRouter STT returned an invalid response.",
                        details={"retryable": False},
                    )
                headers = {str(key): str(value) for key, value in response.headers.items()}
                return body, headers
        except urllib_error.HTTPError as exc:
            raise self._classify_stt_http_error(exc.code) from exc
        except urllib_error.URLError as exc:
            raise ServiceError(
                "stt_provider_unavailable",
                "OpenRouter STT was temporarily unavailable. Please retry the job.",
                details={"retryable": True, "transportError": exc.__class__.__name__},
            ) from exc
        except TimeoutError as exc:
            raise ServiceError(
                "stt_provider_unavailable",
                "OpenRouter STT was temporarily unavailable. Please retry the job.",
                details={"retryable": True, "transportError": exc.__class__.__name__},
            ) from exc

    def _post_cleanup_request(
        self,
        api_key: str,
        payload: dict[str, object],
    ) -> tuple[dict[str, object], dict[str, str]]:
        request = urllib_request.Request(
            OPENROUTER_CLEANUP_ENDPOINT,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib_request.urlopen(request, timeout=60) as response:
                raw_body = response.read().decode("utf-8")
                body = json.loads(raw_body)
                if not isinstance(body, dict):
                    raise ServiceError(
                        "cleanup_request_failed",
                        "OpenRouter cleanup returned an invalid response.",
                        details={"retryable": False},
                    )
                headers = {str(key): str(value) for key, value in response.headers.items()}
                return body, headers
        except urllib_error.HTTPError as exc:
            raise self._classify_cleanup_http_error(exc.code) from exc
        except urllib_error.URLError as exc:
            raise ServiceError(
                "cleanup_provider_unavailable",
                "OpenRouter cleanup was temporarily unavailable. Please retry the job.",
                details={"retryable": True, "transportError": exc.__class__.__name__},
            ) from exc
        except TimeoutError as exc:
            raise ServiceError(
                "cleanup_provider_unavailable",
                "OpenRouter cleanup was temporarily unavailable. Please retry the job.",
                details={"retryable": True, "transportError": exc.__class__.__name__},
            ) from exc

    def _extract_cleanup_text(self, message_content: object) -> str:
        if isinstance(message_content, str):
            return message_content.strip()
        if isinstance(message_content, list):
            chunks: list[str] = []
            for item in message_content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") != "text":
                    continue
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    chunks.append(text.strip())
            return "\n".join(chunks).strip()
        return ""

    def _cleanup_transcript(
        self,
        raw_transcript: str,
        prompt_preset_id: str,
        prompt_snapshot_text: str,
        prompt_snapshot_hash: str,
        qa_metadata: dict[str, object] | None = None,
    ) -> tuple[str, str | None, dict[str, object]]:
        metadata: dict[str, object] = {
            "cleanup": {
                "endpoint": OPENROUTER_CLEANUP_ENDPOINT,
                "model": str(self.config["cleanupModel"]),
                "promptPresetId": prompt_preset_id,
                "promptHash": prompt_snapshot_hash,
                "attemptCount": 0,
            }
        }
        cleanup_metadata = metadata["cleanup"]
        assert isinstance(cleanup_metadata, dict)

        if self._qa_mock_provider_enabled(qa_metadata):
            generation_id = "mock-cleanup-deterministic"
            cleanup_metadata["attemptCount"] = 1
            cleanup_metadata["transport"] = "mockProvider"
            cleanup_metadata["generationId"] = generation_id
            cleanup_metadata["completedAt"] = utc_now()
            cleanup_metadata["qaMode"] = QA_MOCK_PROVIDER
            return self._fixture_cleanup_text() or QA_DEFAULT_CLEANUP_TEXT, generation_id, metadata

        fixture_text = self._fixture_cleanup_text()
        if fixture_text is not None:
            generation_id = f"fixture-cleanup-{prompt_preset_id}"
            cleanup_metadata["attemptCount"] = 1
            cleanup_metadata["transport"] = "fixture"
            cleanup_metadata["generationId"] = generation_id
            cleanup_metadata["completedAt"] = utc_now()
            return fixture_text, generation_id, metadata

        api_key = self._read_secret(
            auth_error_code="cleanup_auth_failed",
            auth_error_message="OpenRouter cleanup authentication failed. Check the stored API key.",
        )
        payload = {
            "model": str(self.config["cleanupModel"]),
            "messages": [
                {"role": "system", "content": prompt_snapshot_text},
                {"role": "user", "content": raw_transcript},
            ],
        }
        cleanup_metadata["attemptCount"] = 1

        try:
            response_payload, response_headers = self._post_cleanup_request(api_key, payload)
            choices = response_payload.get("choices")
            if not isinstance(choices, list) or not choices:
                raise ServiceError(
                    "cleanup_request_failed",
                    "OpenRouter cleanup returned no transcript choices.",
                    details={"retryable": False},
                )
            first_choice = choices[0]
            if not isinstance(first_choice, dict):
                raise ServiceError(
                    "cleanup_request_failed",
                    "OpenRouter cleanup returned an invalid transcript choice.",
                    details={"retryable": False},
                )
            message = first_choice.get("message")
            if not isinstance(message, dict):
                raise ServiceError(
                    "cleanup_request_failed",
                    "OpenRouter cleanup returned an invalid message payload.",
                    details={"retryable": False},
                )

            cleaned_text = self._extract_cleanup_text(message.get("content"))
            if not cleaned_text:
                raise ServiceError(
                    "cleanup_request_failed",
                    "OpenRouter cleanup returned an empty processed transcript.",
                    details={"retryable": False},
                )

            generation_id = response_headers.get("X-Generation-Id") or response_headers.get("x-generation-id")
            usage = response_payload.get("usage")
            cleanup_metadata["transport"] = "openrouter"
            cleanup_metadata["providerStatusCode"] = 200
            cleanup_metadata["completedAt"] = utc_now()
            if generation_id:
                cleanup_metadata["generationId"] = generation_id
            if isinstance(usage, dict):
                cleanup_metadata["usage"] = usage
            return cleaned_text, generation_id, metadata
        except ServiceError as exc:
            cleanup_metadata["transport"] = cleanup_metadata.get("transport", "openrouter")
            cleanup_metadata["lastErrorCode"] = exc.code
            if "statusCode" in exc.details:
                cleanup_metadata["providerStatusCode"] = exc.details["statusCode"]
            if "transportError" in exc.details:
                cleanup_metadata["transportError"] = exc.details["transportError"]
            raise ServiceError(
                exc.code,
                exc.message,
                details={"requestMetadata": metadata, **exc.details},
            ) from exc

    def _transcribe_audio(
        self,
        job_id: str,
        qa_metadata: dict[str, object] | None = None,
    ) -> tuple[str, str | None, dict[str, object]]:
        audio_path = self._job_audio_path(job_id)
        if self._qa_mock_provider_enabled(qa_metadata) and (
            not audio_path.exists() or audio_path.stat().st_size == 0
        ):
            self._materialize_qa_fixture_audio(audio_path)
        if not audio_path.exists():
            raise ServiceError(
                "audio_missing",
                f"The captured audio file is missing for job {job_id}.",
                details={"retryable": False},
            )

        audio_bytes = audio_path.read_bytes()
        if not audio_bytes:
            raise ServiceError(
                "audio_missing",
                f"The captured audio file is empty for job {job_id}.",
                details={"retryable": False},
            )

        audio_format = self._audio_format_for_path(audio_path)
        metadata: dict[str, object] = {
            "stt": {
                "endpoint": OPENROUTER_STT_ENDPOINT,
                "model": str(self.config["sttModel"]),
                "audioFormat": audio_format,
                "audioBytes": len(audio_bytes),
                "attemptCount": 0,
                "transientRetryUsed": False,
                "attempts": [],
            },
        }
        stt_metadata = metadata["stt"]
        assert isinstance(stt_metadata, dict)

        if self._qa_mock_provider_enabled(qa_metadata):
            generation_id = "mock-stt-deterministic"
            stt_metadata["attemptCount"] = 1
            stt_metadata["transport"] = "mockProvider"
            stt_metadata["generationId"] = generation_id
            stt_metadata["completedAt"] = utc_now()
            stt_metadata["qaMode"] = QA_MOCK_PROVIDER
            cast_attempts = stt_metadata["attempts"]
            assert isinstance(cast_attempts, list)
            cast_attempts.append(
                {
                    "attempt": 1,
                    "outcome": "success",
                    "transport": "mockProvider",
                    "generationId": generation_id,
                }
            )
            return self._fixture_stt_text() or QA_DEFAULT_STT_TEXT, generation_id, metadata

        fixture_text = self._fixture_stt_text()
        if fixture_text is not None:
            generation_id = f"fixture-stt-{job_id}"
            stt_metadata["attemptCount"] = 1
            stt_metadata["transport"] = "fixture"
            stt_metadata["generationId"] = generation_id
            stt_metadata["completedAt"] = utc_now()
            cast_attempts = stt_metadata["attempts"]
            assert isinstance(cast_attempts, list)
            cast_attempts.append(
                {
                    "attempt": 1,
                    "outcome": "success",
                    "transport": "fixture",
                    "generationId": generation_id,
                }
            )
            return fixture_text, generation_id, metadata

        api_key = self._read_secret()
        payload = {
            "model": str(self.config["sttModel"]),
            "input_audio": {
                "data": base64.b64encode(audio_bytes).decode("utf-8"),
                "format": audio_format,
            },
        }

        attempts = stt_metadata["attempts"]
        assert isinstance(attempts, list)
        for attempt_index in range(1, STT_MAX_ATTEMPTS + 1):
            attempt_metadata: dict[str, object] = {"attempt": attempt_index}
            try:
                response_payload, response_headers = self._post_stt_request(api_key, payload)
                response_text = response_payload.get("text")
                if not isinstance(response_text, str) or not response_text.strip():
                    raise ServiceError(
                        "stt_request_failed",
                        "OpenRouter STT returned an empty transcript.",
                        details={"retryable": False},
                    )

                generation_id = response_headers.get("X-Generation-Id") or response_headers.get("x-generation-id")
                usage = response_payload.get("usage")
                attempt_metadata["outcome"] = "success"
                attempt_metadata["statusCode"] = 200
                if generation_id:
                    attempt_metadata["generationId"] = generation_id
                    stt_metadata["generationId"] = generation_id
                if isinstance(usage, dict):
                    attempt_metadata["usage"] = usage
                    stt_metadata["usage"] = usage
                attempts.append(attempt_metadata)
                stt_metadata["attemptCount"] = attempt_index
                stt_metadata["transport"] = "openrouter"
                stt_metadata["providerStatusCode"] = 200
                stt_metadata["completedAt"] = utc_now()
                return response_text.strip(), generation_id, metadata
            except ServiceError as exc:
                attempt_metadata["outcome"] = "error"
                attempt_metadata["errorCode"] = exc.code
                if "statusCode" in exc.details:
                    attempt_metadata["statusCode"] = exc.details["statusCode"]
                if "transportError" in exc.details:
                    attempt_metadata["transportError"] = exc.details["transportError"]
                attempts.append(attempt_metadata)
                stt_metadata["attemptCount"] = attempt_index
                stt_metadata["transport"] = "openrouter"
                stt_metadata["lastErrorCode"] = exc.code
                retryable = bool(exc.details.get("retryable"))
                if retryable and attempt_index < STT_MAX_ATTEMPTS:
                    stt_metadata["transientRetryUsed"] = True
                    time.sleep(STT_RETRY_DELAY_SECONDS)
                    continue
                raise ServiceError(
                    exc.code,
                    exc.message,
                    details={"requestMetadata": metadata, **exc.details},
                ) from exc

        raise ServiceError(
            "stt_provider_unavailable",
            "OpenRouter STT was temporarily unavailable. Please retry the job.",
            details={"requestMetadata": metadata, "retryable": False},
        )

    def _secret_configured(self) -> bool:
        return SECRET_PATH.exists() and SECRET_PATH.stat().st_size > 0

    def _health_status(self) -> str:
        return "ok" if LIVE_CONFIG_PATH.exists() and self.history_db_path.exists() else "degraded"

    def _state_payload(self) -> dict[str, object]:
        return {
            "lifecycleState": self.state.lifecycle_state,
            "activeJobId": self.state.active_job_id,
            "activeSessionId": self.state.active_session_id,
            "lastJobId": self.state.last_job_id,
        }

    def _config_summary(self) -> dict[str, object]:
        return {
            "socketPath": str(self.socket_path),
            "historyDbPath": str(self.history_db_path),
            "jobsCacheDir": str(self.jobs_cache_dir),
            "exportDirectory": str(self.export_directory),
            "defaultPromptPresetId": self.config["defaultPromptPresetId"],
            "sttModel": self.config["sttModel"],
            "cleanupModel": self.config["cleanupModel"],
            "completedJobRetentionDays": self.config["completedJobRetentionDays"],
            "failedJobRetentionDays": self.config["failedJobRetentionDays"],
            "failedJobAudioTtlHours": self.config["failedJobAudioTtlHours"],
            "secretConfigured": self._secret_configured(),
        }

    def snapshot(self) -> dict[str, object]:
        with self._connect_db() as connection:
            totals = connection.execute(
                """
                SELECT COUNT(*) AS jobCount,
                       SUM(CASE WHEN status = 'ready' THEN 1 ELSE 0 END) AS readyCount,
                       SUM(CASE WHEN status IN ('ready', 'failed', 'cancelled') THEN 1 ELSE 0 END) AS completedCount,
                       SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failedCount,
                       SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) AS cancelledCount
                FROM transcript_jobs
                """
            ).fetchone()

        return {
            "protocolVersion": PROTOCOL_VERSION,
            "serviceVersion": SERVICE_VERSION,
            "startedAt": self.started_at,
            "health": {
                "status": self._health_status(),
                "runtimeDir": str(self.runtime_dir),
                "socketReady": self.server is not None,
                "configPath": str(LIVE_CONFIG_PATH),
                "historyDbReady": self.history_db_path.exists(),
                "secretConfigured": self._secret_configured(),
            },
            "state": self._state_payload(),
            "configSummary": self._config_summary(),
            "historySummary": {
                "jobCount": int(totals["jobCount"] or 0),
                "readyCount": int(totals["readyCount"] or 0),
                "completedCount": int(totals["completedCount"] or 0),
                "failedCount": int(totals["failedCount"] or 0),
                "cancelledCount": int(totals["cancelledCount"] or 0),
            },
        }

    def ping(self) -> dict[str, object]:
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "serviceVersion": SERVICE_VERSION,
            "service": SERVICE_NAME,
            "status": self._health_status(),
            "uptimeSeconds": round(time.monotonic() - self.process_started_monotonic, 3),
        }

    def save_settings(self, params: dict[str, object]) -> dict[str, object]:
        settings = params.get("settings")
        if not isinstance(settings, dict):
            raise ServiceError("invalid_request", "saveSettings requires an object at params.settings")

        merged = dict(self.config)
        merged.update({str(key): value for key, value in settings.items()})
        with self._connect_db() as connection:
            self._ensure_prompt_preset_exists(connection, str(merged["defaultPromptPresetId"]))
            connection.commit()
        self._write_live_config(merged)
        return {
            "ack": True,
            "savedKeys": sorted(settings.keys()),
            "configSummary": self._config_summary(),
        }

    def save_secret(self, params: dict[str, object]) -> dict[str, object]:
        secret = params.get("secret")
        if not isinstance(secret, str) or not secret.strip():
            raise ServiceError("invalid_request", "saveSecret requires a non-empty params.secret string")

        SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
        SECRET_PATH.write_text(secret.strip(), encoding="utf-8")
        os.chmod(SECRET_PATH, stat.S_IRUSR | stat.S_IWUSR)
        return {
            "ack": True,
            "secretPath": str(SECRET_PATH),
            "secretConfigured": self._secret_configured(),
        }

    def list_prompt_presets(self, _params: dict[str, object]) -> dict[str, object]:
        with self._connect_db() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM prompts
                ORDER BY is_builtin DESC, created_at ASC, id ASC
                """
            ).fetchall()
        items = [self._serialize_prompt_preset(row) for row in rows]
        return {
            "items": items,
            "count": len(items),
            "defaultPromptPresetId": self.config["defaultPromptPresetId"],
        }

    def save_prompt_preset(self, params: dict[str, object]) -> dict[str, object]:
        preset_id = str(params.get("presetId") or uuid.uuid4()).strip()
        label = params.get("label")
        prompt_text = params.get("promptText")
        if not isinstance(label, str) or not label.strip():
            raise ServiceError("invalid_request", "savePromptPreset requires a non-empty params.label string")
        if not isinstance(prompt_text, str) or not prompt_text.strip():
            raise ServiceError("invalid_request", "savePromptPreset requires a non-empty params.promptText string")

        with self._connect_db() as connection:
            existing = self._read_prompt_preset(connection, preset_id)
            now = utc_now()
            normalized_prompt_text = prompt_text.strip()
            prompt_hash = hash_prompt_text(normalized_prompt_text)
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO prompts (
                        id,
                        label,
                        prompt_text,
                        prompt_hash,
                        is_builtin,
                        duplicated_from_prompt_id,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, 0, NULL, ?, ?)
                    """,
                    (preset_id, label.strip(), normalized_prompt_text, prompt_hash, now, now),
                )
            else:
                connection.execute(
                    """
                    UPDATE prompts
                    SET label = ?,
                        prompt_text = ?,
                        prompt_hash = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (label.strip(), normalized_prompt_text, prompt_hash, now, preset_id),
                )
            row = self._read_prompt_preset(connection, preset_id)
            connection.commit()

        assert row is not None
        return {"ack": True, "preset": self._serialize_prompt_preset(row)}

    def duplicate_prompt_preset(self, params: dict[str, object]) -> dict[str, object]:
        source_preset_id = params.get("sourcePresetId")
        if not isinstance(source_preset_id, str) or not source_preset_id.strip():
            raise ServiceError(
                "invalid_request",
                "duplicatePromptPreset requires a non-empty params.sourcePresetId string",
            )

        label_override = params.get("label")
        if label_override is not None and (not isinstance(label_override, str) or not label_override.strip()):
            raise ServiceError("invalid_request", "duplicatePromptPreset params.label must be a non-empty string")

        with self._connect_db() as connection:
            source_row = self._ensure_prompt_preset_exists(connection, source_preset_id.strip())
            preset_id = str(uuid.uuid4())
            now = utc_now()
            label = label_override.strip() if isinstance(label_override, str) else f"{source_row['label']} Copy"
            connection.execute(
                """
                INSERT INTO prompts (
                    id,
                    label,
                    prompt_text,
                    prompt_hash,
                    is_builtin,
                    duplicated_from_prompt_id,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (
                    preset_id,
                    label,
                    str(source_row["prompt_text"]),
                    str(source_row["prompt_hash"]),
                    str(source_row["id"]),
                    now,
                    now,
                ),
            )
            duplicated = self._read_prompt_preset(connection, preset_id)
            connection.commit()

        assert duplicated is not None
        return {"ack": True, "preset": self._serialize_prompt_preset(duplicated)}

    def _set_runtime_state(self, lifecycle_state: str, job_id: str | None, session_id: str | None) -> None:
        self.state.lifecycle_state = lifecycle_state
        self.state.last_job_id = job_id or self.state.last_job_id
        if lifecycle_state in ACTIVE_JOB_STATUSES:
            self.state.active_job_id = job_id
            self.state.active_session_id = session_id
            return

        self.state.active_job_id = None
        self.state.active_session_id = None

    def _update_session_activity(self, connection: sqlite3.Connection, session_id: str, job_id: str, updated_at: str) -> None:
        connection.execute(
            "UPDATE transcript_sessions SET updated_at = ?, last_job_id = ? WHERE id = ?",
            (updated_at, job_id, session_id),
        )

    def _update_job_row(self, job_id: str, **fields: object) -> None:
        if not fields:
            return

        assignments = ", ".join(f"{column} = ?" for column in fields)
        values = list(fields.values())
        values.append(job_id)
        with self._connect_db() as connection:
            connection.execute(
                f"UPDATE transcript_jobs SET {assignments} WHERE id = ?",
                values,
            )
            connection.commit()

    def _transition_job_state(self, job_id: str, session_id: str, status: str, **extra_fields: object) -> str:
        now = utc_now()
        payload: dict[str, object] = {
            "status": status,
            "updated_at": now,
            **extra_fields,
        }
        with self._connect_db() as connection:
            assignments = ", ".join(f"{column} = ?" for column in payload)
            values = list(payload.values())
            values.append(job_id)
            connection.execute(
                f"UPDATE transcript_jobs SET {assignments} WHERE id = ?",
                values,
            )
            self._update_session_activity(connection, session_id, job_id, now)
            connection.commit()

        self._set_runtime_state(status, job_id, session_id)
        return now

    def _read_job(self, job_id: str) -> sqlite3.Row | None:
        with self._connect_db() as connection:
            return connection.execute(
                "SELECT * FROM transcript_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()

    def _remove_temp_audio(self, job_id: str) -> bool:
        audio_path = self._job_audio_path(job_id)
        if not audio_path.exists():
            return False
        audio_path.unlink()
        return True

    def _mark_job_failed(
        self,
        job_id: str,
        session_id: str,
        code: str,
        message: str,
        **extra_fields: object,
    ) -> dict[str, object]:
        failed_at = self._transition_job_state(
            job_id,
            session_id,
            "failed",
            error_code=code,
            error_message=message,
            completed_at=utc_now(),
            **extra_fields,
        )
        self._update_job_row(job_id, updated_at=failed_at)
        return {
            "jobId": job_id,
            "sessionId": session_id,
            "status": "failed",
            "errorCode": code,
            "errorMessage": message,
        }

    def _complete_job(self, job_id: str, session_id: str) -> tuple[list[str], bool]:
        transition_path: list[str] = []
        self._transition_job_state(job_id, session_id, "transcribing")
        transition_path.append("transcribing")
        job_row = self._read_job(job_id)
        if job_row is None:
            raise ServiceError("job_not_found", f"No job exists with id {job_id}")
        qa_metadata = self._job_qa_metadata(job_row)
        raw_transcript, request_session_id, request_metadata = self._transcribe_audio(job_id, qa_metadata)
        persisted_request_metadata = self._job_request_metadata(job_row)
        merged_request_metadata = dict(persisted_request_metadata)
        merged_request_metadata.update(request_metadata)
        prompt_preset_id = str(job_row["prompt_preset_id"] or self.config["defaultPromptPresetId"])
        prompt_snapshot_text = str(job_row["prompt_snapshot_text"] or "").strip()
        prompt_snapshot_hash = str(job_row["prompt_snapshot_hash"] or "").strip()
        if not prompt_snapshot_text or not prompt_snapshot_hash:
            raise ServiceError("internal_error", f"Job {job_id} is missing its prompt snapshot.")
        transcribed_at = utc_now()
        self._transition_job_state(
            job_id,
            session_id,
            "postprocessing",
            raw_transcript=raw_transcript,
            stt_request_session_id=request_session_id,
            request_metadata_json=json.dumps(merged_request_metadata, sort_keys=True),
            transcribed_at=transcribed_at,
        )
        transition_path.append("postprocessing")
        try:
            processed_transcript, cleanup_request_session_id, cleanup_metadata = self._cleanup_transcript(
                raw_transcript,
                prompt_preset_id,
                prompt_snapshot_text,
                prompt_snapshot_hash,
                qa_metadata,
            )
        except ServiceError as exc:
            merged_request_metadata = dict(merged_request_metadata)
            cleanup_request_metadata = exc.details.get("requestMetadata")
            if isinstance(cleanup_request_metadata, dict):
                cleanup_section = cleanup_request_metadata.get("cleanup")
                if isinstance(cleanup_section, dict):
                    merged_request_metadata["cleanup"] = cleanup_section
            raise ServiceError(
                exc.code,
                exc.message,
                details={**exc.details, "requestMetadata": merged_request_metadata},
            ) from exc

        merged_request_metadata = dict(merged_request_metadata)
        cleanup_section = cleanup_metadata.get("cleanup")
        if isinstance(cleanup_section, dict):
            merged_request_metadata["cleanup"] = cleanup_section
        temp_audio_removed = self._remove_temp_audio(job_id)
        cleanup_completed_at = utc_now()
        self._transition_job_state(
            job_id,
            session_id,
            "ready",
            processed_transcript=processed_transcript,
            cleanup_request_session_id=cleanup_request_session_id,
            request_metadata_json=json.dumps(merged_request_metadata, sort_keys=True),
            cleanup_completed_at=cleanup_completed_at,
            completed_at=utc_now(),
        )
        transition_path.append("ready")
        return transition_path, temp_audio_removed

    def start_recording(self, params: dict[str, object]) -> dict[str, object]:
        if self.state.active_job_id:
            raise ServiceError(
                "recording_active",
                "A recording is already active.",
                details={
                    "idempotent": True,
                    "state": self._state_payload(),
                    "jobId": self.state.active_job_id,
                },
            )

        now = utc_now()
        session_id = str(params.get("sessionId") or uuid.uuid4())
        client_source = str(params.get("clientSource") or "plugin")
        job_id = str(uuid.uuid4())
        selected_prompt_preset_id = str(
            params.get("promptPresetId") or self.config["defaultPromptPresetId"]
        ).strip()
        if not selected_prompt_preset_id:
            raise ServiceError("invalid_request", "startRecording requires a non-empty prompt preset id")
        audio_path = self._job_audio_path(job_id)
        audio_path.touch(exist_ok=False)
        request_metadata: dict[str, object] = {}
        if self._deterministic_qa_requested(params):
            fixture_audio_path = self._materialize_qa_fixture_audio(audio_path)
            request_metadata["qa"] = {
                "mode": QA_MOCK_PROVIDER,
                "mockProvider": QA_MOCK_PROVIDER,
                "fixtureAudioFile": fixture_audio_path.name,
            }

        with self._connect_db() as connection:
            prompt_row = self._ensure_prompt_preset_exists(connection, selected_prompt_preset_id)
            connection.execute(
                """
                INSERT OR IGNORE INTO transcript_sessions (id, created_at, updated_at, client_source)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, now, now, client_source),
            )
            connection.execute(
                """
                INSERT INTO transcript_jobs (
                    id, session_id, status, prompt_preset_id, prompt_snapshot_text, prompt_snapshot_hash, stt_model_id, cleanup_model_id,
                    created_at, updated_at, recording_started_at, request_metadata_json
                ) VALUES (?, ?, 'recording', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    session_id,
                    str(prompt_row["id"]),
                    str(prompt_row["prompt_text"]),
                    str(prompt_row["prompt_hash"]),
                    self.config["sttModel"],
                    self.config["cleanupModel"],
                    now,
                    now,
                    now,
                    json.dumps(request_metadata, sort_keys=True),
                ),
            )
            connection.execute(
                "UPDATE transcript_sessions SET updated_at = ?, last_job_id = ? WHERE id = ?",
                (now, job_id, session_id),
            )
            connection.commit()

        self._set_runtime_state("recording", job_id, session_id)
        return {
            "ack": True,
            "state": self._state_payload(),
            "job": {
                "jobId": job_id,
                "sessionId": session_id,
                "status": "recording",
                "promptPresetId": selected_prompt_preset_id,
                "audioPath": str(audio_path),
            },
        }

    def stop_recording(self) -> dict[str, object]:
        if self.state.lifecycle_state != "recording" or not self.state.active_job_id:
            raise ServiceError("no_active_recording", "There is no active recording to stop.")

        job_id = self.state.active_job_id
        session_id = self.state.active_session_id
        if session_id is None:
            raise ServiceError("internal_error", f"Active job {job_id} is missing its session context.")

        self._transition_job_state(
            job_id,
            session_id,
            "finalizing",
            recording_stopped_at=utc_now(),
        )
        transition_path = ["finalizing"]
        try:
            completed_transitions, temp_audio_removed = self._complete_job(job_id, session_id)
            transition_path.extend(completed_transitions)
        except ServiceError as exc:
            if exc.code == "audio_missing":
                self._transition_job_state(
                    job_id,
                    session_id,
                    "cancelled",
                    error_code="audio_missing",
                    error_message=exc.message,
                    completed_at=utc_now(),
                )
                temp_audio_removed = self._remove_temp_audio(job_id)
                return {
                    "ack": True,
                    "jobId": job_id,
                    "state": self._state_payload(),
                    "transitionPath": [*transition_path, "cancelled"],
                    "job": {
                        "jobId": job_id,
                        "sessionId": session_id,
                        "status": "cancelled",
                        "errorCode": "audio_missing",
                        "errorMessage": exc.message,
                        "tempAudioRemoved": temp_audio_removed,
                    },
                }

            request_metadata = exc.details.get("requestMetadata")
            extra_fields: dict[str, object] = {}
            if isinstance(request_metadata, dict):
                extra_fields["request_metadata_json"] = json.dumps(request_metadata, sort_keys=True)
            failed_job = self._mark_job_failed(
                job_id,
                session_id,
                exc.code,
                exc.message,
                **extra_fields,
            )
            raise ServiceError(
                exc.code,
                exc.message,
                details={
                    "state": self._state_payload(),
                    "job": failed_job,
                    "transitionPath": [*transition_path, "failed"],
                },
            ) from exc
        except Exception as exc:
            failed_job = self._mark_job_failed(
                job_id,
                session_id,
                "post_recording_pipeline_failed",
                f"The placeholder Task 4 processing pipeline failed: {exc}",
            )
            raise ServiceError(
                "job_failed",
                failed_job["errorMessage"],
                details={
                    "state": self._state_payload(),
                    "job": failed_job,
                    "transitionPath": [*transition_path, "failed"],
                },
            ) from exc

        return {
            "ack": True,
            "jobId": job_id,
            "state": self._state_payload(),
            "transitionPath": transition_path,
            "job": {
                "jobId": job_id,
                "sessionId": session_id,
                "status": "ready",
                "audioPath": str(self._job_audio_path(job_id)),
                "tempAudioRemoved": temp_audio_removed,
            },
        }

    def cancel_job(self, params: dict[str, object]) -> dict[str, object]:
        job_id = str(params.get("jobId") or self.state.active_job_id or "").strip()
        if not job_id:
            raise ServiceError("invalid_request", "cancelJob requires params.jobId when no recording is active.")

        session_id: str | None = None
        status: str | None = None
        with self._connect_db() as connection:
            row = connection.execute(
                "SELECT session_id, status FROM transcript_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise ServiceError("job_not_found", f"No job exists with id {job_id}")
            session_id = str(row["session_id"])
            status = LEGACY_JOB_STATUS_MAP.get(str(row["status"]), str(row["status"]))

        if status not in TERMINAL_JOB_STATUSES:
            self._transition_job_state(
                job_id,
                session_id,
                "cancelled",
                error_code="cancelled_by_client",
                error_message="The job was cancelled through the local IPC contract.",
                completed_at=utc_now(),
            )
        else:
            self._set_runtime_state(status, None, None)

        temp_audio_removed = self._remove_temp_audio(job_id)
        self.state.last_job_id = job_id
        return {
            "ack": True,
            "jobId": job_id,
            "state": self._state_payload(),
            "job": {
                "jobId": job_id,
                "sessionId": session_id,
                "status": "cancelled" if status not in TERMINAL_JOB_STATUSES else status,
                "tempAudioRemoved": temp_audio_removed,
            },
        }

    def list_history(self, params: dict[str, object]) -> dict[str, object]:
        raw_limit = params.get("limit", 20)
        if not isinstance(raw_limit, int) or raw_limit <= 0:
            raise ServiceError("invalid_request", "listHistory requires a positive integer params.limit")

        with self._connect_db() as connection:
            rows = connection.execute(
                """
                SELECT j.id,
                       j.session_id,
                       j.status,
                       j.raw_transcript,
                       j.processed_transcript,
                       j.prompt_preset_id,
                       j.prompt_snapshot_hash,
                       j.stt_model_id,
                       j.cleanup_model_id,
                       j.created_at,
                       j.completed_at,
                       j.error_code,
                       COUNT(e.id) AS export_count
                FROM transcript_jobs j
                LEFT JOIN transcript_exports e ON e.job_id = j.id
                GROUP BY j.id
                ORDER BY j.created_at DESC
                LIMIT ?
                """,
                (raw_limit,),
            ).fetchall()

        items: list[dict[str, object]] = []
        for row in rows:
            items.append(
                {
                    "jobId": row["id"],
                    "sessionId": row["session_id"],
                    "status": row["status"],
                    "rawTranscript": row["raw_transcript"],
                    "processedTranscript": row["processed_transcript"],
                    "promptPresetId": row["prompt_preset_id"],
                    "promptSnapshotHash": row["prompt_snapshot_hash"],
                    "sttModel": row["stt_model_id"],
                    "cleanupModel": row["cleanup_model_id"],
                    "createdAt": row["created_at"],
                    "completedAt": row["completed_at"],
                    "errorCode": row["error_code"],
                    "exportCount": int(row["export_count"] or 0),
                }
            )

        return {"items": items, "count": len(items)}

    def export_item(self, params: dict[str, object]) -> dict[str, object]:
        job_id = params.get("jobId")
        if not isinstance(job_id, str) or not job_id.strip():
            raise ServiceError("invalid_request", "exportItem requires a non-empty params.jobId string")

        with self._connect_db() as connection:
            row = connection.execute(
                """
                SELECT j.*, s.id AS session_identity
                FROM transcript_jobs j
                JOIN transcript_sessions s ON s.id = j.session_id
                WHERE j.id = ?
                """,
                (job_id,),
            ).fetchone()
            if row is None:
                raise ServiceError("job_not_found", f"No job exists with id {job_id}")

            export_id = str(uuid.uuid4())
            completed_at = row["completed_at"] or row["updated_at"] or row["created_at"]
            filename = self._export_filename(job_id, str(completed_at))
            export_path = self.export_directory / filename
            template = EXPORT_TEMPLATE_PATH.read_text(encoding="utf-8")
            rendered = (
                template.replace("{{exportedAt}}", utc_now())
                .replace("{{sessionId}}", str(row["session_identity"]))
                .replace("{{jobId}}", job_id)
                .replace("{{status}}", str(row["status"] or ""))
                .replace("{{promptPresetId}}", str(row["prompt_preset_id"] or ""))
                .replace("{{promptSnapshotHash}}", str(row["prompt_snapshot_hash"] or ""))
                .replace("{{sttModelId}}", str(row["stt_model_id"] or ""))
                .replace("{{cleanupModelId}}", str(row["cleanup_model_id"] or ""))
                .replace("{{createdAt}}", str(row["created_at"] or ""))
                .replace("{{completedAt}}", str(row["completed_at"] or ""))
                .replace("{{errorCode}}", str(row["error_code"] or ""))
                .replace("{{rawTranscript}}", str(row["raw_transcript"] or ""))
                .replace("{{processedTranscript}}", str(row["processed_transcript"] or ""))
            )
            export_path.write_text(rendered, encoding="utf-8")
            connection.execute(
                """
                INSERT INTO transcript_exports (id, job_id, export_format, export_path, created_at)
                VALUES (?, ?, 'markdown', ?, ?)
                """,
                (export_id, job_id, str(export_path), utc_now()),
            )
            connection.commit()

        return {
            "ack": True,
            "exportId": export_id,
            "jobId": job_id,
            "exportPath": str(export_path),
        }

    def dispatch(self, request: dict[str, object]) -> dict[str, object]:
        command = request.get("command")
        if command not in PUBLIC_COMMANDS:
            allowed = ", ".join(sorted(PUBLIC_COMMANDS))
            raise ServiceError("invalid_command", f"Unsupported command {command!r}. Allowed commands: {allowed}")
        params = request.get("params") or {}
        if not isinstance(params, dict):
            raise ServiceError("invalid_request", "params must be an object when provided")

        if command == "snapshot":
            return self.snapshot()
        if command == "ping":
            return self.ping()
        if command == "saveSettings":
            return self.save_settings(params)
        if command == "saveSecret":
            return self.save_secret(params)
        if command == "listPromptPresets":
            return self.list_prompt_presets(params)
        if command == "savePromptPreset":
            return self.save_prompt_preset(params)
        if command == "duplicatePromptPreset":
            return self.duplicate_prompt_preset(params)
        if command == "startRecording":
            return self.start_recording(params)
        if command == "stopRecording":
            return self.stop_recording()
        if command == "cancelJob":
            return self.cancel_job(params)
        if command == "listHistory":
            return self.list_history(params)
        if command == "exportItem":
            return self.export_item(params)
        raise ServiceError("invalid_command", f"Unhandled command {command!r}")

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request: dict[str, object] | None = None
        try:
            raw_request = await reader.readline()
            if not raw_request:
                return
            try:
                request = json.loads(raw_request.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise ServiceError("invalid_json", f"Request body must be valid JSON: {exc.msg}") from exc

            if not isinstance(request, dict):
                raise ServiceError("invalid_request", "Request body must decode to a JSON object")

            result = self.dispatch(request)
            response = {
                "ok": True,
                "requestId": request.get("requestId"),
                "command": request.get("command"),
                "protocolVersion": PROTOCOL_VERSION,
                "serviceVersion": SERVICE_VERSION,
                "result": result,
            }
        except ServiceError as exc:
            request_id = request.get("requestId") if request is not None else None
            command = request.get("command") if request is not None else None
            response = {
                "ok": False,
                "requestId": request_id,
                "command": command,
                "protocolVersion": PROTOCOL_VERSION,
                "serviceVersion": SERVICE_VERSION,
                "error": {"code": exc.code, "message": exc.message, "details": exc.details},
            }
        except Exception as exc:  # pragma: no cover - defensive runtime guard
            request_id = request.get("requestId") if request is not None else None
            command = request.get("command") if request is not None else None
            response = {
                "ok": False,
                "requestId": request_id,
                "command": command,
                "protocolVersion": PROTOCOL_VERSION,
                "serviceVersion": SERVICE_VERSION,
                "error": {"code": "internal_error", "message": str(exc)},
            }

        writer.write((json.dumps(response) + "\n").encode("utf-8"))
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    def _prepare_socket(self) -> None:
        if self.socket_path.exists():
            if not stat.S_ISSOCK(self.socket_path.stat().st_mode):
                raise ServiceError(
                    "socket_path_conflict",
                    f"Existing socket path is not a Unix socket: {self.socket_path}",
                )
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                try:
                    probe.connect(str(self.socket_path))
                except OSError:
                    self.socket_path.unlink()
                else:
                    raise ServiceError(
                        "service_already_running",
                        f"A helper service is already listening on {self.socket_path}",
                    )

    async def run(self) -> None:
        self._prepare_socket()
        self.server = await asyncio.start_unix_server(self.handle_client, path=str(self.socket_path))
        stop_loop = asyncio.get_running_loop()
        for signame in (signal.SIGINT, signal.SIGTERM):
            stop_loop.add_signal_handler(signame, self.shutdown_event.set)

        try:
            await self.shutdown_event.wait()
        finally:
            assert self.server is not None
            self.server.close()
            await self.server.wait_closed()
            if self.socket_path.exists():
                self.socket_path.unlink()


def main() -> None:
    try:
        service = VoiceWidgetService()
        asyncio.run(service.run())
    except ServiceError as exc:
        raise SystemExit(f"{exc.code}: {exc.message}") from exc


if __name__ == "__main__":
    main()
