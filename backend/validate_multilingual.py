"""Sequential functional tests against the existing Qwen CPU WebSocket backend."""

import argparse
import asyncio
import json
import math
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from backend.test_streaming_client import stream_file
from backend.validate_samples import DEFAULT_MANIFEST, ROOT, inspect_samples


def check_events(events: list[dict], language: str, duration: float) -> dict:
    """Check structural duplication/order; ASR word repetition is a quality issue."""
    if any(event.get("type") in {"error", "cancelled"} for event in events):
        raise ValueError("Server returned error/cancelled.")
    segments = [event for event in events if event.get("type") == "segment_transcript"]
    finals = [event for event in events if event.get("type") == "final_transcript"]
    metrics = [event for event in events if event.get("type") == "metrics"]
    completed = [event for event in events if event.get("type") == "completed"]
    if not segments or len(finals) != 1 or len(metrics) != 1 or len(completed) != 1:
        raise ValueError("Expected segments and exactly one final, metrics, and completed event.")
    if [event["type"] for event in events[-3:]] != ["final_transcript", "metrics", "completed"]:
        raise ValueError("Invalid final event order.")
    if [segment["segment_index"] for segment in segments] != list(range(len(segments))):
        raise ValueError("Segment indexes are duplicated, missing, or out of order.")
    stream_id = finals[0]["stream_id"]
    if not stream_id or any(event.get("stream_id") != stream_id for event in events):
        raise ValueError("Responses have inconsistent stream IDs.")
    if finals[0]["language"] != language or any(segment["language"] != language for segment in segments):
        raise ValueError("Response language differs from selected language.")
    previous_end = 0.0
    for segment in segments:
        start, end = segment["audio_start_seconds"], segment["audio_end_seconds"]
        if not math.isclose(start, previous_end, abs_tol=1 / 16000) or not end > start:
            raise ValueError("Audio windows overlap or leave a gap.")
        previous_end = end
    if not math.isclose(previous_end, duration, abs_tol=1 / 16000):
        raise ValueError("Final residual window does not cover the whole recording.")
    if not math.isclose(metrics[0]["total_audio_duration_seconds"], duration, abs_tol=1 / 16000):
        raise ValueError("Server audio duration differs from WAV duration.")
    separator = "" if language == "Chinese" else " "
    assembled = separator.join(segment["text"].strip() for segment in segments if segment["text"].strip())
    if finals[0]["text"] != assembled:
        raise ValueError("Final text does not join each segment exactly once.")
    if not assembled:
        raise ValueError("No transcription returned for a speech recording.")
    if language == "Chinese" and not any("\u3400" <= char <= "\u9fff" for char in assembled):
        raise ValueError("Chinese transcription contains no Han characters; review required.")
    return {"actual_transcript": assembled, "segments": segments, "segment_count": len(segments),
            "stream_id": stream_id, "metrics": metrics[0], "ordering": "PASS", "final_assembly": "PASS"}


def normalize_text(text: str) -> str:
    """Qualitative equality check only"""
    return "".join(char for char in unicodedata.normalize("NFKC", text).casefold()
                   if not char.isspace() and not unicodedata.category(char).startswith("P"))


def compare_reference(actual: str, row: dict) -> dict:
    if not row.get("reference_available") or row.get("reference_verified") is not True:
        return {"reference_comparison": "NOT TESTED", "reference_note": "A verified reference is unavailable."}
    reference = row["reference_text"]
    if actual == reference:
        return {"reference_comparison": "EXACT MATCH", "reference_note": "Text equals the user-verified reference."}
    if normalize_text(actual) == normalize_text(reference):
        return {"reference_comparison": "MATCH AFTER NORMALIZATION",
                "reference_note": "Only case, punctuation, Unicode compatibility, or whitespace differs."}
    return {"reference_comparison": "DIFFERENCES FOUND",
            "reference_note": "Manual review required; reference and observed text differ. No accuracy score calculated."}


def classify_completed(row: dict) -> dict:
    """A working connection must not hide a failed verified-text comparison."""
    comparison = row["reference_comparison"]
    reference_result = "NOT TESTED" if comparison == "NOT TESTED" else (
        "FAIL" if comparison == "DIFFERENCES FOUND" else "PASS")
    return {"streaming_result": "PASS", "reference_result": reference_result,
            "result": "FAIL" if reference_result == "FAIL" else "PASS"}


async def validate(manifest: Path, url: str, api_base: str) -> dict:
    rows = inspect_samples(manifest)
    backend_error = None
    health = None
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            response = await client.get(f"{api_base.rstrip('/')}/health")
            response.raise_for_status()
            health = response.json()
            if health.get("device") != "cpu" or health.get("model") != "Qwen3-ASR-0.6B" or health.get("model_loaded") is not True:
                raise ValueError("Expected the existing loaded Qwen3-ASR-0.6B CPU backend.")
    except Exception as exc:
        backend_error = str(exc)
    for row in rows:
        row.update(result="NOT TESTED", streaming_status="NOT TESTED", actual_transcript=None,
                   reference_comparison="NOT TESTED", streaming_result="NOT TESTED", reference_result="NOT TESTED")
        if row["format_status"] != "PASS":
            if row["format_status"] == "FAIL":
                row["result"] = "FAIL"
            continue
        try:
            if backend_error:
                raise ValueError(backend_error)
            # All languages use the same already-running server; never restart/load a model here.
            output = await stream_file(ROOT / row["audio"], row["language"], 500, url)
            row.update(check_events(output["events"], row["language"], row["duration_seconds"]))
            row.update(compare_reference(row["actual_transcript"], row))
            row.update(classify_completed(row))
            row.update(streaming_status="completed",
                       progressive_update_before_upload_complete=output["progressive_update_before_upload_complete"])
        except Exception as exc:
            row.update(result="FAIL", streaming_status="failed", streaming_result="FAIL")
            row["errors"].append(str(exc))
    return {"recorded_at": datetime.now(timezone(timedelta(hours=5, minutes=30))).isoformat(),
            "backend_url": url, "api_base_url": api_base, "backend_health": health,
            "manifest": str(manifest), "chunk_duration_ms": 500,
            "result_scope": "Overall FAIL for streaming errors or verified-reference word differences; streaming_result is separate. No accuracy scores.",
            "results": rows}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--url", default="ws://127.0.0.1:8000/ws/transcribe")
    parser.add_argument("--api-base-url", help="Use http://127.0.0.1:5173/backend for Vite proxy testing.")
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "multilingual_observations.json")
    args = parser.parse_args()
    address = urlsplit(args.url)
    api_base = args.api_base_url or f"{'https' if address.scheme == 'wss' else 'http'}://{address.netloc}"
    result = asyncio.run(validate(args.manifest, args.url, api_base))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for row in result["results"]:
        print(f"{row['language']}: {row['result']}; streaming: {row['streaming_result']}; reference: {row['reference_comparison']}")
    print(f"Actual observations saved to {args.output}")
    return int(any(row["result"] == "FAIL" for row in result["results"]))


if __name__ == "__main__":
    raise SystemExit(main())
