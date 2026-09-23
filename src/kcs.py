# -*- coding: utf-8 -*-
"""知识点骨架（KC）：把讲义页提炼成「可以单独学会、单独自测」的单元。

两条来源
--------
1. **讲义正文**（账本里的 `pages.json`，PDF 文字层清洗后的结果）；
2. **你自己的追问**（归档通道搬进来的 `annotations.json`）——
   被问到的点一定是你真正卡住的地方，所以 prompt 里明确要求**优先**把它们提炼成知识点。

schema 复用已有资产（C6）
-----------------------
严格沿用用户既有的 `knowledge_skeleton.json` 字段：
    id / label / type / importance / deps / is_hub / source_refs
外加本项目特有的三个：
    points    —— 要点（2~4 条），做笔记和出卡的原料
    pages     —— 出处页号（便于按页渲染）
    questions —— 挂上你在这个知识点上的追问（含原话）

id 的稳定性
----------
`id = "<讲次前缀>.<序号>"`，如 `L05.7`。**只要该讲的提取结果不变，id 就不变** ——
提取结果按窗口内容哈希缓存，没变就不重算。改了 `KC_PROMPT_VERSION` 才可能重排。
"""
from __future__ import annotations

import os
import re
from typing import Any, Iterable

from ledger import atomic_write_json, content_hash, load_json
import engine

#: 改这个会让全部 KC 提取缓存失效（与 deepreader 的 PROMPT_VERSION 同思路）。
#: v2：用户实测反馈「太碎 / 了解的事实类太多 / 医学包装要不得」→ 重写 prompt + 加硬过滤。
#: v3：用户建议「让 AI 自己读导览页/学习目标，领会课件思路，把知识点与结构对应并
#:     建立逻辑联系」→ 新增 ①讲次结构提取 ②知识点归属 ③收口串联。
KC_PROMPT_VERSION = 3

#: 每批喂给模型多少页。太小会丢上下文、太大模型会走神且更容易截断。
DEFAULT_WINDOW = 8

TYPE_VALUES = ("concept", "principle", "procedure", "fact")
IMPORTANCE_VALUES = ("must", "key", "freq", "info")

#: **不要的类别**（用户明确要求）：
#:   - type == fact：事实/数据/趣闻，信息密度低
#:   - importance == info：「了解即可」的内容
DROPPED_TYPES = ("fact",)
DROPPED_IMPORTANCE = ("info",)

#: 应用包装（医学/临床/工程/生活）。用户原话「什么物理与医疗不要有」。
#: 这门课是医学类物理，模型很爱加医学外衣 —— prompt 里禁 + 闸门兜底，两层都要。
_APP_RE = re.compile(
    r"医学|临床|骨折|牵引|床垫|血管|神经|患者|病人|手术|治疗|绷带|压疮"
    r"|人工关节|医用|医院|救护车|病房|医生|护理|诊断|生理|人体|义肢|插管|输液"
)

