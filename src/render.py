# -*- coding: utf-8 -*-
"""把账本渲染成 Obsidian 能直接读的笔记。

核心规矩（需求 R12）：**程序只写 marker 块以内的内容，块以外一个字都不碰。**
第一次生成时会把「我的笔记」区一并建好；以后重跑只替换 marker 块内的部分，
所以你在块外写的东西永远不会被覆盖。
"""
from __future__ import annotations

import os
import re

from ledger import atomic_write_text

GEN_BEGIN = "<!-- gen:begin -->"
GEN_END = "<!-- gen:end -->"

MARKER_RE = re.compile(
    re.escape(GEN_BEGIN) + r"(.*?)" + re.escape(GEN_END), re.S
)

HANDWRITTEN_SECTION = """## 我的笔记

> 这一节是你的地盘。程序只改上面 `gen:begin` 与 `gen:end` 之间那块，
> 这里和以下所有内容它永远不会动。
"""


# ---------------------------------------------------------------- 拼装

def render_lecture_body(course: str, lecture: dict, include_images: bool = True) -> str:
    """渲染一篇讲义笔记的「生成块」内容。"""
    out: list[str] = []
    head = lecture.get("running_head") or ""
    # 图片目录用 slug（无空格/无禁用字符），否则 `![x](../assets/01 GC01/p.png)`
    # 在 CommonMark 里是非法链接，VS Code 预览与 GitHub 都显示不出图。
    slug = lecture.get("slug") or lecture["id"]
    out.append(f"> 来源：`{lecture['file']}` ｜ 共 {lecture['page_count']} 页"
               + (f" ｜ 页眉：{head}" if head else ""))
    if lecture.get("boilerplate"):
        shown = "、".join(lecture["boilerplate"][:4])
        out.append(f">")
        out.append(f"> 已自动剔除的页眉页脚：{shown}")
    out.append("")
    out.append("---")
    out.append("")

    for p in lecture["pages"]:
        out.append(f"### 第 {p['no']} 页")
        out.append("")
        if p.get("image") and include_images:
            out.append(f"![第 {p['no']} 页](../assets/{slug}/{p['image']})")
            out.append("")
        if not p.get("has_text_layer", True):
            out.append("*（本页没有文字层 —— 内容全在图上）*")
        elif p.get("is_blank"):
            out.append("*（本页几乎没有文字）*")
        else:
            for para in p["text"].split("\n"):
                out.append(para)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_index_body(course: str, lectures: list[dict]) -> str:
    out: list[str] = []
    out.append(f"> 共 {len(lectures)} 讲。本页由程序生成，重跑会自动更新。")
    out.append("")
    for lec in lectures:
        out.append(f"- [{lec['title']}]({lec['note_name']}) — {lec['page_count']} 页"
                   + (f" ｜ {lec['running_head']}" if lec.get("running_head") else ""))
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------- 合并写盘

def merge_gen_block(existing: str | None, title: str, body: str) -> str:
    """把 body 塞进 marker 块。已有的手写内容原样保留。

    - 文件不存在 → 新建：标题 + 生成块 + 手写区模板
    - 有 marker   → 只替换块内
    - 无 marker   → 抛错，绝不覆盖（那是用户的文件）
    """
    block = f"{GEN_BEGIN}\n{body.rstrip()}\n{GEN_END}"

    if existing is None:
        return f"# {title}\n\n{block}\n\n{HANDWRITTEN_SECTION}"

    if GEN_BEGIN not in existing or GEN_END not in existing:
        raise ValueError(
            "文件已存在但没有 gen:begin / gen:end 标记，程序拒绝改写。"
            "请手动加上标记，或把这个文件改名保留。"
        )
    return MARKER_RE.sub(lambda _m: block, existing, count=1)


def write_note(path: str, title: str, body: str) -> str:
    """读旧文件 → 合并 → 原子写。返回最终内容（便于测试）。"""
    existing = None
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            existing = f.read()
    merged = merge_gen_block(existing, title, body)
    atomic_write_text(path, merged)
    return merged
