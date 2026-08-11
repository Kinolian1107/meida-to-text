from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
from pathlib import Path

from app.config import Settings
from app.pipeline.gpu_lock import llama_server_is_up

logger = logging.getLogger(__name__)


def _pid_file(settings: Settings) -> Path:
    return settings.data_dir / "sqlite" / "llama-server.pid"


async def stop_llama_server(settings: Settings) -> bool:
    """Stop managed llama-server if we own the pid file. Returns True if stopped/absent."""
    pid_path = _pid_file(settings)
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
            os.kill(pid, signal.SIGTERM)
            for _ in range(30):
                try:
                    os.kill(pid, 0)
                except OSError:
                    break
                await asyncio.sleep(0.2)
            else:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
            logger.info("Stopped llama-server pid=%s", pid)
        except (ValueError, ProcessLookupError, PermissionError) as exc:
            logger.warning("Could not stop llama-server via pid file: %s", exc)
        finally:
            pid_path.unlink(missing_ok=True)

    # If still up (externally started), try fuser/pkill by port as last resort
    if await llama_server_is_up(settings) and settings.llama_auto_manage:
        port = settings.llama_server_port
        try:
            proc = await asyncio.create_subprocess_exec(
                "fuser",
                "-k",
                f"{port}/tcp",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            await asyncio.sleep(0.5)
        except FileNotFoundError:
            logger.warning("fuser not available; cannot force-stop llama-server on :%s", port)

    return not await llama_server_is_up(settings)


async def ensure_llama_server_running(settings: Settings) -> bool:
    """Start llama-server if auto-manage enabled and models configured."""
    if await llama_server_is_up(settings):
        return True
    if not settings.llama_auto_manage:
        return False
    if not settings.qwen_vl_gguf or not settings.qwen_vl_mmproj:
        logger.warning("QWEN_VL_GGUF/MMPROJ unset; cannot auto-start llama-server")
        return False

    bin_path = settings.llama_server_bin
    cmd = [
        bin_path,
        "-m",
        settings.qwen_vl_gguf,
        "--mmproj",
        settings.qwen_vl_mmproj,
        "--host",
        settings.llama_server_host,
        "--port",
        str(settings.llama_server_port),
        "-c",
        "8192",
        "-ngl",
        "99",
        "--flash-attn",
        "on",
    ]
    log_path = settings.data_dir / "sqlite" / "llama-server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_f = open(log_path, "ab")  # noqa: SIM115
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=log_f,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except FileNotFoundError:
        log_f.close()
        logger.error("llama-server binary not found: %s", bin_path)
        return False

    _pid_file(settings).write_text(str(proc.pid), encoding="utf-8")
    logger.info("Started llama-server pid=%s", proc.pid)

    for _ in range(60):
        if await llama_server_is_up(settings):
            return True
        if proc.poll() is not None:
            logger.error("llama-server exited early; see %s", log_path)
            _pid_file(settings).unlink(missing_ok=True)
            return False
        await asyncio.sleep(0.5)
    logger.error("llama-server did not become ready in time")
    return False
