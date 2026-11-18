r"""Run from the project root:

    .\.venv\Scripts\python.exe -m backend.tests.test_api
    .\.venv\Scripts\python.exe -m backend.tests.test_api --real

Default tests mock only the model, exercising the actual service and REST API.
--real loads the cached Qwen model once and runs a small integration smoke test.
"""

import io
import json
import sys
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from backend.asr_service import ASRService
from backend.config import Settings
from backend.main import create_app

SAMPLE = Path(__file__).resolve().parents[2] / "samples" / "english.wav"


def make_wav(*, rate=16_000, channels=1, subtype="PCM_16", frames=1600) -> bytes:
    destination = io.BytesIO()
    data = np.zeros((frames, channels), dtype=np.float32)
    sf.write(destination, data, rate, format="WAV", subtype=subtype)
    return destination.getvalue()


def post_audio(client, payload: bytes, language="English"):
    return client.post(
        "/api/transcribe",
        data={"language": language},
        files={"file": ("audio.wav", payload, "audio/wav")},
    )


class APITests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings()
        self.service = ASRService(self.settings)
        self.model = Mock()
        self.model.transcribe.return_value = [SimpleNamespace(text="Mock recognized speech.")]
        self.loader = patch.object(self.service, "_create_model", return_value=self.model)
        self.create_model = self.loader.start()
        self.addCleanup(self.loader.stop)
        self.client = TestClient(create_app(self.settings, self.service))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_health(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "status": "healthy", "model": "Qwen3-ASR-0.6B",
            "device": "cpu", "model_loaded": True,
        })

    def test_system(self):
        response = self.client.get("/api/system")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        for key in ("cpu_logical_processors", "cpu_physical_cores", "python_version"):
            self.assertIn(key, body)
        self.assertGreater(body["total_ram_bytes"], 0)
        self.assertGreater(body["process_ram_bytes"], 0)
        self.assertEqual(body["device"], "cpu")

    def test_english_sample_and_model_reuse(self):
        payload = SAMPLE.read_bytes()
        for _ in range(2):
            response = post_audio(self.client, payload)
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertTrue(body["success"])
            self.assertEqual(body["transcript"], "Mock recognized speech.")
            self.assertEqual(body["language"], "English")
            self.assertEqual(body["device"], "cpu")
            with wave.open(str(SAMPLE), "rb") as wav:
                self.assertAlmostEqual(body["audio_duration_seconds"], wav.getnframes() / wav.getframerate())
            self.assertGreaterEqual(body["inference_time_seconds"], 0)
        self.create_model.assert_called_once()
        audio, rate = self.model.transcribe.call_args.kwargs["audio"]
        self.assertEqual(rate, 16_000)
        self.assertEqual(audio.dtype, np.float32)
        self.assertEqual(audio.ndim, 1)
        self.assertLessEqual(float(np.max(np.abs(audio))), 1.0)

    def test_all_supported_languages(self):
        for language in ("English", "Chinese", "Indonesian"):
            with self.subTest(language=language):
                response = post_audio(self.client, make_wav(), language)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["language"], language)
                self.assertEqual(self.model.transcribe.call_args.kwargs["language"], language)

    def assert_invalid(self, payload, message, status=400):
        response = post_audio(self.client, payload)
        self.assertEqual(response.status_code, status, response.text)
        self.assertIn(message, response.json()["detail"])
        self.model.transcribe.assert_not_called()

    def test_invalid_file(self):
        self.assert_invalid(b"This is not audio", "Invalid")

    def test_non_wav_container(self):
        data = io.BytesIO()
        sf.write(data, np.zeros(1600), 16_000, format="FLAC", subtype="PCM_16")
        self.assert_invalid(data.getvalue(), "Only WAV")

    def test_empty_file(self):
        self.assert_invalid(b"", "empty")

    def test_empty_wav(self):
        self.assert_invalid(make_wav(frames=0), "no audio samples")

    def test_wrong_sample_rate(self):
        self.assert_invalid(make_wav(rate=44_100), "16000")

    def test_stereo(self):
        self.assert_invalid(make_wav(channels=2), "mono")

    def test_wrong_bit_depth(self):
        self.assert_invalid(make_wav(subtype="PCM_24"), "16-bit Linear PCM")

    def test_float_encoding(self):
        self.assert_invalid(make_wav(subtype="FLOAT"), "16-bit Linear PCM")

    def test_truncated_wav(self):
        self.assert_invalid(make_wav()[:-10], "truncated")

    def test_oversized_upload(self):
        tiny_settings = Settings(max_upload_bytes=100)
        with TestClient(create_app(tiny_settings, self.service)) as client:
            response = post_audio(client, make_wav())
        self.assertEqual(response.status_code, 413)
        self.model.transcribe.assert_not_called()

    def test_unsupported_language(self):
        response = post_audio(self.client, make_wav(), "French")
        self.assertEqual(response.status_code, 422)
        self.assertIn("language", str(response.json()["detail"]))
        self.model.transcribe.assert_not_called()

    def test_missing_file(self):
        response = self.client.post("/api/transcribe", data={"language": "English"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("file", str(response.json()["detail"]))

    def test_missing_language(self):
        response = self.client.post("/api/transcribe", files={"file": ("test.wav", make_wav())})
        self.assertEqual(response.status_code, 422)
        self.assertIn("language", str(response.json()["detail"]))

    def test_model_not_loaded(self):
        self.service.unload_model()
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["model_loaded"])
        self.assertEqual(post_audio(self.client, make_wav()).status_code, 503)

    def test_inference_failure(self):
        self.model.transcribe.side_effect = RuntimeError("simulated model error")
        with self.assertLogs("backend.asr_service", level="ERROR"):
            response = post_audio(self.client, make_wav())
        self.assertEqual(response.status_code, 500)
        self.assertIn("Transcription failed", response.json()["detail"])

    def test_invalid_model_result(self):
        self.model.transcribe.return_value = []
        with self.assertLogs("backend.asr_service", level="ERROR"):
            response = post_audio(self.client, make_wav())
        self.assertEqual(response.status_code, 500)

    def test_startup_failure_is_visible(self):
        service = ASRService(self.settings)
        with patch.object(service, "_create_model", side_effect=RuntimeError("cache missing")):
            with self.assertLogs("backend.main", level="ERROR"):
                with TestClient(create_app(self.settings, service)) as client:
                    self.assertEqual(client.get("/health").status_code, 503)
                    self.assertEqual(client.get("/api/system").status_code, 200)
                    self.assertEqual(post_audio(client, make_wav()).status_code, 503)


@unittest.skipUnless("--real" in sys.argv, "Real model check is opt-in: python -m backend.tests.test_api --real")
class RealModelSmokeTest(unittest.TestCase):
    def test_real_backend(self):
        with TestClient(create_app()) as client:
            for path in ("/health", "/api/system"):
                response = client.get(path)
                print(f"\nGET {path}: {response.status_code}", flush=True)
                print(json.dumps(response.json(), indent=2), flush=True)
                self.assertEqual(response.status_code, 200, response.text)
            response = post_audio(client, SAMPLE.read_bytes())
            print(f"\nPOST /api/transcribe: {response.status_code}", flush=True)
            print(json.dumps(response.json(), indent=2, ensure_ascii=False), flush=True)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["transcript"].strip())
            self.assertGreater(response.json()["inference_time_seconds"], 0)
            self.assertGreater(response.json()["audio_duration_seconds"], 0)
            invalid = post_audio(client, b"not a wav")
            unsupported = post_audio(client, SAMPLE.read_bytes(), "French")
            print(f"\nInvalid WAV: {invalid.status_code}; unsupported language: {unsupported.status_code}", flush=True)
            self.assertEqual(invalid.status_code, 400)
            self.assertEqual(unsupported.status_code, 422)


if __name__ == "__main__":
    suite_type = RealModelSmokeTest if "--real" in sys.argv else APITests
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(suite_type)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
