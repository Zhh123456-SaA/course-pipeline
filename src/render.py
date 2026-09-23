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

#: 知识点类型/重要度的中文显示名（骨架里存的是英文枚举）
_KC_TYPE_CN = {"concept": "概念", "principle": "原理", "procedure": "方法", "fact": "事实"}
_KC_IMP_CN = {"must": "必须掌握", "key": "重要", "freq": "常用", "info": "了解"}
_KC_IMP_STAR = {"must": "★★★", "key": "★★", "freq": "★", "info": "☆"}


def render_kc_block(kc: dict) -> str:
    """一个知识点 → markdown（挂在它所出的那一页下面）。"""
    out: list[str] = []
    t = _KC_TYPE_CN.get(kc.get("type"), kc.get("type", ""))
    imp = _KC_IMP_CN.get(kc.get("importance"), kc.get("importance", ""))
    star = _KC_IMP_STAR.get(kc.get("importance"), "")
    hub = " · 🧭 枢纽" if kc.get("is_hub") else ""
    part = f" · 📂 {kc['part']}" if kc.get("part") else ""
    out.append(f"##### 🎯 {kc.get('label', '')}　`{kc.get('id', '')}`")
    out.append("")
    out.append(f"> {t} · {imp} {star}{hub}{part}")
    for p in kc.get("points") or []:
        out.append(f"> - {p}")
    deps = kc.get("deps") or []
    if deps:
        out.append(f"> - 前置：{'、'.join(deps)}")
    for q in kc.get("questions") or []:
        out.append(f"> - 💬 **你问过**：{q.get('q', '')}")
    return "\n".join(out)


def render_outline_block(outline: dict) -> str:
    """讲次导览：AI 读完课件的导览/目标页后写出的骨架。

    用户建议的产物 —— 让 AI 先领会「老师是怎么组织这一讲的」，
    后面的知识点归属与依赖判定才有依据。
    """
    if not outline:
        return ""
    parts = outline.get("parts") or []
    objs = outline.get("objectives") or []
    title = outline.get("title") or ""
    if not (parts or objs or title):
        return ""
    out: list[str] = ["### 🧭 本讲导览", ""]
    if title:
        out.append(f"**{title}**")
        out.append("")
    if objs:
        out.append("**课件写明的学习目标**")
        out.append("")
        for o in objs:
            out.append(f"- {o.rstrip(chr(65307) + chr(59))}")
        out.append("")
    if parts:
        out.append("**结构**")
        out.append("")
        out.append("| # | 部分 | 页 | 讲什么 |")
        out.append("|---|---|---|---|")
        for i, p in enumerate(parts, 1):
            out.append(f"| {i} | {p.get('label', '')} "
                       f"| {p.get('from', '?')}–{p.get('to', '?')} "
                       f"| {p.get('summary', '')} |")
        out.append("")
    return "\n".join(out).rstrip()