SYSTEM = """你在为一份大学课程讲义建立「知识点骨架」。

知识点 = **一个完整的、既能独立讲解也能独立自测的单元**。它不是一个细节，也不是章节标题。

## 关键：要「厚」，不要「碎」

反面（太碎，禁止）：把「牛顿第二定律」拆成「建立过程」「数学形式」「适用条件」三条。
正面（合适）：合成一条「牛顿第二定律：形式、适用条件与常见误用」。

**一份 8 页的讲义，通常只该产出 3~6 个知识点。宁少而厚，不要多而薄。**

## 一律不要写这些（写了算不合格，会被程序剔除）

1. **科学史、人物、年代、轶事** —— 例：「经典力学诞生的科学史」「伽利略的建模方法」。
2. **应用包装**：医学、临床、工程、生活场景 —— 例：「骨折牵引中的张力控制」
   「压强与减压床垫」「摩擦的医学实例」。
   这门课虽然是医学类物理，但我要的是**物理本身**，不要医学外衣；
   也**不要**在要点里写「在医学上…」「临床对应…」。
3. **纯数据罗列** —— 例：「重力随纬度的变化」「水漏旋汇的半球方向」「某某常量表」。
4. **type 为 fact** —— 「事实/数据/趣闻」信息密度太低。
5. **importance 为 info** —— 「了解即可」的内容一律不要。

## 每个知识点输出

- label      : 不超过 20 字，是一个**知识块**而不是一个细节
- type       : concept（概念定义）| principle（原理/定理/定律）| procedure（方法/步骤/算法）
- importance : must（必须掌握）| key（重要）| freq（常用）
- points     : 3~5 条，**每条都要带实信息** —— 公式、成立条件、适用范围、易错点、
               推导的关键一步。**不要**写「这个概念很重要」这种空话，也**不要**写应用举例。
- page       : 主要出自第几页（**必须是我给出的页号之一**）
- part       : 它属于「本讲结构」里的哪一部分（填 part 的 label 原文；
               没给我结构、或确实不属于任何部分就填 ""）
- deps       : 依赖同批里哪些知识点的 label（填 label 原文；没有就 []）
- is_hub     : 布尔。它是不是被后面反复用到的基础（像「牛顿第二定律」那样）？

只输出 JSON，不要任何解释、不要 markdown 围栏：
{"kcs": [{"label": "...", "type": "principle", "importance": "must",
          "points": ["...", "..."], "page": 10, "deps": [], "is_hub": false}]}

铁律：
1. 只提炼讲义里**真正讲了**的内容，不脑补、不补充课本外的东西。
2. 已经在「本讲已有知识点」里出现过的，**不要重复提炼**。"""


# ---------------------------------------------------------------- 讲次结构（导览/目标）
#
# 用户建议：「让 AI 自己阅读课件，领会课件的思路（本讲导览、学习目标之类的页面），
# 将其与问题对应，并建立知识点之间的逻辑联系。」
#
# 为什么有用：逐窗口提取只能看到 8 页，不知道「这一讲整体在讲什么、分几块、先后如何」，
# 于是知识点只是一堆并列的散点，deps 也只能勉强连上同窗口的邻居。
# 先把课件的**骨架**读出来，后面的提取与串联才有依据。

#: 判定「导览/目标页」的关键词
_OVERVIEW_RE = re.compile(
    r"学习目标|教学目标|本章|本节|目录|导览|重点|难点|要求|小结|概述|框架"
    r"|知识结构|内容提要|本章内容|教学安排|课时"
)

OUTLINE_SYSTEM = """你在读一份大学课程的课件。任务：**先读懂这份课件的思路**，把它的骨架写出来。

我要的不是复述内容，而是**老师是怎么组织这一讲的**：
分几个部分、每部分解决什么问题、先讲什么后讲什么、明确写了哪些学习目标。

输出字段：
- title      : 这一讲的标题（从课件里取，不要自己编）
- objectives : 课件**明确写出**的学习目标/要求（没写就空数组，不要脑补）
- parts      : 这一讲分成的部分，每部分给：
               label（部分名，不超过 15 字）
               from / to（大致覆盖第几页到第几页，用我给的页号）
               summary（这部分解决什么问题，一句话）

只输出 JSON，不要解释、不要 markdown 围栏：
{"title": "...", "objectives": ["..."],
 "parts": [{"label": "...", "from": 6, "to": 19, "summary": "..."}]}

铁律：
1. **以课件的实际组织为准**，不要套用你熟悉的教材目录。
2. 部分数量通常在 **3~6** 个之间；太少没有结构，太多等于没分。
3. 页号必须落在我给出的范围内。"""

