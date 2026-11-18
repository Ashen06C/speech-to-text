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


async def stream_file(path: Path, language: str, chunk_ms: int, url: str, *,
                      observation: dict | None = None, verbose: bool = True,
                      start_event: asyncio.Event | None = None) -> dict:
    """Optional observation retains events/timestamps even if a stream fails."""
    def emit(*args, **kwargs):
        if verbose:
            print(*args, **kwargs)

    if not 1 <= chunk_ms <= 1000:
        raise ValueError("Chunk duration must be between 1 and 1000 ms.")
    # Reuse complete WAV validation, then read original raw frames below.
    with path.open("rb") as source:
        audio = decode_wav_upload(UploadFile(source, size=path.stat().st_size), Settings())
        total_samples = audio.samples.size
    del audio
    samples_sent = 0
    first_segment_arrival = None
    progressive_update = False
    sending_finished_at = None
    events, arrivals, timing = [], [], {}
    observation = observation if observation is not None else {}
    observation.update(events=events, event_arrivals_seconds=arrivals, timing=timing)
    observation.update(connection_success=False, bytes_sent=0, chunks_sent=0)
    emit(f"Connecting to {url}...", flush=True)

    if start_event is not None:
        await start_event.wait()
    timing["connection_attempt_at"] = perf_counter()
    async with connect(url, proxy=None, max_size=1024 * 1024, max_queue=8) as websocket:
        observation["connection_success"] = True
        emit("Connected successfully.", flush=True)
        await websocket.send(json.dumps({
            "type": "start", "language": language, "sample_rate": 16000,
            "channels": 1, "bit_depth": 16, "chunk_duration_ms": chunk_ms,
        }))
        ready = json.loads(await asyncio.wait_for(websocket.recv(), 15))
        observation["start_response"] = ready
        if ready.get("type") != "ready":
            raise RuntimeError(f"Server rejected start: {ready}")
        observation["ready"] = ready
        emit(f"Streaming started: {language}; stream_id={ready['stream_id']}", flush=True)
        started_at = perf_counter()
        timing["stream_started_at"] = started_at

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
                    timing["last_audio_sent_at"] = perf_counter()
                    samples_sent += len(pcm) // 2
                    chunk_index += 1
                    observation.update(bytes_sent=samples_sent * 2, chunks_sent=chunk_index)
                    emit(f"Sending audio chunk {chunk_index}: {samples_sent / 16000:.3f}s sent", flush=True)
            sending_finished_at = perf_counter()
            timing["end_sent_at"] = perf_counter()  # Immediately before initiating the end send.
            await websocket.send(json.dumps({"type": "end"}))
            emit("Audio transmission completed. Waiting for remaining inference...", flush=True)

        async def receive_events():
            nonlocal first_segment_arrival, progressive_update
            async for message in websocket:
                received_at = perf_counter()
                event = json.loads(message)
                events.append(event)
                arrivals.append(received_at - started_at)
                kind = event.get("type")
                if kind == "segment_transcript":
                    if event["text"].strip():
                        if first_segment_arrival is None:
                            first_segment_arrival = received_at
                            timing["first_transcript_at"] = received_at
                        if samples_sent < total_samples:
                            progressive_update = True
                    emit(f"\n[Transcript Segment {event['segment_index']}]\n{event['text']}", flush=True)
                    emit(f"Inference: {event['inference_time_seconds']:.3f}s; "
                          f"queue wait: {event['queue_wait_time_seconds']:.3f}s", flush=True)
                elif kind == "final_transcript":
                    timing["final_transcript_at"] = received_at
                    if event["text"].strip() and first_segment_arrival is None:
                        first_segment_arrival = received_at
                        timing["first_transcript_at"] = received_at
                    emit(f"\nFinal Transcript:\n{event['text']}", flush=True)
                elif kind == "metrics":
                    emit("\nMetrics:\n" + json.dumps(event, indent=2, ensure_ascii=False), flush=True)
                elif kind == "completed":
                    kinds = [item["type"] for item in events]
                    if "final_transcript" not in kinds or "metrics" not in kinds:
                        raise RuntimeError("Server completed without final transcript and metrics.")
                    timing["completed_at"] = received_at
                    emit("\nStreaming completed successfully.", flush=True)
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
                emit("Cancellation requested; closing the connection.", flush=True)
            except ConnectionClosed:
                pass
            raise
        finally:
            for task in (sender, receiver):
                if not task.done():
                    task.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)

    emit(f"Transcript received before all audio was sent: {progressive_update}", flush=True)
    if first_segment_arrival is not None:
        emit(f"Client time to first transcript: {first_segment_arrival - started_at:.3f}s", flush=True)
        if not progressive_update and sending_finished_at is not None:
            emit(f"First transcript delay after sending audio: "
                  f"{first_segment_arrival - sending_finished_at:.3f}s", flush=True)
    observation["progressive_update_before_upload_complete"] = progressive_update
    return observation


def main() -> int:
    # Windows redirected stdout can default to cp1252, which cannot print Chinese.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
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
