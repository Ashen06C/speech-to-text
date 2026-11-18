"""Validate supplied WAVs/references without loading a model or changing audio."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import soundfile as sf
from fastapi import UploadFile

from backend.audio_service import decode_wav_upload
from backend.config import Language, Settings

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "samples" / "multilingual_manifest.json"


def file_hash(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def load_manifest(path: Path = DEFAULT_MANIFEST) -> list[dict]:
    entries = json.loads(path.read_text(encoding="utf-8-sig"))["samples"]
    languages = [Language(entry["language"]).value for entry in entries]
    if len(languages) != len(set(languages)) or set(languages) != {lang.value for lang in Language}:
        raise ValueError("Manifest must contain exactly one entry for English, Chinese, and Indonesian.")
    for entry in entries:
        if not all(isinstance(entry.get(key), str) and entry[key] for key in ("audio", "reference", "ui_label")):
            raise ValueError("Each manifest entry needs audio, reference, and ui_label strings.")
    return entries


def inspect_sample(entry: dict, root: Path = ROOT) -> dict:
    audio_path, reference_path = root / entry["audio"], root / entry["reference"]
    row = {**entry, "format_status": "NOT TESTED", "duration_seconds": None,
           "reference_available": False, "reference_text": None,
           "reference_status": "NOT TESTED", "errors": []}
    if reference_path.is_file():
        try:
            text = reference_path.read_text(encoding="utf-8-sig").strip()
            if not text:
                raise ValueError("Reference transcript is empty.")
            row.update(reference_available=True, reference_text=text,
                       reference_sha256=file_hash(reference_path),
                       reference_status="VERIFIED BY USER" if entry.get("reference_verified") is True else "UNVERIFIED")
        except (UnicodeError, ValueError, OSError) as exc:
            row["errors"].append(f"Reference: {exc}")
    if not audio_path.is_file():
        row["errors"].append("Audio recording is missing.")
        return row
    try:
        info = sf.info(str(audio_path))
        row.update(sample_rate=info.samplerate, channels=info.channels, subtype=info.subtype, container=info.format)
        with audio_path.open("rb") as source:
            decoded = decode_wav_upload(UploadFile(source, size=audio_path.stat().st_size), Settings())
        row.update(format_status="PASS", duration_seconds=decoded.duration_seconds,
                   audio_sha256=file_hash(audio_path))
    except (ValueError, OSError, sf.LibsndfileError) as exc:
        row.update(format_status="FAIL")
        row["errors"].append(str(exc))
    return row


def inspect_samples(manifest: Path = DEFAULT_MANIFEST, root: Path = ROOT) -> list[dict]:
    return [inspect_sample(entry, root) for entry in load_manifest(manifest)]


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--require-all", action="store_true")
    args = parser.parse_args()
    rows = inspect_samples(args.manifest)
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    else:
        for row in rows:
            print(f"{row['language']}: {row['format_status']} - {row['audio']}")
            if row["duration_seconds"] is not None:
                print(f"  {row['duration_seconds']:.6f}s; {row['sample_rate']} Hz; {row['channels']} channel; {row['subtype']}")
            print(f"  Reference: {row['reference_status']} ({row['reference']})")
            for error in row["errors"]:
                print(f"  {error}")
    return int(any(row["format_status"] == "FAIL" or
                   (args.require_all and row["format_status"] != "PASS") for row in rows))


if __name__ == "__main__":
    raise SystemExit(main())
