from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from app.config import Settings
from app.pipeline.errors import PipelineError

logger = logging.getLogger(__name__)


def extract_audio_wav(media_path: Path, audio_path: Path) -> Path:
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(media_path),
        "-ar",
        "16000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(audio_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise PipelineError(
            "EXTRACT_AUDIO_FAILED",
            proc.stderr[-2000:] if proc.stderr else "ffmpeg failed",
        )
    return audio_path


def probe_duration_sec(media_path: Path) -> float | None:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(media_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return None


def _scene_list(media_path: Path, settings: Settings) -> list[tuple[float, float]]:
    from scenedetect import SceneManager, open_video
    from scenedetect.detectors import AdaptiveDetector, ContentDetector

    video = open_video(str(media_path))
    manager = SceneManager()
    if settings.scene_detector == "content":
        manager.add_detector(ContentDetector(threshold=settings.content_threshold))
    else:
        manager.add_detector(AdaptiveDetector())
    manager.detect_scenes(video)
    scenes = manager.get_scene_list()
    result: list[tuple[float, float]] = []
    for start, end in scenes:
        result.append((start.get_seconds(), end.get_seconds()))
    if not result:
        duration = probe_duration_sec(media_path) or 0.0
        result = [(0.0, duration)]
    return result


def _expand_long_scenes(
    scenes: list[tuple[float, float]], max_len: float = 60.0, step: float = 30.0
) -> list[tuple[float, float, float]]:
    """Return list of (scene_start, scene_end, sample_time)."""
    samples: list[tuple[float, float, float]] = []
    for start, end in scenes:
        length = end - start
        if length <= max_len:
            mid = (start + end) / 2.0
            samples.append((start, end, mid))
        else:
            t = start
            while t < end:
                sample = min(t + step / 2.0, end - 0.01)
                samples.append((start, end, max(start, sample)))
                t += step
    return samples


def extract_frames(
    media_path: Path, frames_dir: Path, settings: Settings
) -> list[dict[str, Any]]:
    frames_dir.mkdir(parents=True, exist_ok=True)
    scenes = _scene_list(media_path, settings)
    samples = _expand_long_scenes(scenes)
    index: list[dict[str, Any]] = []

    for scene_start, scene_end, sample_t in samples:
        ms = int(round(sample_t * 1000))
        out_path = frames_dir / f"{ms}.jpg"
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{sample_t:.3f}",
            "-i",
            str(media_path),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(out_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0 or not out_path.exists():
            logger.warning("Failed to extract frame at %s", sample_t)
            continue
        index.append(
            {
                "start": scene_start,
                "end": scene_end,
                "timestamp": sample_t,
                "timestamp_ms": ms,
                "path": str(out_path.name),
            }
        )

    (frames_dir.parent / "frames_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return index


def run_extract(
    *,
    work_dir: Path,
    media_path: Path,
    media_type: str,
    settings: Settings,
) -> dict[str, Any]:
    audio_path = work_dir / "audio.wav"
    extract_audio_wav(media_path, audio_path)
    duration = probe_duration_sec(media_path)
    frames_index: list[dict[str, Any]] = []
    if media_type == "video":
        frames_index = extract_frames(media_path, work_dir / "frames", settings)
    return {
        "audio_path": str(audio_path),
        "duration_sec": duration,
        "frames_index": frames_index,
    }
