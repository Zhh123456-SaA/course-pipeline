# -*- coding: utf-8 -*-
"""讲义 PDF 摄取：抽出文字层、剥页眉页脚、断行重排、导出页图。

为什么需要「剥页眉页脚」和「断行重排」：
  讲义是 PPT 导出的 PDF，每页都有相同的页眉（如「绪 论」），而且一句话会被
  PPT 的文本框切成好几行。不处理的话，账本里全是噪音、笔记读起来是断的。
"""
from __future__ import annotations

import os
import re
from collections import Counter
from typing import Iterable

import pypdfium2 as pdfium

# ---------------------------------------------------------------- 常量

#: 项目符号 / 编号开头 —— 命中就视为新段落
BULLET_RE = re.compile(
    r"^\s*(?:"
    r"[•·▪◦●○◆■□※✓✔\-\*–—]"
    r"|\(?\d{1,2}[\.\)、]"
    r"|[（(][一二三四五六七八九十\d]{1,3}[）)]"
    r"|[一二三四五六七八九十]{1,3}[、\.]"
    r"|[A-Za-z][\.\)]"
    r")\s*"
)

#: 强句末标点：上一行以它结尾 → 下一行开新段落
ENDS_STRONG = "。！？!?…"

#: 页眉页脚判定：在超过这个比例的页面上出现的行
BOILERPLATE_RATIO = 0.4
BOILERPLATE_MIN_PAGES = 3

_CJK = re.compile(r"[\u3400-\u9fff\u3000-\u303f\uff00-\uffef]")


# ---------------------------------------------------------------- 抽取

def extract_raw_pages(pdf_path: str) -> list[str]:
    """按页抽出原始文字层。"""
    doc = pdfium.PdfDocument(pdf_path)
    try:
        return [doc[i].get_textpage().get_text_range() for i in range(len(doc))]
    finally:
        doc.close()


def split_lines(text: str) -> list[str]:
    """拆行并去掉空白行。"""
    return [l.strip() for l in text.replace("\r\n", "\n").replace("\r", "\n").split("\n") if l.strip()]


# ---------------------------------------------------------------- 页眉页脚

def detect_boilerplate(pages: list[list[str]]) -> set[str]:
    """找出在多页重复出现的行（页眉 / 页脚 / 固定角标）。

    只在页面**边缘位置**（前 2 行或后 2 行）统计，避免把正文里的常见短语误判。
    """
    n = len(pages)
    if n < BOILERPLATE_MIN_PAGES:
        return set()
    counter: Counter[str] = Counter()
    for lines in pages:
        edge = set(lines[:2]) | set(lines[-2:])
        for l in edge:
            counter[l] += 1
    threshold = max(BOILERPLATE_MIN_PAGES, int(n * BOILERPLATE_RATIO))
    return {l for l, c in counter.items() if c >= threshold}


def strip_boilerplate(lines: list[str], boilerplate: set[str]) -> list[str]:
    """只在页面的前 2 行 / 后 2 行位置剥离页眉页脚。"""
    if not boilerplate:
        return list(lines)
    keep = []
    last = len(lines) - 1
    for i, l in enumerate(lines):
        at_edge = i <= 1 or i >= last - 1
        if at_edge and l in boilerplate:
            continue
        keep.append(l)
    return keep


# ---------------------------------------------------------------- 断行重排

def _needs_space(a: str, b: str) -> bool:
    """中文之间直接接；只要有一侧是拉丁字母/数字就补一个空格。"""
    if not a or not b:
        return False
    left_cjk = bool(_CJK.match(a[-1]))
    right_cjk = bool(_CJK.match(b[0]))
    return not (left_cjk and right_cjk)


def reflow(lines: Iterable[str]) -> list[str]:
    """把被 PPT 文本框切碎的行合并回段落。

    开新段落的三种情况：
      1. 本行以项目符号 / 编号开头
      2. 上一行以强句末标点结尾
      3. 没有上一行
    """
    paras: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if not paras:
            paras.append(line)
            continue
        prev = paras[-1]
        if BULLET_RE.match(line) or prev.endswith(tuple(ENDS_STRONG)):
            paras.append(line)
        else:
            sep = " " if _needs_space(prev, line) else ""
            paras[-1] = prev + sep + line
    return paras


# ---------------------------------------------------------------- 页图

def render_page_image(doc, index: int, out_path: str, scale: float = 1.6) -> None:
    """把第 index 页渲染成 PNG。"""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    bitmap = doc[index].render(scale=scale)
    pil = bitmap.to_pil()
    pil.save(out_path, "PNG", optimize=True)


# ---------------------------------------------------------------- 一页的组装

def build_page(no: int, raw: str, boilerplate: set[str]) -> dict:
    """单页 → 账本里的一个 page 记录。"""
    lines = split_lines(raw)
    kept = strip_boilerplate(lines, boilerplate)
    paras = reflow(kept)
    text = "\n".join(paras)
    return {
        "no": no,
        "chars_raw": len(raw.strip()),
        "chars_clean": len(text),
        "text": text,
        "lines_removed": len(lines) - len(kept),
        "is_blank": len(text) < 10,
    }


def ingest(pdf_path: str, images_dir: str | None = None, scale: float = 1.6,
           render_images: bool = True) -> dict:
    """摄取一个讲义 PDF，返回账本用的数据结构（不落盘）。"""
    doc = pdfium.PdfDocument(pdf_path)
    try:
        n = len(doc)
        raw_pages = [doc[i].get_textpage().get_text_range() for i in range(n)]
        per_page_lines = [split_lines(t) for t in raw_pages]
        boilerplate = detect_boilerplate(per_page_lines)
        pages = [build_page(i + 1, raw_pages[i], boilerplate) for i in range(n)]

        if render_images and images_dir:
            os.makedirs(images_dir, exist_ok=True)
            for i in range(n):
                pages[i]["image"] = f"p{i + 1:03d}.png"
                render_page_image(doc, i, os.path.join(images_dir, pages[i]["image"]), scale)
    finally:
        doc.close()

    running_head = ""
    if boilerplate:
        running_head = max(boilerplate, key=len).replace(" ", "")

    return {
        "pages": pages,
        "page_count": n,
        "boilerplate": sorted(boilerplate),
        "running_head": running_head,
    }
