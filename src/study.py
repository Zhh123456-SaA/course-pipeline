# -*- coding: utf-8 -*-
"""学习记录 —— 把 HTML 课里的自评、作答、追问、手写解答**收进账本**。

## 为什么必须有这一块

HTML 课里那些交互（三档自评、贴手写解答、框选提问）现在**只活在浏览器的
localStorage 里**。而「账本是唯一真相源」是本项目的铁律之一 ——
一个把学习记录存在浏览器里、导出后无人认领的系统，等于没有记录。

具体缺了什么叫：

1. **掌握度数据**。用户砍掉了「今日学习清单」，根因就是系统压根不知道他学过什么；
   Q17 的结论是"先攒数据"，而数据现在攒在浏览器里。
2. **手写解答**（L2「AI 读你的推导」的输入）。图不落进账本，AI 就读不到。
3. **重复导入的去重**。用户原话：「更新很烦，之前所有内容均重复」——
   每次导出都带全部内容，不做内容哈希去重就会越导越乱。

## 设计

- 账本：`<课程>/.ledger/study.json`（结构化记录）
- 图：`<课程>/study/shots/<sha1>.jpg`（可重建的附件层，与 assets/ 同级）
- 去重：图按**内容 sha1**命名，同一张图重复导入只留一份；
  自评只记**变化**（`grade_log`），所以"什么时候从 ❌ 变成 ✅"是可查的。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re

from ledger import atomic_write_json, load_json


def study_ledger_path(library_root: str) -> str:
    return os.path.join(library_root, ".ledger", "study.json")


def shots_dir(library_root: str) -> str:
    return os.path.join(library_root, "study", "shots")


def load_study(library_root: str) -> dict:
    d = load_json(study_ledger_path(library_root), None)
    if not isinstance(d, dict) or "lessons" not in d:
        return {"version": 1, "lessons": {}}
    d.setdefault("lessons", {})
    return d


def save_study(library_root: str, data: dict) -> None:
    data["version"] = 1
    atomic_write_json(study_ledger_path(library_root), data)


# ---------------------------------------------------------------- 图

_DATA_URL = re.compile(r"^data:image/[a-z+]+;base64,", re.I)


def decode_shot(data_url: str) -> bytes | None:
    """把 `data:image/jpeg;base64,...` 解成字节。坏数据返回 None（不炸整轮）。

    为什么是 None 而不是抛错：导出的 JSON 是浏览器生成的，
    一张图坏掉不该让整批学习记录都进不来。
    """
    if not data_url or not isinstance(data_url, str):
        return None
    s = _DATA_URL.sub("", data_url.strip())
    try:
        return base64.b64decode(s, validate=False)
    except (binascii.Error, ValueError):
        return None


def save_shot(library_root: str, raw: bytes) -> tuple[str, bool]:
    """按内容 sha1 存图。返回 (sha1, 是否新写入)。

    ★ 内容寻址是**去重的关键**：用户每次导出都带全部图，
    同一张重复导入时 sha1 一样，直接命中已有文件，不重复写。
    """
    h = hashlib.sha1(raw).hexdigest()
    d = shots_dir(library_root)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, h + ".jpg")
    if os.path.exists(p):
        return h, False
    # 用二进制原子写（atomic_write_text 是给文本用的）
    tmp = p + ".tmp"
    with open(tmp, "wb") as f:
        f.write(raw)
    os.replace(tmp, p)
    return h, True


# ---------------------------------------------------------------- 导入

def find_exports(path: str) -> list[str]:
    """给一个文件或目录，找出其中所有 `study-*.json`。"""
    if os.path.isfile(path):
        return [path]
    out: list[str] = []
    if os.path.isdir(path):
        for fn in sorted(os.listdir(path)):
            if fn.lower().endswith(".json") and fn.lower().startswith("study-"):
                out.append(os.path.join(path, fn))
    return out


def import_export(library_root: str, data: dict, payload: dict) -> list[str]:
    """把一份导出的作答并进账本。`data` 会被原地修改。

    合并规则（每条都对应一个真实需求）：
      - **自评**：同一个问题以**最新一次**为准；只有值**变了**才追加进 `grade_log`
        （这样"什么时候从 ❌ 变成 ✅"是可查的，而日志又不会无限膨胀）
      - **作答文字**：最新覆盖
      - **追问**：按文字去重后追加（同一句话问两遍只留一条）
      - **手写图**：按内容 sha1 去重
    """
    report: list[str] = []
    lesson_key = str(payload.get("lesson") or "").strip()
    if not lesson_key:
        return ["[warn] 这份导出没有 lesson 字段，跳过"]

    lessons = data.setdefault("lessons", {})
    rec = lessons.setdefault(lesson_key, {})
    rec["title"] = payload.get("title") or rec.get("title") or ""
    rec["mode"] = payload.get("mode") or rec.get("mode") or ""
    rec["last_import_at"] = payload.get("exported_at") or ""

    orig = str(payload.get("exported_at") or "")

    # ---- 自评 ----
    grades = payload.get("grades") or {}
    g_cur = rec.setdefault("grades", {})
    g_log = rec.setdefault("grade_log", [])
    n_new = n_chg = 0
    for k, v in grades.items():
        if not isinstance(k, str) or v not in ("ok", "mid", "no"):
            continue
        old = g_cur.get(k)
        if old == v:
            continue
        g_cur[k] = v
        if old is None:
            n_new += 1
        else:
            n_chg += 1
        g_log.append({"q": k, "from": old, "to": v, "at": orig})
    if grades:
        report.append(f"[study] 自评：新增 {n_new} 条、变化 {n_chg} 条"
                      f"（该节累计 {len(g_cur)}/{len(grades)}）")

    # ---- 作答文字 ----
    answers = payload.get("answers") or {}
    if answers:
        a_cur = rec.setdefault("answers", {})
        a_cur.update({str(k): str(v) for k, v in answers.items()})

    # ---- 追问 ----
    asks = payload.get("asks") or []
    a_list = rec.setdefault("asks", [])
    seen = {str(x.get("q") or "") for x in a_list}
    n_ask = 0
    for x in asks:
        q = str((x or {}).get("q") or "").strip()
        if q and q not in seen:
            a_list.append({"q": q, "t": str((x or {}).get("t") or "")})
            seen.add(q)
            n_ask += 1
    if n_ask:
        report.append(f"[study] 追问：新增 {n_ask} 条")

    # ---- 手写解答图 ----
    shots = payload.get("shots") or []
    s_list = rec.setdefault("shots", [])
    have = {x.get("sha1") for x in s_list}
    added = dup = bad = 0
    for it in shots:
        # 兼容两种形态：新的 {d,n} 与老的纯 data URL 字符串
        if isinstance(it, str):
            body, note = it, ""
        elif isinstance(it, dict):
            body, note = it.get("d") or "", str(it.get("n") or "")
        else:
            bad += 1
            continue
        raw = decode_shot(body)
        if not raw:
            bad += 1
            continue
        sha, _is_new_file = save_shot(library_root, raw)
        # 判重看的是**这节课有没有引用过它**，不是"磁盘上是不是新文件"。
        # 文件本身按内容全局去重（省磁盘、也解决"整本重复导出"）；
        # 但同一张图合法地属于两节课时，两边都该留下引用 ——
        # 按"文件是否新"判重会把第二节课的引用悄悄丢掉（实测踩到）。
        if sha in have:
            dup += 1
            if note:
                for x in s_list:
                    if x.get("sha1") == sha and x.get("note") != note:
                        x["note"] = note
            continue
        s_list.append({"sha1": sha, "note": note, "at": orig,
                       "file": f"study/shots/{sha}.jpg"})
        have.add(sha)
        added += 1
    if shots:
        report.append(f"[study] 手写解答：新增 {added} 张、"
                      f"重复跳过 {dup} 张" + (f"、坏数据 {bad} 条" if bad else ""))
    return report


def merge_file(library_root: str, path: str) -> list[str]:
    """导入一个文件或一个目录。返回报告行。"""
    files = find_exports(path)
    if not files:
        return [f"[warn] 在 {path} 下没找到 study-*.json（导出按钮生成的那种）"]
    report: list[str] = []
    data = load_study(library_root)
    for f in files:
        # ★ 这里必须自己兜住异常：`load_json` 遇到坏 JSON 是**抛错**的
        #   （这是对的 —— 账本自己损坏时必须大声报错，不能静默返回默认值）。
        #   但导出的 JSON 是浏览器生成的"外来数据"，一个坏文件不该让整批进不来。
        #   实测：第一版没兜，坏文件直接把整轮导入炸掉。
        try:
            payload = load_json(f, None)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
            report.append(f"[warn] {os.path.basename(f)} 读不出来，跳过："
                          f"{type(e).__name__}")
            continue
        if not isinstance(payload, dict):
            report.append(f"[warn] {os.path.basename(f)} 不是一份作答导出（顶层不是对象），跳过")
            continue
        lines = import_export(library_root, data, payload)
        report.append(f"[file ] {os.path.basename(f)}")
        report += [f"        {x}" for x in lines]
    save_study(library_root, data)
    n_lesson = len(data["lessons"])
    n_shot = sum(len(v.get("shots") or []) for v in data["lessons"].values())
    n_grade = sum(len(v.get("grades") or {}) for v in data["lessons"].values())
    report.append(f"[done ] 学习记录：{n_lesson} 节课 · {n_grade} 条自评 · {n_shot} 张手写图"
                  f"（已写入 .ledger/study.json）")
    return report


# ---------------------------------------------------------------- 统计

def summarize(library_root: str) -> list[str]:
    """给 check 用：一眼看出攒了多少数据、掌握度如何。"""
    data = load_study(library_root)
    lessons = data.get("lessons") or {}
    if not lessons:
        return ["学习记录：还没有（在 HTML 课里自评/作答后点「导出我的作答」，再 import）"]
    cnt = {"ok": 0, "mid": 0, "no": 0}
    for v in lessons.values():
        for g in (v.get("grades") or {}).values():
            if g in cnt:
                cnt[g] += 1
    tot = sum(cnt.values())
    pct = (cnt["ok"] * 100 // tot) if tot else 0
    out = [f"学习记录：{len(lessons)} 节课 · {tot} 条自评"
           f"（会 {cnt['ok']} / 不确定 {cnt['mid']} / 不会 {cnt['no']}，会 {pct}%）"]
    for k, v in sorted(lessons.items()):
        g = v.get("grades") or {}
        n_ok = sum(1 for x in g.values() if x == "ok")
        out.append(f"  {v.get('title') or k}：{len(g)} 条自评"
                   f"（会 {n_ok}）· 追问 {len(v.get('asks') or [])} · "
                   f"手写 {len(v.get('shots') or [])} 张")
    return out