LINK_SYSTEM = """你在为一份课程讲义的知识点**建立逻辑联系**。

我给你这一讲的结构（老师是怎么组织的）和已经提取出来的全部知识点。
请判断它们之间的**先后与依赖** —— 学哪个之前必须先懂哪个。

对每个知识点给出：
- part   : 它属于我给的哪个部分（填 part 的 label 原文；实在不属于任何部分就填 ""）
- deps   : 它**直接**依赖哪些知识点（填 id；没有就 []）。
           判据：不懂 deps 里的东西，就没法学懂它。**只填直接前置，不要把整条链都写上。**
- is_hub : 它是不是被后面反复用到的基础（像「牛顿第二定律」那样）
- order  : 建议的学习顺序（把全部 id 排一遍，从先到后）

只输出 JSON：
{"links": [{"id": "L05.1", "part": "...", "deps": [], "is_hub": true}],
 "order": ["L05.1", "L05.2"]}

铁律：
1. **只填真实的直接前置**。宁可少填，不要为了"看起来有结构"而乱连。
2. order 必须包含我给的全部 id，且每个只出现一次。
3. 不要发明新知识点，也不要改 id。"""


def page_headlines(pages: list[dict], max_len: int = 42) -> str:
    """每页头一行的速览 —— 让模型先对全讲有个鸟瞰（很便宜）。"""
    out: list[str] = []
    for p in sorted(pages, key=lambda x: x.get("no", 0)):
        t = (p.get("text") or "").strip()
        if not t:
            continue
        first = t.split("\n")[0].strip()[:max_len]
        if first:
            out.append(f"第{p.get('no')}页: {first}")
    return "\n".join(out)


def overview_pages(pages: list[dict], limit: int = 6) -> list[int]:
    """挑出「导览/目标」类页面。

    命中关键词的优先；一个都没命中就退回最前面几页（很多课件把目标写在开头）。
    """
    hits = [int(p["no"]) for p in pages
            if _OVERVIEW_RE.search(p.get("text") or "")]
    if not hits:
        hits = [int(p["no"]) for p in sorted(pages, key=lambda x: x["no"])[:3]]
    return sorted(set(hits))[:limit]


def outline_text(pages_by_no: dict[int, dict], nos: list[int],
                 per_page_limit: int = 1500) -> str:
    chunks: list[str] = []
    for no in nos:
        t = ((pages_by_no.get(no) or {}).get("text") or "").strip()
        if t:
            chunks.append(f"--- 第 {no} 页 ---\n{t[:per_page_limit]}")
    return "\n\n".join(chunks)


def extract_outline(pages_by_no: dict[int, dict], nos: list[int],
                    all_pages: list[dict]) -> tuple[dict, dict]:
    """读导览页 + 全讲页标题速览 → 讲次结构。返回 (outline, meta)。"""
    user = (f"【导览页 / 学习目标页】\n{outline_text(pages_by_no, nos)}\n\n"
            f"【全讲每一页的头一行（速览，帮你判断组织）】\n"
            f"{page_headlines(all_pages)}\n\n请输出 JSON。")
    obj, meta = engine.chat_json(OUTLINE_SYSTEM, user, max_tokens=8000)
    if not isinstance(obj, dict):
        return {}, meta
    parts = []
    seen: set[str] = set()
    for p in obj.get("parts") or []:
        if not isinstance(p, dict):
            continue
        lb = str(p.get("label") or "").strip()
        if not lb or lb in seen:
            continue
        seen.add(lb)
        try:
            f, t = int(p.get("from")), int(p.get("to"))
        except (TypeError, ValueError):
            continue
        if f > t:
            f, t = t, f
        parts.append({"label": lb, "from": f, "to": t,
                      "summary": str(p.get("summary") or "").strip()})
    return {
        "title": str(obj.get("title") or "").strip(),
        "objectives": [str(x).strip() for x in (obj.get("objectives") or []) if str(x).strip()],
        "parts": parts,
    }, meta


