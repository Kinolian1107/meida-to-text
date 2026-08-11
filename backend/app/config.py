from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = Path("./data")
    lancedb_uri: Path = Path("./data/lancedb")
    sqlite_path: Path = Path("./data/sqlite/app.db")

    whisperx_python: str = "/home/kino/asr/.venv-whisperx/bin/python3"
    whisperx_script: str = (
        "/home/kino/git/asr-kino/.claude/skills/asr-local/"
        "whisperx/scripts/transcribe_whisperx.py"
    )
    asr_hotwords_file: str = (
        "/home/kino/git/asr-kino/.claude/skills/asr-local/config/hotwords.txt"
    )
    asr_corrections_file: str = (
        "/home/kino/git/asr-kino/.claude/skills/asr-local/config/corrections.json"
    )
    enable_diarization: bool = False

    llama_server_url: str = "http://127.0.0.1:8080/v1"
    llama_server_model: str = "qwen3-vl"
    llama_server_host: str = "127.0.0.1"
    llama_server_port: int = 8080
    qwen_vl_gguf: str = ""
    qwen_vl_mmproj: str = ""
    llama_server_bin: str = "llama-server"
    llama_auto_manage: bool = True

    cloud_llm_base_url: str = ""
    cloud_llm_api_key: str = ""
    cloud_llm_model: str = ""

    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_embedding_model: str = "bge-m3"
    embed_dim: int = 1024

    max_download_bytes: int = 4_294_967_296
    youtube_cookies_file: str = ""
    ytdlp_sleep_seconds: int = 5

    scene_detector: str = "adaptive"  # adaptive | content
    content_threshold: float = 27.0

    # Bind 0.0.0.0 so Windows HOST can reach WSL2 services
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:4173,http://127.0.0.1:4173"
    )

    credentials_fernet_key: str = ""

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://127.0.0.1:8000/api/accounts/google/callback"
    frontend_origin: str = "http://127.0.0.1:5173"

    # Phase 4: optional Redis/ARQ queue (empty = in-process asyncio.Queue)
    redis_url: str = ""
    job_queue_backend: str = "asyncio"  # asyncio | arq

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def prompts_dir(self) -> Path:
        return Path(__file__).resolve().parents[2] / "prompts"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.lancedb_uri.mkdir(parents=True, exist_ok=True)
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)

    def ensure_fernet_key(self) -> str:
        """Return Fernet key; persist auto-generated key under data/ if unset."""
        if self.credentials_fernet_key:
            return self.credentials_fernet_key
        key_path = self.data_dir / "sqlite" / "fernet.key"
        if key_path.exists():
            return key_path.read_text(encoding="utf-8").strip()
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode("ascii")
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_text(key, encoding="utf-8")
        self.credentials_fernet_key = key
        return key


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
