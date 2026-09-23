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


# ---------------------------------------------------------------- 知识点笔记与双链
#
# 为什么要「一个知识点一个笔记」（用户提的「建立知识点图谱」）：
#   Obsidian 的**图谱视图只认 `[[双链]]`**。知识点如果只是一段文本，
#   图谱里什么都长不出来；只有变成独立笔记、互相链接，才能看到
#   枢纽节点、依赖链、以及哪些是孤岛。

#: Windows 文件名禁用字符
_BAD_FN = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def kc_note_name(kc: dict) -> str:
    """知识点笔记的文件名（不含 .md）。Obsidian 用它做 `[[链接]]` 的目标。"""
    label = _BAD_FN.sub("-", str(kc.get("label") or "")).strip(" -、，。")
    label = re.sub(r"-{2,}", "-", label)[:36]
    return f"{kc.get('id', '')} {label}".strip()


def kc_link(kc: dict) -> str:
    """指向知识点笔记的双链。"""
    return f"[[{kc_note_name(kc)}]]"


def render_kc_note(kc: dict, lecture_label: str, lecture_note: str, outline: dict,
                   by_id: dict, backlinks: list) -> str:
    """一个知识点的独立笔记（**生成块**内容，标题由 write_kc_note 加）。

    前置写成双链 → Obsidian 图谱能长出依赖链；
    反向链接（谁依赖我）也显式写进去 —— Obsidian 自己会算，但写出来更直观。

    `lecture_note` 是讲次笔记的**文件名**（不含 .md）；不要用导览标题去做链接，
    那会指向一个不存在的笔记（实测踩到：`[[本讲导览：从…]]` 是断链）。
    """
    out: list[str] = []
    t = _KC_TYPE_CN.get(kc.get("type"), kc.get("type", ""))
    imp = _KC_IMP_CN.get(kc.get("importance"), kc.get("importance", ""))
    star = _KC_IMP_STAR.get(kc.get("importance"), "")
    meta = [f"`{kc.get('id', '')}`", t, f"{imp} {star}"]
    if kc.get("is_hub"):
        meta.append("🧭 枢纽")
    if kc.get("part"):
        meta.append(f"📂 {kc['part']}")
    out.append("> " + " · ".join(meta))
    out.append("")
    out.append(f"> 出处：`{lecture_note}` 第 "
               + "、".join(str(p) for p in (kc.get("pages") or [])) + " 页")
    out.append("")

    out.append("## 要点")
    out.append("")
    for p in kc.get("points") or []:
        out.append(f"- {p}")
    out.append("")

    if kc.get("self_test"):
        out.append("## 自测（能顺畅答上来才算学会）")
        out.append("")
        for i, q in enumerate(kc["self_test"], 1):
            out.append(f"{i}. {q}")
        out.append("")

    if kc.get("questions"):
        out.append("## 我卡过的地方")
        out.append("")
        for q in kc["questions"]:
            out.append(f"- 💬 {q.get('q', '')}"
                       + (f"　*(第 {q.get('page')} 页)*" if q.get("page") else ""))
        out.append("")

    deps = [by_id[d] for d in (kc.get("deps") or []) if d in by_id]
    if deps:
        out.append("## 学它之前先懂")
        out.append("")
        for d in deps:
            out.append(f"- {kc_link(d)}")
        out.append("")

    if backlinks:
        out.append("## 学会了它才能学")
        out.append("")
        for b in backlinks:
            out.append(f"- {kc_link(b)}")
        out.append("")

    out.append("---")
    out.append("")
    out.append(f"← 回到讲次：[[{lecture_note}]]")
    return "\n".join(out)


#: 知识点笔记的「你的地盘」尾部（与讲义笔记那份不同：这里是原子笔记，只留一块）
KC_HANDWRITTEN = """## 我的理解

> 这一节是你的地盘 —— 程序重跑只替换上面的生成块，这里一个字都不动。
>
> 两点建议：**写你自己的话**（别照抄要点，照抄等于没写）；
> **用 `[[双链]]` 关联别的知识点**（Obsidian 的图谱就是这么长出来的）。
"""


def write_kc_note(path: str, title: str, body: str) -> str:
    """写知识点原子笔记：生成块 + 你自己的理解区。"""
    existing = None
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            existing = f.read()
    merged = merge_gen_block(existing, title, body, tail=KC_HANDWRITTEN)
    atomic_write_text(path, merged)
    return merged


def render_questions_page(course: str, chapters: list) -> str:
    """全课程自测问题清单：**按讲次结构分组**。

    用户要的「抽象出知识点问题」—— 陈述句读完会觉得懂了，问句才会暴露没懂。
    """
    total = sum(len(k.get("self_test") or [])
                for c in chapters for k in (c.get("kcs") or []))
    out: list[str] = [f"> 共 **{total}** 道自测题。本页由程序生成，重跑会自动更新。", ""]
    for c in chapters:
        kcs = [k for k in (c.get("kcs") or []) if k.get("self_test")]
        if not kcs:
            continue
        outline = c.get("outline") or {}
        out.append(f"## {outline.get('title') or c.get('label') or c.get('id')}")
        out.append("")
        parts_order = [p.get("label") for p in (outline.get("parts") or [])]
        groups: dict = {}
        for k in kcs:
            groups.setdefault(k.get("part") or "", []).append(k)
        keys = [x for x in parts_order if x in groups] + \
               [x for x in groups if x and x not in parts_order] + \
               ([""] if "" in groups else [])
        for key in keys:
            out.append(f"### 📂 {key}" if key else "### 📂 （未归属）")
            out.append("")
            for k in sorted(groups[key], key=lambda x: (x.get("page") or 0, x["id"])):
                star = _KC_IMP_STAR.get(k.get("importance"), "")
                qs = k["self_test"]
                if len(qs) == 1:
                    out.append(f"- {kc_link(k)} {star} — {qs[0]}")
                else:
                    out.append(f"- **{kc_link(k)}** {star}")
                    for q in qs:
                        out.append(f"    - {q}")
            out.append("")
    return "\n".join(out).rstrip() + "\n"