def extract_links(outline: dict, kcs: list[dict]) -> tuple[dict, dict]:
    """收口：把全部知识点串起来 —— 归属部分 + 直接前置依赖 + 枢纽判定 + 学习顺序。"""
    lines: list[str] = []
    for k in kcs:
        first_point = (k.get("points") or [""])[0][:60]
        lines.append(f"{k['id']} | {k['label']} | 第{k.get('page')}页 | {first_point}")
    parts = "、".join(p["label"] for p in (outline.get("parts") or [])) or "（未提取到结构）"
    obj_txt = "；".join(outline.get("objectives") or []) or "（课件没写明确目标）"

    user = (f"【本讲标题】{outline.get('title') or '（未知）'}\n"
            f"【课件写明的学习目标】{obj_txt}\n"
            f"【本讲结构】{parts}\n\n"
            f"【全部知识点】（id | 名称 | 出处页 | 首条要点）\n" + "\n".join(lines)
            + "\n\n请输出 JSON。")
    got, meta = engine.chat_json(LINK_SYSTEM, user, max_tokens=12000)
    return (got if isinstance(got, dict) else {}), meta


def apply_links(kcs: list[dict], links: dict) -> tuple[list[dict], list[str]]:
    """把收口结果套回知识点。只接受**能解析到真实 id** 的依赖，悬空的一律丢。"""
    by_id = {k["id"]: k for k in kcs}
    for row in links.get("links") or []:
        if not isinstance(row, dict):
            continue
        k = by_id.get(str(row.get("id") or ""))
        if not k:
            continue
        k["part"] = str(row.get("part") or "").strip()
        deps = []
        for d in row.get("deps") or []:
            d = str(d).strip()
            if d in by_id and d != k["id"] and d not in deps:
                deps.append(d)
        if deps:
            k["deps"] = deps
        k["is_hub"] = bool(row.get("is_hub"))

    order: list[str] = []
    for i in links.get("order") or []:
        i = str(i).strip()
        if i in by_id and i not in order:
            order.append(i)
    for k in sorted(kcs, key=lambda x: (x.get("page") or 0, x["id"])):
        if k["id"] not in order:
            order.append(k["id"])
    return kcs, order


# ---------------------------------------------------------------- 工具

def lecture_prefix(lecture_id: str) -> str:
    """从讲次名里取一个短前缀当 id 命名空间。

    「05第五讲-动力学1_2026」→「L05」；取不到数字就退回净化后的前若干字符。
    """
    m = re.match(r"\s*0*(\d{1,3})", lecture_id)
    if m:
        return f"L{int(m.group(1)):02d}"
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", lecture_id)
    return (s[:6] or "L")


def window_pages(page_numbers: list[int], size: int = DEFAULT_WINDOW) -> list[list[int]]:
    """把页号切成窗口（连续、不重叠）。"""
    out: list[list[int]] = []
    for i in range(0, len(page_numbers), max(1, size)):
        out.append(page_numbers[i:i + max(1, size)])
    return out


def render_window_text(pages_by_no: dict[int, dict], window: list[int],
                       per_page_limit: int = 1200) -> str:
    """把窗口里的页拼成 prompt 用的正文。"""
    chunks: list[str] = []
    for no in window:
        p = pages_by_no.get(no) or {}
        text = (p.get("text") or "").strip()
        if not text:
            continue
        chunks.append(f"--- 第 {no} 页 ---\n{text[:per_page_limit]}")
    return "\n\n".join(chunks)


def render_questions(annotations: list[dict], window: list[int]) -> str:
    """窗口内用户追问的清单（这是他真正卡住的点）。"""
    lines: list[str] = []
    for a in annotations:
        try:
            pg = int(a.get("page", 0))
        except (TypeError, ValueError):
            continue
        if pg not in window:
            continue
        q = (a.get("question") or "").strip()
        if q:
            lines.append(f"- 第 {pg} 页：{q}")
    if not lines:
        return ""
    return ("\n\n【学生在这一区间问过的问题】\n"
            "（这些是他**真正卡住**的地方，请**优先**把它们提炼成知识点）\n"
            + "\n".join(lines))


# ---------------------------------------------------------------- 质量闸门

