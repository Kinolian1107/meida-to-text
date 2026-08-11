from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Request

from app.models.schemas import PromptTemplateItem, PromptTemplateUpsert
from app.pipeline.summarize import list_summary_templates

router = APIRouter(prefix="/api/prompts", tags=["prompts"])


def _merged_templates(request: Request) -> list[PromptTemplateItem]:
    settings = request.app.state.settings
    store = request.app.state.store
    builtin = {
        i["id"]: PromptTemplateItem(
            id=i["id"], name=i["name"], content=i["content"], builtin=True
        )
        for i in list_summary_templates(settings)
    }
    for row in store.list_prompt_templates():
        builtin[row["id"]] = PromptTemplateItem(
            id=row["id"],
            name=row["name"],
            content=row["content"],
            builtin=False,
        )
    return sorted(builtin.values(), key=lambda x: x.id)


@router.get("/summary", response_model=list[PromptTemplateItem])
async def get_summary_prompts(request: Request):
    return _merged_templates(request)


@router.post("/summary", response_model=PromptTemplateItem)
async def upsert_summary_prompt(request: Request, body: PromptTemplateUpsert):
    tid = body.id.strip()
    if not re.fullmatch(r"[a-zA-Z0-9_\-]{1,64}", tid):
        raise HTTPException(400, "id 僅允許英數、底線、連字號")
    store = request.app.state.store
    row = store.upsert_prompt_template(
        tid=tid, name=body.name.strip() or tid, content=body.content
    )
    # Also mirror to prompts/summary for file-based workflows
    settings = request.app.state.settings
    out = settings.prompts_dir / "summary" / f"{tid}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body.content, encoding="utf-8")
    return PromptTemplateItem(
        id=row["id"], name=row["name"], content=row["content"], builtin=False
    )


@router.delete("/summary/{template_id}")
async def delete_summary_prompt(request: Request, template_id: str):
    store = request.app.state.store
    settings = request.app.state.settings
    ok = store.delete_prompt_template(template_id)
    path = settings.prompts_dir / "summary" / f"{template_id}.txt"
    # Only delete custom file if it was user-owned in SQLite
    if ok and path.exists():
        # Keep builtin templates on disk; remove only if custom deleted
        path.unlink(missing_ok=True)
    if not ok:
        raise HTTPException(404, "Template not found (builtin cannot be deleted)")
    return {"ok": True}