def render_overview_body(course: str, chapters: list[dict]) -> str:
    """课程级知识点总览：**按讲次结构分组**。"""
    total = sum(len(c.get("kcs") or []) for c in chapters)
    n_must = sum(1 for c in chapters for k in (c.get("kcs") or [])
                 if k.get("importance") == "must")
    n_q = sum(1 for c in chapters for k in (c.get("kcs") or []) if k.get("questions"))
    n_dep = sum(1 for c in chapters for k in (c.get("kcs") or []) if k.get("deps"))
    out: list[str] = [
        f"> 共 **{total}** 个知识点（必须掌握 {n_must} 个；**{n_q} 个你问过问题**；"
        f"{n_dep} 个有前置依赖）。本页由程序生成，重跑会自动更新。",
        "",
    ]
    for c in chapters:
        kcs = c.get("kcs") or []
        if not kcs:
            continue
        outline = c.get("outline") or {}
        head = outline.get("title") or c.get("label") or c.get("id")
        out.append(f"## {head}")
        out.append("")
        if outline.get("objectives"):
            out.append("**学习目标**：" + "；".join(o.rstrip("；;") for o in outline["objectives"]))
            out.append("")

        # 按 part 分组；没有 part 的放最后
        order = [p.get("label") for p in (outline.get("parts") or [])]
        groups: dict[str, list[dict]] = {}
        for k in kcs:
            groups.setdefault(k.get("part") or "", []).append(k)
        keys = [x for x in order if x in groups] + \
               [x for x in groups if x and x not in order] + \
               ([""] if "" in groups else [])

        for key in keys:
            if key:
                summary = next((p.get("summary", "") for p in (outline.get("parts") or [])
                                if p.get("label") == key), "")
                out.append(f"### 📂 {key}" + (f" — {summary}" if summary else ""))
            else:
                out.append("### 📂 （未归属）")
            out.append("")
            out.append("| id | 知识点 | 类型 | 重要度 | 页 | 前置 | 你问过 |")
            out.append("|---|---|---|---|---|---|---|")
            for k in sorted(groups[key], key=lambda x: (x.get("page") or 0, x["id"])):
                t = _KC_TYPE_CN.get(k.get("type"), k.get("type", ""))
                imp = _KC_IMP_CN.get(k.get("importance"), k.get("importance", ""))
                pages = "、".join(str(p) for p in (k.get("pages") or []))
                deps = "、".join(k.get("deps") or []) or ""
                nq = len(k.get("questions") or [])
                hub = " 🧭" if k.get("is_hub") else ""
                out.append(f"| `{k.get('id','')}` | {k.get('label','')}{hub} | {t} | {imp} "
                           f"| {pages} | {deps} | {'💬 ' + str(nq) if nq else ''} |")
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_lecture_body(course: str, lecture: dict, include_images: bool = True,
                        annotations_by_page: dict[int, list[dict]] | None = None,
                        kcs: list[dict] | None = None,
                        outline: dict | None = None) -> str:
    """渲染一篇讲义笔记的「生成块」内容。

    `annotations_by_page`：来自 ppt-deepreader 的框选追问（归档通道搬进来的）。
    `kcs`：从这一讲提炼出来的知识点（S2），按页挂到对应页面下面。

    每页的顺序固定为：**讲义原文 → 知识点 → AI 追问记录 → 我的手写批注位**。
    """
    import archive as _archive   # 延迟导入，避免模块级循环依赖
    import kcs as _kcs

    ann_by_page = annotations_by_page or {}
    kc_list = kcs or []
    out: list[str] = []
    head = lecture.get("running_head") or ""
    # 图片目录用 slug（无空格/无禁用字符），否则 `![x](../assets/01 GC01/p.png)`
    # 在 CommonMark 里是非法链接，VS Code 预览与 GitHub 都显示不出图。
    slug = lecture.get("slug") or lecture["id"]

    n_ann = sum(len(v) for v in ann_by_page.values())
    out.append(f"> 来源：`{lecture['file']}` ｜ 共 {lecture['page_count']} 页"
               + (f" ｜ 页眉：{head}" if head else ""))
    if n_ann:
        pages = "、".join(str(k) for k in sorted(ann_by_page))
        out.append(">")
        out.append(f"> 含 **{n_ann}** 条来自逐页精读器的追问记录（第 {pages} 页）")
    if lecture.get("boilerplate"):
        shown = "、".join(lecture["boilerplate"][:4])
        out.append(f">")
        out.append(f"> 已自动剔除的页眉页脚：{shown}")
    out.append("")
    out.append("---")
    out.append("")
    # 讲次导览（AI 读完课件的导览/目标页后写出的骨架）
    ob = render_outline_block(outline or {})
    if ob:
        out.append(ob)
        out.append("")
        out.append("---")
        out.append("")

    for p in lecture["pages"]:
        no = p["no"]
        out.append(f"### 第 {no} 页")
        out.append("")
        if p.get("image") and include_images:
            out.append(f"![第 {no} 页](../assets/{slug}/{p['image']})")
            out.append("")
        if not p.get("has_text_layer", True):
            out.append("*（本页没有文字层 —— 内容全在图上）*")
        elif p.get("is_blank"):
            out.append("*（本页几乎没有文字）*")
        else:
            for para in p["text"].split("\n"):
                out.append(para)

        # 这一页提炼出的知识点（S2）
        page_kcs = _kcs.kcs_by_page(kc_list, no)
        if page_kcs:
            out.append("")
            for k in page_kcs:
                out.append(render_kc_block(k))
                out.append("")

        # 归档进来的 AI 追问记录（来自逐页精读器的框选提问）
        for a in ann_by_page.get(no, []):
            out.append("")
            out.append(f"#### 🤖 追问记录 #{a.get('no', '?')}")
            out.append("")
            out.append(_archive.render_annotation_md(a, slug))

        out.append("")
        # 每页紧跟一个批注位 —— 批注必须在被批注内容的旁边
        out.append(annotation_block(no))
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