def check_kc(kc: dict, valid_pages: set[int]) -> tuple[bool, str]:
    """单个知识点的质量闸门。照着「什么样的知识点算废」定的。

    v2 起加了两条硬过滤（用户实测反馈「太碎、了解的事实类太多」）：
      - `type == fact` 一律丢；
      - `importance == info` 一律丢。
    这不是"模型不该输出"，而是"即使输出了也不要" —— 闸门兜底比只靠 prompt 稳。
    """
    if not isinstance(kc, dict):
        return False, "不是对象"
    label = str(kc.get("label") or "").strip()
    if not label:
        return False, "label 为空"
    if len(label) > 30:
        return False, f"label 太长（{len(label)} 字）"
    if label in ("知识点", "小结", "总结", "本章内容", "以下内容"):
        return False, f"label 太空泛（{label}）"
    if str(kc.get("type")) not in TYPE_VALUES:
        return False, f"type 非法：{kc.get('type')!r}"
    if str(kc.get("importance")) not in IMPORTANCE_VALUES:
        return False, f"importance 非法：{kc.get('importance')!r}"
    if str(kc.get("type")) in DROPPED_TYPES:
        return False, f"不要「{kc.get('type')}」类（信息密度低）"
    if str(kc.get("importance")) in DROPPED_IMPORTANCE:
        return False, f"不要「{kc.get('importance')}」级（了解即可）"
    if _APP_RE.search(label):
        return False, f"label 是应用包装：{label}"
    pts = kc.get("points")
    if not isinstance(pts, list) or not [p for p in pts if str(p).strip()]:
        return False, "points 为空"
    try:
        page = int(kc.get("page"))
    except (TypeError, ValueError):
        return False, f"page 非法：{kc.get('page')!r}"
    if page not in valid_pages:
        return False, f"page {page} 不在本批页号内"
    return True, ""


def strip_applications(kc: dict) -> tuple[dict, list[str]]:
    """去掉要点里的应用包装（医学/临床/工程/生活）。

    prompt 里已经明令禁止，但实测模型仍会夹带 —— 例如
    「弹性力与胡克定律」的要点里塞了一条「医学版本：血管壁的弹性…」。
    这里把这类**单条要点**剔掉；若剔完全没了，整条知识点作废。
    """
    removed: list[str] = []
    kept: list[str] = []
    for p in kc.get("points") or []:
        s = str(p).strip()
        if _APP_RE.search(s):
            removed.append(s[:40])
        else:
            kept.append(s)
    out = dict(kc)
    out["points"] = kept
    return out, removed


def normalize_kcs(raw_kcs: Iterable[dict], lecture_id: str, prefix: str,
                  start_seq: int, label_to_id: dict[str, str]) -> tuple[list[dict], list[str]]:
    """把模型输出规范化成 KC 记录（分配 id、解析 deps、去重）。"""
    out: list[dict] = []
    dropped: list[str] = []
    seq = start_seq
    seen: set[str] = set()

    for kc in raw_kcs or []:
        label = str((kc or {}).get("label") or "").strip()
        if not label or label in seen:
            continue
        seen.add(label)
        points = [str(p).strip() for p in (kc.get("points") or []) if str(p).strip()]
        page = int(kc.get("page"))
        dep_labels = [str(d).strip() for d in (kc.get("deps") or []) if str(d).strip()]
        seq += 1
        kid = f"{prefix}.{seq}"
        out.append({
            "id": kid,
            "label": label,
            "type": str(kc.get("type")),
            "importance": str(kc.get("importance")),
            "points": points[:4],
            "page": page,
            "pages": [page],
            "part": str(kc.get("part") or "").strip(),
            "deps": [],                       # 先放 label，合并阶段再解析成 id
            "_dep_labels": dep_labels,
            "is_hub": bool(kc.get("is_hub")),
            "source_refs": [f"{lecture_id}.pdf/{page}"],
            "questions": [],
        })
        label_to_id[label] = kid
    return out, dropped


