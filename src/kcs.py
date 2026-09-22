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

#: 改这个会让全部 KC 提取缓存失效（与 deepreader 的 PROMPT_VERSION 同思路）
KC_PROMPT_VERSION = 1

#: 每批喂给模型多少页。太小会丢上下文、太大模型会走神且更容易截断。
DEFAULT_WINDOW = 8

TYPE_VALUES = ("concept", "principle", "procedure", "fact")
IMPORTANCE_VALUES = ("must", "key", "freq", "info")

SYSTEM = """你在为一份大学课程讲义建立「知识点骨架」。

知识点 = **可以单独学会、也能单独自测**的最小单元。
它不是章节标题，也不是一句泛泛的概括。

每个知识点输出这些字段：
- label      : 短名称，不超过 20 字，要具体（例：「张力的定义与方向」而不是「力的分析」）
- type       : concept（概念定义）| principle（原理/定理/定律）
               | procedure（方法/步骤/算法）| fact（事实/公式/数据）
- importance : must（必须掌握）| key（重要）| freq（常用）| info（了解即可）
- points     : 2~4 条要点，每条一句话，写清「是什么 / 为什么 / 怎么用」
- page       : 它主要出自第几页（**必须是我给出的页号之一**）
- deps       : 它依赖同批里哪些知识点的 label（填 label 原文；没有就空数组 []）
- is_hub     : 布尔。这个知识点是不是被后面反复用到的基础（像"牛顿第二定律"那样）？

只输出 JSON，不要任何解释、不要 markdown 围栏：
{"kcs": [{"label": "...", "type": "concept", "importance": "must",
          "points": ["...", "..."], "page": 10, "deps": [], "is_hub": false}]}

铁律：
1. **宁少勿滥**。只提炼讲义里**真正讲了**的内容，不要脑补、不要补充课本外的东西。
2. 同一页可以出多个知识点，也可以一个都不出（那页只是过渡的话）。
3. 已经在「本讲已有知识点」里出现过的，**不要重复提炼**。"""


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
    """单个知识点的质量闸门。照着「什么样的知识点算废」定的。"""
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


def put_lecture(data: dict, lecture_id: str, label: str, kcs: list[dict]) -> None:
    """把一讲的 KC 写进骨架（覆盖该讲）。"""
    chapters = data.setdefault("chapters", [])
    for c in chapters:
        if c.get("id") == lecture_id:
            c["label"] = label
            c["kcs"] = kcs
            return
    chapters.append({"id": lecture_id, "label": label, "kcs": kcs})


# ---------------------------------------------------------------- 缓存

def cache_path(library_root: str) -> str:
    return os.path.join(library_root, ".ledger", "kc_cache.json")


def _window_key(text: str, questions: str, known: list[str], model: str) -> str:
    return content_hash({"v": KC_PROMPT_VERSION, "text": text, "q": questions,
                         "known": known, "model": model})


def _load_cache(library_root: str) -> dict:
    return load_json(cache_path(library_root), None) or {"version": 1, "by_key": {}}


def _save_cache(library_root: str, d: dict) -> None:
    d["version"] = 1
    atomic_write_json(cache_path(library_root), d)


# ---------------------------------------------------------------- 提取

def extract_window(text: str, questions: str, known_labels: list[str]) -> tuple[list[dict], dict]:
    """一次窗口提取。返回 (原始 kc 列表, 元信息)。截断会抛错（区分"上限不够"与"写不对"）。"""
    user = f"【讲义内容】\n{text}"
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
                    progress=None) -> tuple[list[dict], list[str]]:
    """把一讲的所有页提炼成 KC 列表。返回 (kcs, 报告行)。"""
    report: list[str] = []
    pages_by_no = {int(p["no"]): p for p in pages}
    # 没有文字层的页跳过（没东西可提炼）
    usable = [n for n in sorted(pages_by_no)
              if len(((pages_by_no[n].get("text") or "")).strip()) >= 30]
    if not usable:
        return [], [f"[kc   ] {lecture_id}：没有可用文字，跳过"]

    prefix = lecture_prefix(lecture_id)
    cache = _load_cache(library_root)
    model = engine.model_name()

    all_kcs: list[dict] = []
    label_to_id: dict[str, str] = {}
    reused = called = 0
    total_tokens = 0

    for wi, win in enumerate(window_pages(usable, window), start=1):
        text = render_window_text(pages_by_no, win)
        questions = render_questions(annotations, win)
        known = [k["label"] for k in all_kcs]
        key = _window_key(text, questions, known, model)

        if use_cache and key in cache.get("by_key", {}):
            raw = cache["by_key"][key]["kcs"]
            reused += 1
        else:
            try:
                raw, meta = extract_window(text, questions, known)
            except Exception as e:  # noqa: BLE001
                report.append(f"[warn ] {lecture_id} 第 {win[0]}-{win[-1]} 页提取失败：{e}")
                continue
            called += 1
            total_tokens += meta.get("tokens", 0)
            cache.setdefault("by_key", {})[key] = {"kcs": raw, "meta": meta}

        valid: list[dict] = []
        for kc in raw:
            ok, why = check_kc(kc, set(win))
            if ok:
                valid.append(kc)
            else:
                report.append(f"[drop ] 第 {win[0]}-{win[-1]} 页：{why}")
        got, _ = normalize_kcs(valid, lecture_id, prefix, len(all_kcs), label_to_id)
        all_kcs.extend(got)
        if progress:
            progress(wi, len(win))

    resolve_deps(all_kcs)
    _save_cache(library_root, cache)
    report.append(f"[kc   ] {lecture_id}：{len(all_kcs)} 个知识点"
                  f"（新算 {called} 批 / 命中缓存 {reused} 批，{total_tokens} token）")
    return all_kcs, report


# ---------------------------------------------------------------- 关联追问

def link_questions(kcs: list[dict], annotations: list[dict]) -> int:
    """把你的追问挂到知识点上（按页号匹配）。

    这是整个项目最有价值的一步：**知识点 ← 你真正问过的问题**。
    有了它，"这个知识点我卡过"就是可查的事实，而不是记忆。
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
        # 挂到「出处页 == 问题页」的知识点上；没有精确匹配就跳过（不乱挂）
        for k in kcs:
            if pg in (k.get("pages") or []):
                k.setdefault("questions", []).append({
                    "page": pg, "no": a.get("no"), "q": q,
                    "created_at": a.get("created_at", ""),
                })
                n += 1
                break
    return n


def kcs_by_page(kcs: list[dict], page: int) -> list[dict]:
    return [k for k in kcs if page in (k.get("pages") or [])]
