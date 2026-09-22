# -*- coding: utf-8 -*-
"""把账本渲染成 Obsidian 能直接读的笔记。

两条规矩：

1. **生成块**（`gen:begin` … `gen:end`）归程序。重跑只替换块内。
   块外的内容（文件末尾的「我的笔记」）程序一个字都不碰。

2. **每页的批注位**（`批注区 pN` … `批注区结束 pN`）归你。
   它在生成块**内部**，但重跑时会被原样搬回去 —— 因为一个 69 页的笔记
   如果只有一个文件末尾的批注区，等于没有批注功能（这是第一版的设计错误，
   用户直接指出来了）。批注必须紧跟在它所批注的那一页下面。
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

#: 每页批注位的标记。用户看得见、能在 Obsidian 里直接点进去打字。
ANNOT_BEGIN = "<!-- 批注区 p{n} 开始 · 程序不会改动这里 -->"
ANNOT_END = "<!-- 批注区 p{n} 结束 -->"
ANNOT_HINT = "✍️ **我的批注**（点这里直接打字）"


def _annot_re(n: int) -> re.Pattern:
    return re.compile(
        r"[ \t]*" + re.escape(ANNOT_BEGIN.format(n=n)) + r"\n(.*?)\n?[ \t]*"
        + re.escape(ANNOT_END.format(n=n)),
        re.S,
    )


def extract_annotations(text: str) -> dict[int, str]:
    """从已有笔记里把每页的批注内容抠出来。"""
    found: dict[int, str] = {}
    for m in re.finditer(r"<!-- 批注区 p(\d+) 开始[^>]*-->\n(.*?)\n?[ \t]*<!-- 批注区 p\1 结束 -->",
                         text, re.S):
        found[int(m.group(1))] = m.group(2)
    return found


def annotation_block(n: int, body: str | None = None) -> str:
    """生成第 n 页的批注位。body 为空时给一句提示。"""
    inner = body if body is not None else f"> {ANNOT_HINT}\n>\n> "
    return (ANNOT_BEGIN.format(n=n) + "\n" + inner.rstrip()
            + "\n" + ANNOT_END.format(n=n))


def apply_annotations(generated: str, annotations: dict[int, str]) -> str:
    """把抠出来的批注塞回新生成的内容里（按页号配对）。"""
    if not annotations:
        return generated
    out = generated
    for n, body in annotations.items():
        out = _annot_re(n).sub(lambda _m, b=body: annotation_block(n, b), out, count=1)
    return out


HANDWRITTEN_SECTION = """## 我的笔记

> 这一节是你的地盘。上面每一页下面还有一个 **✍️ 我的批注** 位，
> 那也是一样 —— 程序重跑时只会把批注原样搬回去，不会改动你写的字。
>
> 在 Obsidian 里直接点进去打字即可（默认就是可编辑模式）。
> 想按页跳，用左边的**大纲**（Outline）面板。
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
        # 每页紧跟一个批注位 —— 批注必须在被批注内容的旁边
        out.append(annotation_block(p["no"]))
        out.append("")
        out.append("---")
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
    """读旧文件 → 抠出批注 → 合并 → 原子写。返回最终内容（便于测试）。

    批注的保管链条：**旧文件是批注的家**。先把它抠出来，等新内容生成好，
    再按页号塞回去。这样即使整篇重渲染，你写的字也不会丢。
    """
    existing = None
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            existing = f.read()

    annotations = extract_annotations(existing) if existing else {}
    body_with_annot = apply_annotations(body, annotations)
    merged = merge_gen_block(existing, title, body_with_annot)
    atomic_write_text(path, merged)
    return merged
