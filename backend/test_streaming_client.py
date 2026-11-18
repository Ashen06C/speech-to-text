"""Paced WAV-to-PCM WebSocket client. Run with python -m backend.test_streaming_client."""

import argparse
import asyncio
import json
import sys
import wave
from pathlib import Path
from time import perf_counter

from fastapi import UploadFile
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from backend.audio_service import decode_wav_upload
from backend.config import Language, Settings


async def stream_file(path: Path, language: str, chunk_ms: int, url: str) -> dict:
    if not 1 <= chunk_ms <= 1000:
        raise ValueError("Chunk duration must be between 1 and 1000 ms.")
    # Reuse Step 2's complete WAV validation, then read original raw frames below.
    with path.open("rb") as source:
        audio = decode_wav_upload(UploadFile(source, size=path.stat().st_size), Settings())
        total_samples = audio.samples.size
    del audio
    samples_sent = 0
    first_segment_arrival = None
    progressive_update = False
    sending_finished_at = None
    events = []
    print(f"Connecting to {url}...", flush=True)

    async with connect(url, proxy=None, max_size=1024 * 1024, max_queue=8) as websocket:
        print("Connected successfully.", flush=True)
        await websocket.send(json.dumps({
            "type": "start", "language": language, "sample_rate": 16000,
            "channels": 1, "bit_depth": 16, "chunk_duration_ms": chunk_ms,
        }))
        ready = json.loads(await asyncio.wait_for(websocket.recv(), 15))
        if ready.get("type") != "ready":
            raise RuntimeError(f"Server rejected start: {ready}")
        print(f"Streaming started: {language}; stream_id={ready['stream_id']}", flush=True)
        started_at = perf_counter()

        async def send_audio():
            nonlocal samples_sent, sending_finished_at
            chunk_samples = 16000 * chunk_ms // 1000
            with wave.open(str(path), "rb") as wav:
                chunk_index = 0
                while pcm := wav.readframes(chunk_samples):
                    # Wait until this chunk's audio duration has elapsed, like a
                    # microphone collecting samples. Avoids sending audio early.
                    target = started_at + (samples_sent + len(pcm) // 2) / 16000
                    await asyncio.sleep(max(0.0, target - perf_counter()))
                    await websocket.send(pcm)
                    samples_sent += len(pcm) // 2
                    chunk_index += 1
                    print(f"Sending audio chunk {chunk_index}: {samples_sent / 16000:.3f}s sent", flush=True)
            sending_finished_at = perf_counter()
            await websocket.send(json.dumps({"type": "end"}))
            print("Audio transmission completed. Waiting for remaining inference...", flush=True)

        async def receive_events():
            nonlocal first_segment_arrival, progressive_update
            async for message in websocket:
                event = json.loads(message)
                events.append(event)
                kind = event.get("type")
                if kind == "segment_transcript":
                    if first_segment_arrival is None:
                        first_segment_arrival = perf_counter()
                    if samples_sent < total_samples:
                        progressive_update = True
                    print(f"\n[Transcript Segment {event['segment_index']}]\n{event['text']}", flush=True)
                    print(f"Inference: {event['inference_time_seconds']:.3f}s; "
                          f"queue wait: {event['queue_wait_time_seconds']:.3f}s", flush=True)
                elif kind == "final_transcript":
                    print(f"\nFinal Transcript:\n{event['text']}", flush=True)
                elif kind == "metrics":
                    print("\nMetrics:\n" + json.dumps(event, indent=2, ensure_ascii=False), flush=True)
                elif kind == "completed":
                    kinds = [item["type"] for item in events]
                    if "final_transcript" not in kinds or "metrics" not in kinds:
                        raise RuntimeError("Server completed without final transcript and metrics.")
                    print("\nStreaming completed successfully.", flush=True)
                    return
                elif kind in {"error", "cancelled"}:
                    raise RuntimeError(f"Session ended without completion: {event}")
                else:
                    raise RuntimeError(f"Unexpected server event: {event}")
            raise RuntimeError("Connection closed before completed.")

        sender = asyncio.create_task(send_audio())
        receiver = asyncio.create_task(receive_events())
        try:
            # If either task fails, stop the other immediately. Completion can
            # only arrive after sender has sent end; don't wait for a closed peer.
            done, _ = await asyncio.wait([sender, receiver], return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            await asyncio.gather(sender, receiver)
        except (asyncio.CancelledError, KeyboardInterrupt):
            try:
                await websocket.send(json.dumps({"type": "stop"}))
                print("Cancellation requested; closing the connection.", flush=True)
            except ConnectionClosed:
                pass
            raise
        finally:
            for task in (sender, receiver):
                if not task.done():
                    task.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)

    print(f"Transcript received before all audio was sent: {progressive_update}", flush=True)
    if first_segment_arrival is not None:
        print(f"Client time to first transcript: {first_segment_arrival - started_at:.3f}s", flush=True)
        if not progressive_update and sending_finished_at is not None:
            print(f"First transcript delay after sending audio: "
                  f"{first_segment_arrival - sending_finished_at:.3f}s", flush=True)
    return {"events": events, "progressive_update_before_upload_complete": progressive_update}


def main() -> int:
    parser = argparse.ArgumentParser(description="Send WAV audio progressively as paced raw PCM chunks.")
    parser.add_argument("--file", type=Path,
                        default=Path(__file__).resolve().parents[1] / "samples" / "english.wav")
    parser.add_argument("--language", choices=[language.value for language in Language], default="English")
    parser.add_argument("--chunk-duration-ms", type=int, default=500)
    parser.add_argument("--url", default="ws://127.0.0.1:8000/ws/transcribe")
    args = parser.parse_args()
    try:
        asyncio.run(stream_file(args.file, args.language, args.chunk_duration_ms, args.url))
    except KeyboardInterrupt:
        print("Streaming cancelled by user.")
        return 130
    except Exception as exc:
        print(f"Streaming failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
