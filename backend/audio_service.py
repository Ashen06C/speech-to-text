"""Validate and decode one complete WAV upload without keeping an audio file."""

import io
import wave
from dataclasses import dataclass

import numpy as np
import soundfile as sf
from fastapi import UploadFile

from backend.config import Settings


class AudioValidationError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class DecodedAudio:
    samples: np.ndarray
    duration_seconds: float


def decode_wav_upload(upload: UploadFile, settings: Settings) -> DecodedAudio:
    """Run from a synchronous route/worker thread; never trust extension or MIME."""
    if upload.size is not None and upload.size > settings.max_upload_bytes:
        raise AudioValidationError("Audio file exceeds the configured upload size limit.", 413)
    upload.file.seek(0)
    payload = upload.file.read(settings.max_upload_bytes + 1)
    if len(payload) > settings.max_upload_bytes:
        raise AudioValidationError("Audio file exceeds the configured upload size limit.", 413)
    if not payload:
        raise AudioValidationError("Audio file is empty.")

    try:
        with sf.SoundFile(io.BytesIO(payload)) as wav:
            if wav.format not in {"WAV", "WAVEX"}:
                raise AudioValidationError("Only WAV audio files are supported.")
            if wav.samplerate != settings.sample_rate:
                raise AudioValidationError("Sample rate must be 16000 Hz; automatic resampling is disabled.")
            if wav.channels != 1:
                raise AudioValidationError("Audio must be mono (one channel).")
            if wav.subtype != "PCM_16":
                raise AudioValidationError("Audio must use 16-bit Linear PCM (PCM_16).")
            if wav.frames == 0:
                raise AudioValidationError("WAV file contains no audio samples.")

            # libsndfile may silently clamp truncated data; compare the declared WAV
            # frame count with actual bytes before accepting the decoded samples.
            with wave.open(io.BytesIO(payload), "rb") as header:
                declared_frames = header.getnframes()
                pcm_bytes = header.readframes(declared_frames)
                if len(pcm_bytes) != declared_frames * 2 or wav.frames != declared_frames:
                    raise AudioValidationError("WAV audio data is truncated or incomplete.")

            samples = wav.read(dtype="float32", always_2d=False)
            if samples.size != declared_frames or samples.size == 0:
                raise AudioValidationError("WAV audio data is empty or incomplete.")
            return DecodedAudio(samples, samples.size / settings.sample_rate)
    except AudioValidationError:
        raise
    except (sf.LibsndfileError, wave.Error, EOFError, ValueError, OSError) as exc:
        raise AudioValidationError("Invalid or unreadable WAV file.") from exc
