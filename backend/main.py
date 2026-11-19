"""FastAPI REST and progressive WebSocket endpoints. Use one Uvicorn worker."""

import logging
import os
import platform
import sys
from contextlib import asynccontextmanager
from typing import Annotated

import psutil
from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile, WebSocket
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from backend.asr_service import ASRService, InferenceError, ModelNotLoadedError
from backend.audio_service import AudioValidationError, decode_wav_upload
from backend.config import Language, Settings
from backend.streaming_service import StreamingService
from backend.system_metrics import CPUSampler

logger = logging.getLogger(__name__)


class TranscribeResponse(BaseModel):
    success: bool
    language: Language
    transcript: str
    audio_duration_seconds: float
    inference_time_seconds: float
    device: str


def create_app(settings: Settings | None = None, service: ASRService | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()
    service = service if service is not None else ASRService(settings)
    streaming = StreamingService(service, settings)
    cpu_sampler = CPUSampler()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            await run_in_threadpool(service.load_model)
        except Exception:
            # Keep diagnostics available; transcription returns 503 until restart.
            logger.exception("ASR startup failed; check the existing model cache and dependencies")
        cpu_sampler.start()
        try:
            yield
        finally:
            await run_in_threadpool(cpu_sampler.stop)
            await streaming.shutdown()
            await run_in_threadpool(service.unload_model)

    app = FastAPI(
        title="Speech-to-Text API",
        description="WAV file and progressive PCM window transcription using Qwen3-ASR on CPU.",
        version="0.2.0",
        lifespan=lifespan,
    )
    app.state.asr_service = service
    app.state.streaming_service = streaming
    app.state.cpu_sampler = cpu_sampler

    @app.websocket("/ws/transcribe")
    async def stream_transcribe(websocket: WebSocket):
        await streaming.handle(websocket)

    @app.get("/health")
    def health(response: Response):
        ready = service.model_loaded
        response.status_code = 200 if ready else 503
        return {
            "status": "healthy" if ready else "unhealthy",
            "model": settings.model_name,
            "device": settings.device,
            "model_loaded": ready,
        }

    @app.get("/api/system")
    def system():
        ram = psutil.virtual_memory().total
        process_ram = psutil.Process().memory_info().rss
        torch = sys.modules.get("torch")
        return {
            "cpu_logical_processors": psutil.cpu_count(logical=True),
            **cpu_sampler.snapshot(),
            "cpu_physical_cores": psutil.cpu_count(logical=False),
            "total_ram_bytes": ram,
            "total_ram_gb": round(ram / (1024**3), 2),
            "python_version": platform.python_version(),
            "process_ram_bytes": process_ram,
            "process_ram_mb": round(process_ram / (1024**2), 2),
            "model": settings.model_name,
            "device": settings.device,
            # Additive diagnostics; existing clients can keep using their fields.
            "process_id": os.getpid(),
            "available_ram_bytes": psutil.virtual_memory().available,
            "operating_system": platform.platform(),
            "configured_cpu_threads": settings.cpu_threads,
            "torch_num_threads": torch.get_num_threads() if torch is not None else None,
            "torch_version": str(torch.__version__) if torch is not None else None,
            "window_ms": settings.window_ms,
            "model_load_time_seconds": getattr(service, "model_load_time_seconds", None),
        }

    @app.post(
        "/api/transcribe",
        response_model=TranscribeResponse,
        responses={
            400: {"description": "Invalid WAV or unsupported audio encoding"},
            413: {"description": "Upload exceeds configured file size limit"},
            422: {"description": "Missing file/language or unsupported language"},
            500: {"description": "Model inference failed"},
            503: {"description": "ASR model not loaded"},
        },
    )
    def transcribe(
        file: Annotated[UploadFile, File(description="16 kHz mono 16-bit Linear PCM WAV")],
        language: Annotated[Language, Form(description="English, Chinese, or Indonesian")],
    ):
        # FastAPI runs this regular def route in its worker threadpool, so file
        # decoding, lock waiting, and CPU inference do not block the event loop.
        try:
            if not service.model_loaded:
                raise ModelNotLoadedError("ASR model is not loaded. Check the server startup log.")
            audio = decode_wav_upload(file, settings)
            result = service.transcribe(audio.samples, language)
            return TranscribeResponse(
                success=True,
                language=language,
                transcript=result.text,
                audio_duration_seconds=audio.duration_seconds,
                inference_time_seconds=result.inference_time_seconds,
                device=settings.device,
            )
        except AudioValidationError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        except ModelNotLoadedError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except InferenceError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        finally:
            # Also removes any temporary multipart spool created by Starlette.
            file.file.close()

    return app


app = create_app()
