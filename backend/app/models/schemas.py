from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


SourceType = Literal[
    "upload_video",
    "upload_audio",
    "youtube",
    "direct_url",
    "google_drive",
]

CaptionSource = Literal["manual", "auto", "none"]

VideoStatus = Literal[
    "pending",
    "fetching_source",
    "extracting",
    "transcribing",
    "describing",
    "merging",
    "ready",
    "failed",
]


class VideoCreateResponse(BaseModel):
    id: str
    status: VideoStatus = "pending"


class YoutubeSubmitRequest(BaseModel):
    url: str
    topic: str = ""
    hotwords: str = ""
    account_id: str | None = None


class DirectUrlSubmitRequest(BaseModel):
    url: str
    topic: str = ""
    hotwords: str = ""


class GoogleDriveSubmitRequest(BaseModel):
    url: str
    topic: str = ""
    hotwords: str = ""
    account_id: str | None = None


class CorrectionInfo(BaseModel):
    """Cloud LLM correction metadata for UI badge."""

    applied: bool = False
    model: str | None = None
    models: dict[str, str | None] = Field(default_factory=dict)
    corrected_at: str | None = None


class VideoStatusResponse(BaseModel):
    id: str
    status: VideoStatus
    stage_label: str
    progress: int = 0
    error_code: str | None = None
    error_message: str | None = None
    caption_source: CaptionSource = "none"
    source_type: SourceType | str = "upload_video"
    filename: str = ""
    can_retranscribe_locally: bool = False
    correction: CorrectionInfo | None = None


class TagItem(BaseModel):
    id: str
    name: str
    kind: Literal["speaker", "show", "channel", "topic"]
    source: Literal["ai", "manual", "channel"] = "manual"
    count: int | None = None


class VideoListItem(BaseModel):
    id: str
    filename: str
    media_type: str
    source_type: str
    status: str
    duration_sec: float | None = None
    upload_time: str | None = None
    caption_source: str = "none"
    progress: int = 0
    error_code: str | None = None
    tags: list[TagItem] = Field(default_factory=list)


class VideoListResponse(BaseModel):
    items: list[VideoListItem]
    total: int
    page: int
    page_size: int


class TagCreateRequest(BaseModel):
    name: str
    kind: Literal["speaker", "show", "channel", "topic"] = "topic"


class TimelineSegment(BaseModel):
    id: str
    video_id: str
    start: float
    end: float
    type: Literal["speech", "frame"]
    text: str
    text_zh: str | None = None
    frame_path: str | None = None
    edited: bool = False
    speaker: str | None = None


class TranslationState(BaseModel):
    """Progress of the Traditional Chinese subtitle translation job."""

    status: Literal["idle", "running", "done", "failed"] = "idle"
    translated: int = 0
    total: int = 0
    model: str | None = None
    error: str | None = None
    updated_at: str | None = None


class TimelineResponse(BaseModel):
    video_id: str
    caption_source: CaptionSource = "none"
    can_retranscribe_locally: bool = False
    segments: list[TimelineSegment]
    correction: CorrectionInfo | None = None
    translation: TranslationState | None = None


class TimelineDiffItem(BaseModel):
    start: float
    end: float
    type: str = "speech"
    before: str
    after: str


class TimelineDiffResponse(BaseModel):
    video_id: str
    items: list[TimelineDiffItem]


class CrossAnalysisCreate(BaseModel):
    summary_ids: list[str] = Field(min_length=2)
    user_prompt: str = ""


class CrossPoint(BaseModel):
    text: str
    sources: list[str] = Field(default_factory=list)


class CrossAnalysisResult(BaseModel):
    common_points: list[CrossPoint] = Field(default_factory=list)
    conflicts: list[CrossPoint] = Field(default_factory=list)


class CrossAnalysisItem(BaseModel):
    id: str
    source_summary_ids: list[str]
    user_prompt: str = ""
    result: CrossAnalysisResult
    created_at: str


class RelatedItem(BaseModel):
    summary_id: str
    video_id: str
    filename: str
    score: float
    snippet: str = ""


class BatchUploadResponse(BaseModel):
    ids: list[str]


class SummarizeRequest(BaseModel):
    prompt_template: str = "bullet_points"
    custom_prompt: str | None = None


class SummaryItem(BaseModel):
    id: str
    video_id: str
    prompt_template: str
    content: str
    created_at: str


class PromptTemplateItem(BaseModel):
    id: str
    name: str
    content: str
    builtin: bool = True


class PromptTemplateUpsert(BaseModel):
    id: str
    name: str
    content: str


class TimelineSegmentPatch(BaseModel):
    text: str


class AccountCreateYoutube(BaseModel):
    account_label: str
    cookies_text: str


class AccountItem(BaseModel):
    id: str
    account_type: str
    account_label: str
    last_verified_at: str | None = None
    status: str = "active"


class RetranscribeRequest(BaseModel):
    topic: str | None = None
    hotwords: str | None = None


class YoutubeProbeResponse(BaseModel):
    title: str | None = None
    duration_sec: float | None = None
    caption_source: CaptionSource
    caption_lang: str | None = None
    message: str
    available_manual: list[str] = Field(default_factory=list)
    available_auto: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str = "ok"
    ytdlp_version: str | None = None


STAGE_LABELS: dict[str, str] = {
    "pending": "排隊中",
    "fetching_source": "抓取來源",
    "extracting": "擷取音軌與畫格",
    "transcribing": "語音轉文字",
    "describing": "畫格描述",
    "merging": "雲端校稿合併",
    "ready": "完成",
    "failed": "失敗",
}


class JobRecord(BaseModel):
    id: str
    video_id: str
    status: str
    stage_label: str
    progress: int = 0
    error_code: str | None = None
    error_message: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
