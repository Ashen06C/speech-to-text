"""Small per-session PCM buffer; each sample belongs to exactly one window."""

from dataclasses import dataclass

import numpy as np


class PCMError(ValueError):
    pass


@dataclass(frozen=True)
class AudioWindow:
    samples: np.ndarray
    start_sample: int
    end_sample: int


class AudioBuffer:
    def __init__(self, sample_rate: int, window_ms: int, chunk_ms: int):
        self.window_samples = sample_rate * window_ms // 1000
        self.chunk_bytes = sample_rate * chunk_ms // 1000 * 2
        self.samples_received = 0
        self.samples_extracted = 0
        self._pcm = bytearray()
        self._short_chunk_received = False

    def append(self, pcm: bytes) -> None:
        if not pcm or len(pcm) % 2:
            raise PCMError("PCM chunks must be nonempty and contain complete 16-bit samples.")
        if len(pcm) > self.chunk_bytes:
            raise PCMError(f"Chunk exceeds the configured {self.chunk_bytes} bytes.")
        if self._short_chunk_received:
            raise PCMError("A short chunk must be the last audio chunk, followed by end.")
        if pcm[:4] == b"RIFF" and pcm[8:12] == b"WAVE":
            raise PCMError("Send raw PCM samples, without a WAV header.")
        self._short_chunk_received = len(pcm) < self.chunk_bytes
        self._pcm.extend(pcm)
        self.samples_received += len(pcm) // 2

    def pop_window(self, *, final: bool = False) -> AudioWindow | None:
        available = len(self._pcm) // 2
        if available == 0 or (available < self.window_samples and not final):
            return None
        count = min(available, self.window_samples)
        pcm = bytes(self._pcm[:count * 2])
        del self._pcm[:count * 2]
        # Explicit little-endian signed PCM16, normalized to [-1, 1).
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / np.float32(32768)
        start = self.samples_extracted
        self.samples_extracted += count
        return AudioWindow(samples, start, self.samples_extracted)

    def clear(self) -> None:
        self._pcm.clear()
