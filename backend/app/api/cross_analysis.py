from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.models.schemas import (
    CrossAnalysisCreate,
    CrossAnalysisItem,
    CrossAnalysisResult,
    CrossPoint,
    RelatedItem,
)
from app.pipeline.cloud_llm import CloudLLMClient
from app.pipeline.cross_analysis import run_cross_analysis

router = APIRouter(prefix="/api/cross-analysis", tags=["cross-analysis"])


def _to_item(row: dict) -> CrossAnalysisItem:
    result = row.get("result") or {}
    return CrossAnalysisItem(
        id=row["id"],
        source_summary_ids=row.get("source_summary_ids") or [],
        user_prompt=row.get("user_prompt") or "",
        result=CrossAnalysisResult(
            common_points=[
                CrossPoint(**p) if isinstance(p, dict) else CrossPoint(text=str(p))
                for p in (result.get("common_points") or [])
            ],
            conflicts=[
                CrossPoint(**p) if isinstance(p, dict) else CrossPoint(text=str(p))
                for p in (result.get("conflicts") or [])
            ],
        ),
        created_at=row.get("created_at") or "",
    )


@router.get("", response_model=list[CrossAnalysisItem])
async def list_cross(request: Request):
    store = request.app.state.store
    return [_to_item(r) for r in store.list_cross_analyses()]


@router.post("", response_model=CrossAnalysisItem)
async def create_cross(request: Request, body: CrossAnalysisCreate):
    settings = request.app.state.settings
    store = request.app.state.store
    client = CloudLLMClient(settings, store)
    try:
        row = await run_cross_analysis(
            client=client,
            settings=settings,
            store=store,
            summary_ids=body.summary_ids,
            user_prompt=body.user_prompt,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    request.app.state.lance.upsert_cross_analysis(row)
    return _to_item(row)


@router.get("/related/{summary_id}", response_model=list[RelatedItem])
async def related_summaries(request: Request, summary_id: str, limit: int = 5):
    store = request.app.state.store
    lance = request.app.state.lance
    if not store.get_summary(summary_id):
        raise HTTPException(404, "Summary not found")
    hits = lance.find_related_summaries(summary_id, limit=limit)
    out: list[RelatedItem] = []
    for h in hits:
        video = store.get_video(h["video_id"]) or {}
        out.append(
            RelatedItem(
                summary_id=h["summary_id"],
                video_id=h["video_id"],
                filename=video.get("filename") or h["video_id"],
                score=float(h["score"]),
                snippet=h.get("snippet") or "",
            )
        )
    return out


@router.get("/{analysis_id}", response_model=CrossAnalysisItem)
async def get_cross(request: Request, analysis_id: str):
    row = request.app.state.store.get_cross_analysis(analysis_id)
    if not row:
        raise HTTPException(404, "Not found")
    return _to_item(row)
