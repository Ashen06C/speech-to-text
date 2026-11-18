"""Multilingual functional tests with mocked ASR; no generated speech recordings.

Run from the project root with python -m backend.tests.test_multilingual.
"""

import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from backend.asr_service import ASRService
from backend.config import Language, Settings
from backend.main import create_app
from backend.validate_multilingual import check_events, classify_completed, compare_reference
from backend.validate_samples import inspect_sample, load_manifest

TEXT = {"English": ["Hello everyone.", "This is a test."],
        "Chinese": ["大家好，", "这是一次测试。"],
        "Indonesian": ["Halo semuanya.", "Ini adalah pengujian."]}
START = {"type": "start", "sample_rate": 16000, "channels": 1, "bit_depth": 16, "chunk_duration_ms": 500}


class MultilingualTests(unittest.TestCase):
    def test_manifest_uses_new_recordings_and_correct_reference_pairs(self):
        entries = load_manifest()
        self.assertEqual([entry["language"] for entry in entries], ["English", "Chinese", "Indonesian"])
        self.assertEqual(entries[0]["audio"], "samples/audios/english.wav")
        self.assertEqual(entries[0]["reference"], "samples/references/english_reference.txt")
        self.assertNotEqual(entries[0]["audio"], "samples/english.wav")

    def test_installed_qwen_language_identifiers(self):
        from qwen_asr.inference.utils import SUPPORTED_LANGUAGES, validate_language
        for language in Language:
            self.assertIn(language.value, SUPPORTED_LANGUAGES)
            validate_language(language.value)

    def test_all_languages_share_one_model_and_preserve_order_and_unicode(self):
        counts = {language: 0 for language in TEXT}

        def infer(**kwargs):
            language = kwargs["language"]
            text = TEXT[language][counts[language]]
            counts[language] += 1
            return [SimpleNamespace(text=text)]

        model = Mock()
        model.transcribe.side_effect = infer
        service = ASRService(Settings())
        with patch.object(service, "_create_model", return_value=model) as loader:
            with TestClient(create_app(service=service)) as client:
                for language in TEXT:
                    with self.subTest(language=language), client.websocket_connect("/ws/transcribe") as ws:
                        ws.send_json(START | {"language": language})
                        self.assertEqual(ws.receive_json()["type"], "ready")
                        for _ in range(7):
                            ws.send_bytes(bytes(16000))  # Silence fixture + mock output only.
                        ws.send_json({"type": "end"})
                        events = []
                        while not events or events[-1]["type"] != "completed":
                            events.append(ws.receive_json())
                        result = check_events(events, language, 3.5)
                        self.assertEqual(result["actual_transcript"], ("" if language == "Chinese" else " ").join(TEXT[language]))
                        self.assertEqual(result["segment_count"], 2)
                loader.assert_called_once()
                self.assertEqual(client.get("/health").status_code, 200)
        self.assertEqual(counts, {language: 2 for language in TEXT})

    def test_model_errors_are_reported_for_each_language(self):
        model = Mock()
        model.transcribe.side_effect = RuntimeError("Mocked model error")
        service = ASRService(Settings())
        with patch.object(service, "_create_model", return_value=model):
            with TestClient(create_app(service=service)) as client:
                for language in TEXT:
                    with self.subTest(language=language), client.websocket_connect("/ws/transcribe") as ws:
                        ws.send_json(START | {"language": language})
                        self.assertEqual(ws.receive_json()["type"], "ready")
                        ws.send_bytes(bytes(16000))
                        with self.assertLogs("backend.asr_service", level="ERROR"):
                            ws.send_json({"type": "end"})
                            self.assertEqual(ws.receive_json()["code"], "inference_failed")
                    self.assertEqual(client.get("/health").status_code, 200)