def resolve_deps(kcs: list[dict]) -> None:
    """把 `_dep_labels` 解析成 id（只在能解析到时才写 deps，避免指向不存在的东西）。"""
    by_label = {k["label"]: k["id"] for k in kcs}
    for k in kcs:
        ids: list[str] = []
        for lb in k.pop("_dep_labels", []) or []:
            kid = by_label.get(lb)
            if kid and kid != k["id"]:
                ids.append(kid)
        k["deps"] = ids


# ---------------------------------------------------------------- 账本

def kcs_ledger_path(library_root: str) -> str:
    return os.path.join(library_root, ".ledger", "kcs.json")


def load_kcs(library_root: str) -> dict:
    d = load_json(kcs_ledger_path(library_root), None)
    if not d:
        return {"version": 1, "title": "", "chapters": []}
    return d


def save_kcs(library_root: str, data: dict) -> None:
    data["version"] = 1
    atomic_write_json(kcs_ledger_path(library_root), data)


def chapters_of(data: dict) -> dict[str, dict]:
    return {c.get("id"): c for c in data.get("chapters", [])}


def put_lecture(data: dict, lecture_id: str, label: str, kcs: list[dict],
                outline: dict | None = None, order: list[str] | None = None) -> None:
    """把一讲的 KC 写进骨架（覆盖该讲）。"""
    chapters = data.setdefault("chapters", [])
    entry = {"id": lecture_id, "label": label, "kcs": kcs,
             "outline": outline or {}, "order": order or []}
    for i, c in enumerate(chapters):
        if c.get("id") == lecture_id:
            chapters[i] = entry
            return
    chapters.append(entry)


# ---------------------------------------------------------------- 缓存

def cache_path(library_root: str) -> str:
    return os.path.join(library_root, ".ledger", "kc_cache.json")


def _window_key(text: str, questions: str, known: list[str], model: str,
                outline: dict | None = None) -> str:
    return content_hash({"v": KC_PROMPT_VERSION, "text": text, "q": questions,
                         "known": known, "model": model,
                         "outline": outline or {}})


def _load_cache(library_root: str) -> dict:
    return load_json(cache_path(library_root), None) or {"version": 1, "by_key": {}}


def _save_cache(library_root: str, d: dict) -> None:
    d["version"] = 1
    atomic_write_json(cache_path(library_root), d)


# ---------------------------------------------------------------- 提取

def extract_window(text: str, questions: str, known_labels: list[str],
                   outline: dict | None = None) -> tuple[list[dict], dict]:
    """一次窗口提取。返回 (原始 kc 列表, 元信息)。截断会抛错（区分"上限不够"与"写不对"）。"""
    user = f"【讲义内容】\n{text}"
    # 先把课件的结构告诉模型 —— 这样它提取时知道"这一块属于哪一部分"，
    # 而不是把 8 页当成孤立的文本（用户建议：让 AI 领会课件的思路）。
    if outline and outline.get("parts"):
        user += ("\n\n【本讲结构（老师是怎么组织的；请在 part 字段里归属）】\n"
                 + "\n".join(f"- {p['label']}（第 {p['from']}-{p['to']} 页）"
                             f"：{p.get('summary', '')}" for p in outline["parts"]))
    if outline and outline.get("objectives"):
        user += ("\n\n【课件写明的学习目标】\n"
                 + "\n".join(f"- {o}" for o in outline["objectives"]))
    if questions:
        user += questions
    if known_labels:
        user += ("\n\n【本讲已有知识点】（**不要重复提炼**）\n"
                 + "\n".join(f"- {x}" for x in known_labels))
    user += "\n\n请输出 JSON。"

    obj, meta = engine.chat_json(SYSTEM, user, max_tokens=8000)
    if isinstance(obj, dict):
        raw = obj.get("kcs") or []
    elif isinstance(obj, list):
        raw = obj
    else:
        raw = []
    return [k for k in raw if isinstance(k, dict)], meta