GUIDE = """# 怎么用这个库

> 这一页是**使用指南**，不参与自动生成 —— 你可以随时改。

## 三类内容，各是谁的地盘

| 位置 | 谁写的 | 能不能改 |
|---|---|---|
| `source/` | 你放的原始素材 | **程序只读**，永远不动 |
| `assets/` | 程序渲染的页图 | 可删（重跑会重建） |
| `.ledger/` | **账本（唯一真相源）** | 别手改；删了这些内容就真没了 |
| 笔记里 `<!-- gen:begin -->…<!-- gen:end -->` 块 | 程序 | 重跑会覆盖 |
| 笔记里 `✍️ 我的批注` 槽 / `## 我的笔记` | **你** | 程序永远不动 |
| 知识点笔记的 `## 我的理解` | **你** | 程序永远不动 |

一句话：**你能打字的地方程序都不碰；程序生成的地方都在 `gen` 块或固定小节里。**

## 每天怎么用（五步）

### 1. 先看「问题」，不要先看「知识点」

打开 `_问题清单.md`。
**陈述句读完你会觉得懂了；问句才会逼出"其实答不上来"。**
挑一道，**先自己答**，答不出来再点进那个知识点看要点。
（每道题都是一个 `[[双链]]`，点一下就跳过去。）

### 2. 卡住的地方就地写下来

在知识点笔记的 `## 我的理解` 里写。这是你的地盘。
写的时候用 `[[双链]]` 关联别的知识点 —— **图谱就是这么长出来的**。

### 3. 把「我卡过的地方」当复习入口

知识点笔记里有 `## 我卡过的地方`：那是你在逐页精读器里框选提问过的记录，
程序自动挂上来的。**这些才是你真正的薄弱点**，比任何"重点"标记都准。

### 4. 用图谱看结构

左侧「关系图谱」。看三件事：
- **枢纽**（连线多的）→ 优先学，回报最高；
- **依赖链** → 顺着箭头方向学，别跳；
- **孤岛**（没连线的）→ 要么是独立小节，要么是提取质量有问题。

### 5. 出处永远指得回去

每个知识点都写着「出处：《某某》第 N 页」。
**不信就回去看原页** —— 人工抽检就在这里做。

## 记笔记的三条经验

1. **一个笔记只说一件事**。知识点笔记是原子的，别往里塞别的东西。
2. **写你自己的话，别抄要点**。要点是程序给的，理解是你的 —— 照抄一遍等于没写。
3. **链接比分类重要**。不要建一堆文件夹分类，用 `[[双链]]` + 标签。

## 标签约定（可以自己加）

| 标签 | 意思 |
|---|---|
| `#没懂` | 当场没搞明白的 |
| `#待复习` | 懂了但怕忘的 |
| `#易错` | 反复踩的坑 |
| `#已掌握` | 能顺畅讲出来的 |

程序自己会打的标签：`course-pipeline`、课程名、讲次名、`p<页号>`、`AI出题`、`追问`。

## 常用操作

```powershell
cd "D:\\deepseek harness\\course-pipeline"

# 素材没变时：重渲染笔记（纯本地、秒级、不花钱）
python run.py --course 物理 all

# 需要 AI 的两步（有缓存，没变就不花钱）
python run.py --course 物理 kcs     # 知识点：导览 → 提取 → 串联 → 出题
python run.py --course 物理 cards   # 制卡推进 Anki（加 --sync）
```"""


def render_guide() -> str:
    return GUIDE


def render_kc_block(kc: dict) -> str:
    """一个知识点 → markdown（挂在它所出的那一页下面）。"""
    out: list[str] = []
    t = _KC_TYPE_CN.get(kc.get("type"), kc.get("type", ""))
    imp = _KC_IMP_CN.get(kc.get("importance"), kc.get("importance", ""))
    star = _KC_IMP_STAR.get(kc.get("importance"), "")
    hub = " · 🧭 枢纽" if kc.get("is_hub") else ""
    part = f" · 📂 {kc['part']}" if kc.get("part") else ""
    out.append(f"##### 🎯 {kc_link(kc)}　`{kc.get('id', '')}`")
    out.append("")
    out.append(f"> {t} · {imp} {star}{hub}{part}")
    for p in kc.get("points") or []:
        out.append(f"> - {p}")
    deps = kc.get("deps") or []
    if deps:
        out.append(f"> - 前置：{'、'.join(deps)}")
    for q in kc.get("self_test") or []:
        out.append(f"> - 🧪 自测：{q}")
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

def merge_gen_block(existing: str | None, title: str, body: str, tail: str | None = None) -> str:
    """把 body 塞进 marker 块。已有的手写内容原样保留。

    - 文件不存在 → 新建：标题 + 生成块 + 手写区模板
    - 有 marker   → 只替换块内
    - 无 marker   → 抛错，绝不覆盖（那是用户的文件）
    """
    block = f"{GEN_BEGIN}\n{body.rstrip()}\n{GEN_END}"

    if existing is None:
        return f"# {title}\n\n{block}\n\n{tail if tail is not None else HANDWRITTEN_SECTION}"

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
