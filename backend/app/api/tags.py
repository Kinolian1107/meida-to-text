from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.models.schemas import TagCreateRequest, TagItem
from app.pipeline.cloud_llm import CloudLLMClient
from app.pipeline.tagging import generate_tags

router = APIRouter(tags=["tags"])


@router.get("/api/tags", response_model=list[TagItem])
async def list_tags(request: Request):
    store = request.app.state.store
    return [TagItem(**t, source="manual") for t in store.list_tags()]


@router.post("/api/videos/{video_id}/tags", response_model=TagItem)
async def add_video_tag(request: Request, video_id: str, body: TagCreateRequest):
    store = request.app.state.store
    if not store.get_video(video_id):
        raise HTTPException(404, "Video not found")
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "標籤名稱不可為空")
    tag = store.add_video_tag(video_id, name, body.kind, source="manual")
    return TagItem(**tag)


@router.delete("/api/videos/{video_id}/tags/{tag_id}")
async def remove_video_tag(request: Request, video_id: str, tag_id: str):
    store = request.app.state.store
    store.remove_video_tag(video_id, tag_id)
    return {"ok": True}


@router.post("/api/videos/{video_id}/generate-tags", response_model=list[TagItem])
async def regenerate_video_tags(request: Request, video_id: str):
    settings, store = request.app.state.settings, request.app.state.store
    video = store.get_video(video_id)
    if not video:
        raise HTTPException(404, "Video not found")
    summaries = store.list_summaries(video_id)
    if not summaries:
        raise HTTPException(400, "尚無摘要，無法產生標籤")
    latest = summaries[0]
    client = CloudLLMClient(settings, store)
    tags = await generate_tags(
        client,
        settings,
        video_id=video_id,
        title=video.get("filename") or "",
        summary_text=latest.get("content") or "",
    )
    if not tags:
        raise HTTPException(502, "AI 標籤產生失敗或無結果")
    store.replace_ai_tags(video_id, tags)
    return [TagItem(**t) for t in store.get_tags_for_videos([video_id])[video_id]]
