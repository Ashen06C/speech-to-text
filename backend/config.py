"""Small, environment-configurable settings; no GPU configuration."""

import os
from dataclasses import dataclass, field
from enum import Enum


class Language(str, Enum):
    ENGLISH = "English"
    CHINESE = "Chinese"
    INDONESIAN = "Indonesian"


def positive_env_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer.")
    return value


@dataclass(frozen=True)
class Settings:
    model_id: str = "Qwen/Qwen3-ASR-0.6B"
    model_name: str = "Qwen3-ASR-0.6B"
    device: str = "cpu"
    sample_rate: int = 16_000
    cpu_threads: int = field(default_factory=lambda: positive_env_int("ASR_CPU_THREADS", 6))
    max_upload_bytes: int = field(
        default_factory=lambda: positive_env_int("ASR_MAX_UPLOAD_MB", 10) * 1024 * 1024
    )
    max_new_tokens: int = 256
    window_ms: int = field(default_factory=lambda: positive_env_int("ASR_WINDOW_MS", 3000))
    pending_windows: int = field(default_factory=lambda: positive_env_int("ASR_PENDING_WINDOWS", 4))
    max_streams: int = field(default_factory=lambda: positive_env_int("ASR_MAX_STREAMS", 2))
    max_stream_seconds: int = field(default_factory=lambda: positive_env_int("ASR_MAX_STREAM_SECONDS", 600))
    stream_idle_seconds: int = 30
    stream_send_timeout_seconds: int = 10

    def __post_init__(self):
        if not 1000 <= self.window_ms <= 30_000:
            raise ValueError("ASR_WINDOW_MS must be between 1000 and 30000.")
        if min(self.pending_windows, self.max_streams, self.max_stream_seconds) <= 0:
            raise ValueError("Streaming queue, session, and duration limits must be positive.")