class SampleTests(unittest.TestCase):
    def test_missing_and_invalid_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = {"language": "Chinese", "audio": "test.wav", "reference": "test.txt", "ui_label": "Mandarin Chinese"}
            self.assertEqual(inspect_sample(entry, root)["format_status"], "NOT TESTED")
            for rate, channels, subtype in [(44100, 1, "PCM_16"), (16000, 2, "PCM_16"), (16000, 1, "PCM_24")]:
                sf.write(root / "test.wav", np.zeros((1000, channels)), rate, subtype=subtype)
                self.assertEqual(inspect_sample(entry, root)["format_status"], "FAIL")

    def test_valid_duration_and_utf8_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sf.write(root / "test.wav", np.zeros(56000), 16000, subtype="PCM_16")
            (root / "test.txt").write_text("你好世界", encoding="utf-8")
            entry = {"language": "Chinese", "audio": "test.wav", "reference": "test.txt", "reference_verified": True}
            row = inspect_sample(entry, root)
            self.assertEqual(row["duration_seconds"], 3.5)
            self.assertEqual(row["reference_text"], "你好世界")
            self.assertEqual(row["reference_status"], "VERIFIED BY USER")
            self.assertEqual(len(row["audio_sha256"]), 64)

    def test_invalid_reference_encoding_is_not_claimed_available(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bad.txt").write_bytes(b"\xff\xfe\xff")
            row = inspect_sample({"language": "English", "audio": "missing.wav", "reference": "bad.txt"}, root)
            self.assertFalse(row["reference_available"])
            self.assertTrue(row["errors"])


class EventAndReferenceTests(unittest.TestCase):
    def events(self):
        return [
            {"type": "segment_transcript", "stream_id": "test", "segment_index": 0, "language": "Chinese", "text": "你好", "audio_start_seconds": 0, "audio_end_seconds": 3},
            {"type": "segment_transcript", "stream_id": "test", "segment_index": 1, "language": "Chinese", "text": "世界", "audio_start_seconds": 3, "audio_end_seconds": 3.5},
            {"type": "final_transcript", "stream_id": "test", "language": "Chinese", "text": "你好世界"},
            {"type": "metrics", "stream_id": "test", "total_audio_duration_seconds": 3.5},
            {"type": "completed", "stream_id": "test"},
        ]

    def test_chinese_final_has_no_added_spaces(self):
        self.assertEqual(check_events(self.events(), "Chinese", 3.5)["actual_transcript"], "你好世界")

    def test_rejects_duplicate_and_out_of_order_indexes(self):
        for index in (0, 2):
            events = copy.deepcopy(self.events())
            events[1]["segment_index"] = index
            with self.assertRaisesRegex(ValueError, "indexes"):
                check_events(events, "Chinese", 3.5)

    def test_rejects_duplicate_final_text_and_wrong_language(self):
        events = self.events()
        events[2]["text"] = "你好你好世界"
        with self.assertRaisesRegex(ValueError, "exactly once"):
            check_events(events, "Chinese", 3.5)
        with self.assertRaisesRegex(ValueError, "language"):
            check_events(self.events(), "English", 3.5)

    def test_rejects_missing_residual_and_error_events(self):
        with self.assertRaisesRegex(ValueError, "residual"):
            check_events(self.events(), "Chinese", 4)
        with self.assertRaisesRegex(ValueError, "error"):
            check_events(self.events() + [{"type": "error"}], "Chinese", 3.5)

    def test_reference_comparison_keeps_quality_separate_from_streaming(self):
        row = {"reference_available": True, "reference_verified": True, "reference_text": "Hello, world!"}
        self.assertEqual(compare_reference("Hello, world!", row)["reference_comparison"], "EXACT MATCH")
        self.assertEqual(compare_reference("hello world", row)["reference_comparison"], "MATCH AFTER NORMALIZATION")
        self.assertEqual(compare_reference("Hello word", row)["reference_comparison"], "DIFFERENCES FOUND")
        row["reference_verified"] = False
        self.assertEqual(compare_reference("Hello, world!", row)["reference_comparison"], "NOT TESTED")

    def test_word_differences_fail_overall_even_if_streaming_completed(self):
        result = classify_completed({"reference_comparison": "DIFFERENCES FOUND"})
        self.assertEqual(result, {"streaming_result": "PASS", "reference_result": "FAIL", "result": "FAIL"})
        self.assertEqual(classify_completed({"reference_comparison": "EXACT MATCH"})["result"], "PASS")


if __name__ == "__main__":
    unittest.main(verbosity=2)