def extract_lecture(library_root: str, lecture_id: str, lecture_label: str,
                    pages: list[dict], annotations: list[dict],
                    window: int = DEFAULT_WINDOW, use_cache: bool = True,
                    progress=None) -> tuple[list[dict], dict, list[str]]:
    """把一讲的所有页提炼成知识点。返回 (kcs, outline, 报告行)。

    三步（用户建议的「先领会课件思路，再建立逻辑联系」）：
      ① **先读导览页/学习目标页** → 讲次结构（分几部分、每部分讲什么、学习目标）
      ② 逐窗口提取，把结构喂进去 → 每个知识点归属到某个部分
      ③ **收口串联** → 修正依赖关系、判定枢纽、给出学习顺序
    """
    report: list[str] = []
    pages_by_no = {int(p["no"]): p for p in pages}
    # 没有文字层的页跳过（没东西可提炼）
    usable = [n for n in sorted(pages_by_no)
              if len(((pages_by_no[n].get("text") or "")).strip()) >= 30]
    if not usable:
        return [], {}, [f"[kc   ] {lecture_id}：没有可用文字，跳过"]

    prefix = lecture_prefix(lecture_id)
    cache = _load_cache(library_root)
    model = engine.model_name()

    # ---- ① 讲次结构 ----
    ov_nos = overview_pages(pages)
    outline: dict = {}
    if use_cache and f"outline:{lecture_id}" in cache.get("by_key", {}):
        outline = cache["by_key"][f"outline:{lecture_id}"]["outline"]
        report.append(f"[outline] {lecture_id}：结构命中缓存")
    else:
        try:
            outline, meta = extract_outline(pages_by_no, ov_nos, pages)
            cache.setdefault("by_key", {})[f"outline:{lecture_id}"] = {"outline": outline,
                                                                      "meta": meta}
            parts = outline.get("parts") or []
            report.append(f"[outline] {lecture_id}：读了第 {'、'.join(map(str, ov_nos))} 页"
                          f" → 「{outline.get('title', '')}」分 {len(parts)} 部分"
                          f"（{'、'.join(p['label'] for p in parts)}），"
                          f"{meta.get('tokens', 0)} token")
        except Exception as e:  # noqa: BLE001 - 结构提取失败不该让整讲挂掉
            report.append(f"[warn ] {lecture_id} 结构提取失败（继续逐窗口提取）：{e}")
            outline = {}

    all_kcs: list[dict] = []
    label_to_id: dict[str, str] = {}
    reused = called = 0
    total_tokens = 0
    drop_reasons: list[str] = []
    app_points = 0

    for wi, win in enumerate(window_pages(usable, window), start=1):
        text = render_window_text(pages_by_no, win)
        questions = render_questions(annotations, win)
        known = [k["label"] for k in all_kcs]
        key = _window_key(text, questions, known, model, outline)

        if use_cache and key in cache.get("by_key", {}):
            raw = cache["by_key"][key]["kcs"]
            reused += 1
        else:
            try:
                raw, meta = extract_window(text, questions, known, outline)
            except Exception as e:  # noqa: BLE001
                report.append(f"[warn ] {lecture_id} 第 {win[0]}-{win[-1]} 页提取失败：{e}")
                continue
            called += 1
            total_tokens += meta.get("tokens", 0)
            cache.setdefault("by_key", {})[key] = {"kcs": raw, "meta": meta}

        valid: list[dict] = []
        for kc in raw:
            ok, why = check_kc(kc, set(win))
            if not ok:
                drop_reasons.append(why)
                continue
            cleaned, removed_pts = strip_applications(kc)
            if removed_pts:
                app_points += len(removed_pts)
            if not cleaned.get("points"):
                drop_reasons.append("要点全是应用包装")
                continue
            valid.append(cleaned)
        got, _ = normalize_kcs(valid, lecture_id, prefix, len(all_kcs), label_to_id)
        all_kcs.extend(got)
        if progress:
            progress(wi, len(win))

    resolve_deps(all_kcs)

    # ---- ③ 收口串联：修正依赖、判定枢纽、给学习顺序 ----
    order: list[str] = []
    if all_kcs and outline:
        lkey = f"links:{lecture_id}"
        if use_cache and lkey in cache.get("by_key", {}):
            links = cache["by_key"][lkey]["links"]
            report.append(f"[link ] {lecture_id}：串联命中缓存")
        else:
            try:
                links, lmeta = extract_links(outline, all_kcs)
                cache.setdefault("by_key", {})[lkey] = {"links": links, "meta": lmeta}
                total_tokens += lmeta.get("tokens", 0)
            except Exception as e:  # noqa: BLE001
                report.append(f"[warn ] {lecture_id} 串联失败（保留逐窗口的依赖）：{e}")
                links = {}
        if links:
            all_kcs, order = apply_links(all_kcs, links)
            n_dep = sum(1 for k in all_kcs if k.get("deps"))
            n_part = sum(1 for k in all_kcs if k.get("part"))
            n_hub = sum(1 for k in all_kcs if k.get("is_hub"))
            report.append(f"[link ] {lecture_id}：{n_part}/{len(all_kcs)} 个知识点归属到部分，"
                          f"{n_dep} 个有前置依赖，{n_hub} 个判为枢纽")

    _save_cache(library_root, cache)
    if drop_reasons:
        # 归类汇总（逐条打印太吵）
        from collections import Counter as _C
        def _bucket(r: str) -> str:
            for key_name, pat in (("fact/info 类", r"不要「"),
                                  ("应用包装", r"应用包装"),
                                  ("空泛/太长", r"空泛|太长"),
                                  ("字段非法", r"非法"),
                                  ("页号越界", r"不在本批"),
                                  ("要点问题", r"要点")):
                if re.search(pat, r):
                    return key_name
            return "其他"
        c = _C(_bucket(r) for r in drop_reasons)
        report.append(f"[drop ] {lecture_id}：剔除 {len(drop_reasons)} 条 —— "
                      + "、".join(f"{k} {v}" for k, v in c.most_common()))
    if app_points:
        report.append(f"[strip] {lecture_id}：从要点里剔掉 {app_points} 条应用包装"
                      f"（医学/临床/工程外衣）")
    report.append(f"[kc   ] {lecture_id}：{len(all_kcs)} 个知识点"
                  f"（新算 {called} 批 / 命中缓存 {reused} 批，{total_tokens} token）")
    return all_kcs, outline, report


