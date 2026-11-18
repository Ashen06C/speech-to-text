
import time
import torch
from qwen_asr import Qwen3ASRModel

# CPU-only configuration
torch.set_num_threads(6)

print("Loading Qwen3-ASR model...")

model = Qwen3ASRModel.from_pretrained(
    "Qwen/Qwen3-ASR-0.6B",
    dtype=torch.float32,
    device_map="cpu",
    max_inference_batch_size=1,
    max_new_tokens=256,
)

print("Model loaded successfully!")

# Place an English WAV recording in the samples folder.
start = time.perf_counter()

results = model.transcribe(
    audio="samples/english.wav",
    language="English",
)

elapsed = time.perf_counter() - start

print("Language:", results[0].language)
print("Transcript:", results[0].text)
print(f"Inference time: {elapsed:.2f} seconds")
