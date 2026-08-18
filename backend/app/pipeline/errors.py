from __future__ import annotations


class PipelineError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class VideoUnavailableError(PipelineError):
    def __init__(self, message: str = "影片不存在或已下架") -> None:
        super().__init__("VIDEO_UNAVAILABLE", message)


class AuthRequiredError(PipelineError):
    def __init__(self, message: str = "需要登入或會員才能存取") -> None:
        super().__init__("AUTH_REQUIRED", message)


class CookieExpiredError(PipelineError):
    def __init__(self, message: str = "Cookie 已失效，請重新匯出") -> None:
        super().__init__("COOKIE_EXPIRED", message)


class GeoBlockedError(PipelineError):
    def __init__(self, message: str = "地區限制，無法下載") -> None:
        super().__init__("GEO_BLOCKED", message)


class YtdlpExtractFailedError(PipelineError):
    def __init__(
        self, message: str = "yt-dlp 擷取失敗，可能需要更新 yt-dlp"
    ) -> None:
        super().__init__("YTDLP_EXTRACT_FAILED", message)


class LivestreamProcessingError(PipelineError):
    def __init__(
        self,
        message: str = (
            "此影片為剛結束的直播，YouTube 尚在轉檔為完整版本（目前僅提供近期片段），"
            "下載會缺片段而失敗；請稍後（約數小時）再重新處理"
        ),
    ) -> None:
        super().__init__("LIVESTREAM_PROCESSING", message)


class PotProviderUnavailableError(PipelineError):
    def __init__(
        self,
        message: str = (
            "YouTube 擋下下載（403）。依可能性由高到低："
            "(1) yt-dlp 缺 JS runtime 或 challenge solver，導致只剩會被 403 的 "
            "android_vr 格式可選 —— 確認 .venv 內有 deno 與 yt-dlp-ejs；"
            "(2) YTDLP_PLAYER_CLIENTS 指定的 client 已被 YouTube 淘汰，需要換一組；"
            "(3) PO Token provider 沒在跑 —— curl http://127.0.0.1:4416/ping 確認"
        ),
    ) -> None:
        super().__init__("POT_PROVIDER_UNAVAILABLE", message)


class CaptionEmptyError(PipelineError):
    def __init__(self, message: str = "字幕檔為空") -> None:
        super().__init__("CAPTION_EMPTY", message)


class DownloadTooLargeError(PipelineError):
    def __init__(self, message: str = "檔案超過允許大小") -> None:
        super().__init__("DOWNLOAD_TOO_LARGE", message)


class InvalidMediaError(PipelineError):
    def __init__(self, message: str = "不是合法的影音檔") -> None:
        super().__init__("INVALID_MEDIA", message)


class AsrFailedError(PipelineError):
    def __init__(self, message: str = "WhisperX 轉錄失敗") -> None:
        super().__init__("ASR_FAILED", message)


class VlFailedError(PipelineError):
    def __init__(self, message: str = "畫格描述失敗") -> None:
        super().__init__("VL_FAILED", message)


class CloudLlmFailedError(PipelineError):
    def __init__(self, message: str = "雲端 LLM 呼叫失敗") -> None:
        super().__init__("CLOUD_LLM_FAILED", message)
