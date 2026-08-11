from __future__ import annotations

import io
from pathlib import Path
from typing import Any


def timeline_to_markdown(
    *,
    title: str,
    segments: list[dict[str, Any]],
    summary: str | None = None,
) -> str:
    lines = [f"# {title}", ""]
    if summary:
        lines.extend(["## 摘要", "", summary, "", "## 時間軸", ""])
    else:
        lines.extend(["## 時間軸", ""])
    for seg in segments:
        start = float(seg.get("start") or 0)
        end = float(seg.get("end") or start)
        kind = seg.get("type") or "speech"
        speaker = seg.get("speaker")
        prefix = f"[{start:.1f}–{end:.1f}] ({kind})"
        if speaker:
            prefix += f" {speaker}"
        lines.append(f"- {prefix} {seg.get('text') or ''}")
    lines.append("")
    return "\n".join(lines)


def export_markdown(path: Path, markdown: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")
    return path


def export_docx(path: Path, title: str, markdown: str) -> Path:
    from docx import Document

    doc = Document()
    doc.add_heading(title, level=1)
    for line in markdown.splitlines():
        if line.startswith("# "):
            continue
        if line.startswith("## "):
            doc.add_heading(line[3:].strip(), level=2)
        elif line.startswith("- "):
            doc.add_paragraph(line[2:], style="List Bullet")
        elif line.strip():
            doc.add_paragraph(line)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return path


def export_pdf(path: Path, title: str, markdown: str) -> Path:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    path.parent.mkdir(parents=True, exist_ok=True)
    # Prefer a CJK-capable font if present on the system
    font_name = "Helvetica"
    for candidate in (
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        p = Path(candidate)
        if p.exists():
            try:
                pdfmetrics.registerFont(TTFont("ExportFont", str(p)))
                font_name = "ExportFont"
                break
            except Exception:
                continue

    c = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4
    y = height - 48
    c.setFont(font_name, 14)
    c.drawString(48, y, title[:80])
    y -= 28
    c.setFont(font_name, 10)
    for line in markdown.splitlines():
        if y < 48:
            c.showPage()
            c.setFont(font_name, 10)
            y = height - 48
        # simple wrap
        text = line[:110]
        c.drawString(48, y, text)
        y -= 14
    c.save()
    return path


def build_export_bytes(fmt: str, title: str, markdown: str) -> tuple[bytes, str, str]:
    """Return (bytes, media_type, filename)."""
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in title)[:60] or "export"
    if fmt == "md":
        data = markdown.encode("utf-8")
        return data, "text/markdown; charset=utf-8", f"{safe}.md"
    if fmt == "docx":
        buf = io.BytesIO()
        tmp = Path("/tmp") / f"{safe}.docx"
        export_docx(tmp, title, markdown)
        data = tmp.read_bytes()
        tmp.unlink(missing_ok=True)
        return (
            data,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            f"{safe}.docx",
        )
    if fmt == "pdf":
        tmp = Path("/tmp") / f"{safe}.pdf"
        export_pdf(tmp, title, markdown)
        data = tmp.read_bytes()
        tmp.unlink(missing_ok=True)
        return data, "application/pdf", f"{safe}.pdf"
    raise ValueError(f"Unsupported format: {fmt}")
