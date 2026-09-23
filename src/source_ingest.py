# -*- coding: utf-8 -*-
"""摄取任意素材：PDF 走 pdf_source；Office（PPTX/PPT/DOCX/DOC）复用 deepreader 的 extractor。

**为什么要有这个模块**（用户实测发现的疏漏）
------------------------------------------
R13 早就说好「复用真源 deepreader 的 `extractor.py`（PPTX / DOCX / 扫描件 OCR 能力）」，
但摄取层一直只认 PDF —— 结果用户放在逐页精读器里的**整门 PPTX 课程被挡在学习库门外**：
它的批注进了账本却不渲染，因为「源文件不在 `source/` 里」，而源文件就算放进去也读不了。

复用而不是自己写
--------------
  - 文字：`extractor.extract_any()`（python-pptx / 自写 docx 解析），
    带表格、讲者备注、内嵌图片 —— 这些都是它踩过坑换来的；
  - 页图：`pageref.prepare_pages()`（Office → PDF → pypdfium2）。
    注意它刻意**不用 `Slide.Export`**（实测在非交互会话里会挂死），
    而是走「Office 存成 PDF」这条老路 —— 这个决定我们也白拿。
"""
from __future__ import annotations

import os
import sys

import pdf_source
from engine import DEEPREADER

OFFICE_SUFFIXES = (".pptx", ".ppt", ".docx", ".doc")
ALL_SUFFIXES = (".pdf",) + OFFICE_SUFFIXES


def _ensure_deepreader() -> None:
    if DEEPREADER not in sys.path:
        sys.path.insert(0, DEEPREADER)


def supported(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in ALL_SUFFIXES


def ingest_any(path: str, images_dir: str | None = None, scale: float = 1.6,
               render_images: bool = True) -> dict:
    """按扩展名分派。返回与 `pdf_source.ingest` 同构的逐页结构。"""
    suffix = os.path.splitext(path)[1].lower()
    if suffix == ".pdf":
        d = pdf_source.ingest(path, images_dir=images_dir, scale=scale,
                              render_images=render_images)
        d["kind"] = "pdf"
        return d
    if suffix in OFFICE_SUFFIXES:
        return ingest_office(path, images_dir=images_dir, render_images=render_images)
    raise ValueError(
        f"不支持的素材类型 {suffix or '(无扩展名)'}；支持：{'、'.join(ALL_SUFFIXES)}"
    )


def ingest_office(path: str, images_dir: str | None = None,
                  render_images: bool = True) -> dict:
    """PPTX / PPT / DOCX / DOC → 逐页结构。

    页图失败**不影响文字**：文字拿得到就先把课收进来，页图缺失只是少一样东西
    （而且 `prepare_pages` 已有就跳过，下次补也行）。
    """
    _ensure_deepreader()
    from src.extractor import extract_any   # noqa: PLC0415

    raw = extract_any(path)
    slides = raw.get("slides") or []

    pages: list[dict] = []
    for i, s in enumerate(slides, start=1):
        no = int(s.get("number") or i)
        title = str(s.get("title") or "").strip()
        body = str(s.get("text") or "").strip()
        notes = str(s.get("notes") or "").strip()
        parts: list[str] = []
        # extract_any 的 text 里可能已经含了标题，去重一下
        if title and title not in body:
            parts.append(title)
        if body:
            parts.append(body)
        if notes:
            parts.append(f"【讲者备注】{notes}")
        text = "\n".join(parts).strip()
        pages.append({
            "no": no,
            "chars_raw": len(text),
            "chars_clean": len(text),
            "text": text,
            "lines_removed": 0,
            "has_text_layer": bool(text),
            "is_blank": len(text) < 3,
        })

    page_count = len(pages)
    warn = ""
    if render_images and images_dir:
        os.makedirs(images_dir, exist_ok=True)
        try:
            from src.pageref import prepare_pages   # noqa: PLC0415
            # ⚠️ prepare_pages 返回的是**元组** `(页图列表, 转换后的PDF或None)`。
            # 第一版直接 `for i, p in enumerate(prepare_pages(...))`，
            # 于是 i=0 拿到整个列表、i=1 拿到 PDF 路径 —— 页图字段全是垃圾，
            # 还误报"页图 2 张与页数 132 不一致"。这个坑踩过。
            imgs, _converted_pdf = prepare_pages(path, out_dir=images_dir)
            for i, p in enumerate(imgs):
                if i < len(pages):
                    pages[i]["image"] = os.path.basename(str(p))
            if len(imgs) != page_count:
                warn = (f"页图 {len(imgs)} 张与页数 {page_count} 不一致"
                        f"（转换/分页可能有偏差）")
        except Exception as e:  # noqa: BLE001 - 页图失败不该让整门课进不来
            warn = f"页图渲染失败：{type(e).__name__}: {e}"

    # 章节名：取第一页第一行（PPTX 的第一页通常就是封面/章标题）
    running_head = ""
    for p in pages:
        first = (p["text"].split("\n") or [""])[0].strip()
        if first:
            running_head = first[:40]
            break

    return {
        "pages": pages,
        "page_count": page_count,
        "boilerplate": [],
        "running_head": running_head,
        "kind": "office",
        "warn": warn,
    }
