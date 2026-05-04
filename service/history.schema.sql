PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS transcript_sessions (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_job_id TEXT,
    client_source TEXT NOT NULL DEFAULT 'plugin',
    notes TEXT,
    FOREIGN KEY (last_job_id) REFERENCES transcript_jobs(id)
);

CREATE TABLE IF NOT EXISTS transcript_jobs (
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
);

CREATE TABLE IF NOT EXISTS prompts (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    prompt_text TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    is_builtin INTEGER NOT NULL DEFAULT 0 CHECK (is_builtin IN (0, 1)),
    duplicated_from_prompt_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (duplicated_from_prompt_id) REFERENCES prompts(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_transcript_jobs_session_id
    ON transcript_jobs(session_id);

CREATE INDEX IF NOT EXISTS idx_transcript_jobs_status
    ON transcript_jobs(status);

CREATE INDEX IF NOT EXISTS idx_transcript_jobs_completed_at
    ON transcript_jobs(completed_at);

CREATE INDEX IF NOT EXISTS idx_prompts_is_builtin
    ON prompts(is_builtin, created_at);

CREATE TABLE IF NOT EXISTS transcript_exports (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    export_format TEXT NOT NULL CHECK (export_format = 'markdown'),
    export_path TEXT NOT NULL,
    template_version TEXT NOT NULL DEFAULT 'v1',
    created_at TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES transcript_jobs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_transcript_exports_job_id
    ON transcript_exports(job_id);
