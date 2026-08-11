from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from app.config import Settings
from app.pipeline.errors import AsrFailedError
from app.pipeline.gpu_lock import ensure_llama_server_stopped_for_asr, gpu_lock
from app.pipeline.youtube import parse_srt_to_segments

logger = logging.getLogger(__name__)


def _find_asr_output(output_dir: Path, stem: str) -> Path | None:
    for ext in (".srt", ".json", ".txt"):
        candidate = output_dir / f"{stem}{ext}"
        if candidate.exists():
            return candidate
    # whisperx may write into output/ subfolder
    for path in output_dir.rglob(f"{stem}.srt"):
        return path
    return None


async def run_whisperx(
    *,
    settings: Settings,
    audio_path: Path,
    work_dir: Path,
    topic: str = "",
    hotwords_file: Path | None = None,
    diarize: bool | None = None,
) -> dict[str, Any]:
    await ensure_llama_server_stopped_for_asr(settings)

    out_dir = work_dir / "asr_out"
    out_dir.mkdir(parents=True, exist_ok=True)

    use_diarize = settings.enable_diarization if diarize is None else diarize
    cmd = [
        settings.whisperx_python,
        settings.whisperx_script,
        str(audio_path),
        "--lang",
        "zh",
        "--format",
        "all" if use_diarize else "srt",
        "--output-dir",
        str(out_dir),
    ]
    if topic:
        cmd.extend(["--topic", topic])
    if use_diarize:
        cmd.append("--diarize")
    hw = hotwords_file or Path(settings.asr_hotwords_file)
    if hw.exists():
        cmd.extend(["--hotwords-file", str(hw)])
    corr = Path(settings.asr_corrections_file)
    if corr.exists():
        cmd.extend(["--corrections-file", str(corr)])

    async with gpu_lock.acquire("whisperx"):
        logger.info("Running WhisperX: %s", " ".join(cmd))
        proc = await _run_subprocess(cmd)

    if proc.returncode != 0:
        raise AsrFailedError(
            (proc.stderr or proc.stdout or "whisperx failed")[-3000:]
        )

    stem = audio_path.stem
    # Prefer JSON when diarization enabled (may include speaker labels)
    json_path = None
    for path in list(out_dir.rglob(f"{stem}.json")) + list(
        Path("/home/kino/asr/output").rglob(f"{stem}.json")
    ):
        json_path = path
        break

    segments: list[dict[str, Any]]
    raw_srt = ""
    if json_path and json_path.exists():
        data = json.loads(json_path.read_text(encoding="utf-8", errors="replace"))
        raw_segments = data.get("segments") or data
        segments = []
        for s in raw_segments:
            if not isinstance(s, dict):
                continue
            speaker = s.get("speaker") or s.get("speaker_id")
            text = s.get("text") or ""
            if speaker and not str(text).startswith(f"[{speaker}]"):
                text = f"[{speaker}] {text}".strip()
            segments.append(
                {
                    "start": float(s.get("start") or 0),
                    "end": float(s.get("end") or 0),
                    "text": text,
                    "speaker": speaker,
                }
            )
    else:
        srt_path = _find_asr_output(out_dir, stem)
        if not srt_path:
            default_out = Path("/home/kino/asr/output")
            srt_path = _find_asr_output(default_out, stem)
        if not srt_path or not srt_path.exists():
            raise AsrFailedError("找不到 WhisperX 輸出的 SRT")
        raw_srt = srt_path.read_text(encoding="utf-8", errors="replace")
        if srt_path.suffix.lower() == ".json":
            data = json.loads(raw_srt)
            segments = data.get("segments") or data
        else:
            segments = parse_srt_to_segments(raw_srt)

    transcript = {
        "source": "whisperx",
        "language": "zh",
        "diarization": bool(use_diarize),
        "segments": segments,
    }
    dest = work_dir / "transcript.raw.json"
    dest.write_text(
        json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # Keep a copy of srt when available
    if raw_srt:
        (work_dir / "transcript.raw.srt").write_text(raw_srt, encoding="utf-8")
    elif json_path and json_path.exists():
        # also copy companion srt if present
        srt_sibling = json_path.with_suffix(".srt")
        if srt_sibling.exists():
            (work_dir / "transcript.raw.srt").write_text(
                srt_sibling.read_text(encoding="utf-8", errors="replace"),
                encoding="utf-8",
            )
    return transcript


async def _run_subprocess(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    import asyncio

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return subprocess.CompletedProcess(
        args=cmd,
        returncode=proc.returncode or 0,
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
    )


def load_transcript(work_dir: Path) -> dict[str, Any] | None:
    path = work_dir / "transcript.raw.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