# ---------------------------------------------------------------- 关联追问

def link_questions(kcs: list[dict], annotations: list[dict], near: int = 2) -> int:
    """把你的追问挂到知识点上。

    这是整个项目最有价值的一步：**知识点 ← 你真正问过的问题**。
    有了它，"这个知识点我卡过"就是可查的事实，而不是记忆。

    匹配策略：**先精确页号，再就近（±near 页）**。
    为什么需要"就近"：知识点会跨页，模型只挑一个锚定页。实测踩到 ——
    用户在第 10 页问「怎么定义收缩的方向」，而合并后的「张力」知识点锚在第 11 页，
    只按精确页匹配就会漏掉这一条。
    """
    n = 0
    for a in annotations:
        try:
            pg = int(a.get("page", 0))
        except (TypeError, ValueError):
            continue
        q = (a.get("question") or "").strip()
        if not q:
            continue

        target = None
        for k in kcs:                                  # 1) 精确匹配
            if pg in (k.get("pages") or []):
                target = k
                break
        if target is None:                             # 2) 就近匹配（取最近的）
            cands = [(abs(int(k.get("page") or 0) - pg), k) for k in kcs
                     if abs(int(k.get("page") or 0) - pg) <= near]
            if cands:
                target = min(cands, key=lambda t: t[0])[1]
        if target is None:
            continue                                   # 3) 附近也没有 → 不乱挂
        target.setdefault("questions", []).append({
            "page": pg, "no": a.get("no"), "q": q,
            "created_at": a.get("created_at", ""),
        })
        n += 1
    return n


def kcs_by_page(kcs: list[dict], page: int) -> list[dict]:
    return [k for k in kcs if page in (k.get("pages") or [])]
