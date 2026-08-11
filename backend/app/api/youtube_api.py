from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.models.schemas import YoutubeProbeResponse
from app.pipeline.errors import PipelineError
from app.pipeline.youtube import probe_youtube

router = APIRouter(prefix="/api/youtube", tags=["youtube"])


@router.get("/probe", response_model=YoutubeProbeResponse)
async def youtube_probe(request: Request, url: str = Query(...)):
    settings = request.app.state.settings
    try:
        data = probe_youtube(url, settings)
    except PipelineError as exc:
        raise HTTPException(400, detail={"code": exc.code, "message": exc.message})
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return YoutubeProbeResponse(**data)
