"""Load a cached Qwen model once and serialize CPU inference."""

import logging
from dataclasses import dataclass
from threading import Lock
from time import perf_counter

import numpy as np

from backend.config import Language, Settings

logger = logging.getLogger(__name__)


class ModelNotLoadedError(RuntimeError):
    pass


class InferenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Transcription:
    text: str
    inference_time_seconds: float


class ASRService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._model = None
        self._lock = Lock()

    @property
    def model_loaded(self) -> bool:
        return self._model is not None

    def _create_model(self):
        # Lazy imports keep mocked API tests independent of model initialization.
        import torch
        from huggingface_hub import snapshot_download
        from qwen_asr import Qwen3ASRModel

        torch.set_num_threads(self.settings.cpu_threads)
        # Resolve to a local directory so BOTH model and processor reuse the cache.
        # No network request or model download is permitted by this backend.
        model_path = snapshot_download(self.settings.model_id, local_files_only=True)
        return Qwen3ASRModel.from_pretrained(
            model_path,
            dtype=torch.float32,
            device_map="cpu",
            local_files_only=True,
            max_inference_batch_size=1,
            max_new_tokens=self.settings.max_new_tokens,
        )

    def load_model(self) -> None:
        with self._lock:
            if self._model is None:
                logger.info("Loading cached %s on CPU", self.settings.model_name)
                self._model = self._create_model()
                logger.info("ASR model ready")

    def unload_model(self) -> None:
        with self._lock:
            self._model = None

    def transcribe(self, audio: np.ndarray, language: Language | str) -> Transcription:
        """Accept normalized float32 mono samples at the configured 16 kHz rate."""
        language = Language(language)
        if audio.dtype != np.float32 or audio.ndim != 1 or audio.size == 0:
            raise ValueError("Expected nonempty mono float32 audio at 16 kHz.")
        if not np.isfinite(audio).all():
            raise ValueError("Audio contains non-finite samples.")

        # This lock protects the shared model. Waiting time is excluded from timing.
        with self._lock:
            if self._model is None:
                raise ModelNotLoadedError("ASR model is not loaded. Check the server startup log.")
            try:
                import torch

                started = perf_counter()
                with torch.inference_mode():
                    results = self._model.transcribe(
                        audio=(audio, self.settings.sample_rate), language=language.value
                    )
                elapsed = perf_counter() - started
                if not results or not isinstance(results[0].text, str):
                    raise RuntimeError("Model returned no valid transcription result.")
                return Transcription(results[0].text, elapsed)
            except Exception as exc:
                logger.exception("Qwen3-ASR inference failed")
                raise InferenceError("Transcription failed. Check the server log for details.") from exc
