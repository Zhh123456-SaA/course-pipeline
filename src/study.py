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
    """整份覆盖写。

    ⚠️ **不要拿过期的内存副本调它。** `merge_file` / `import_export` 是自己
    `load → 改 → save` 的；它们跑完之后，你手上那份 `data` 就是过期的，
    再 `save_study` 会把期间的写入**整段抹掉**（实测踩到：测试里拿旧副本写，
    把先前合并进去的两节覆盖没了）。
    要改账本，要么用 `merge_file`，要么先 `load_study` 拿最新的再改。
    """
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

    # ---- 过课件的标记与提问（划线信号）----
    # 用户原话：「我希望我可以先看课件，**划线记笔记问问题**」。
    # 划线与提问是两种不同的信号：划线 = 「这里重要」（他的判断，零成本，量最大）；
    # 提问 = 「这里我不懂」（他的缺口，要打字，量小）。两种都收下。
    marks = payload.get("marks") or []
    if marks:
        m_list = rec.setdefault("marks", [])
        seen_m = {(x.get("p"), tuple(x.get("r") or []), x.get("q") or "") for x in m_list}
        m_add = m_dup = m_bad = 0
        for m in marks:
            if not isinstance(m, dict):
                m_bad += 1
                continue
            try:
                pg = int(m.get("p"))
            except (TypeError, ValueError):
                m_bad += 1
                continue
            r = m.get("r") or []
            if not (isinstance(r, list) and len(r) == 4):
                m_bad += 1
                continue
            try:
                r = [round(float(v), 1) for v in r]
            except (TypeError, ValueError):
                m_bad += 1
                continue
            q = str(m.get("q") or "").strip()
            key = (pg, tuple(r), q)
            if key in seen_m:
                m_dup += 1
                continue
            m_list.append({"p": pg, "r": r, "q": q, "at": str(m.get("t") or orig)})
            seen_m.add(key)
            m_add += 1
        report.append(f"[study] 课件标记：新增 {m_add} 处"
                      f"（累计提问 {sum(1 for x in m_list if x.get('q'))} 条）、"
                      f"重复跳过 {m_dup} 处"
                      + (f"、坏数据 {m_bad} 条" if m_bad else ""))
    return report


def _now() -> str:
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def append_ladder(library_root: str, lesson: str, page: int, question: str,
                  answer: str, stalls: int = 0, gave_answer: bool = False,
                  tid: str = "", stall: bool = False) -> None:
    """把**一轮追问的来回**记进账本。

    用户原话：「那我在**哪里查看**我和 ai 的交互和反问呢」——
    在这之前，阶梯的来回只活在浏览器的一个小面板里（关掉就没了），
    服务端也只存在内存里（重启就没），**一个字都没进账本**。
    那就又变回"读完就忘"了 —— 正是加阶梯要治的病。

    记「他问了什么 + AI 回了什么」，按时间追加；同样的 (页, 对话, 问题, 回答) 不重复记。

    `tid` 是**子对话编号**（用户原话：「我希望制作成**子对话**的形式……可以之后再调出来读」）。
    光有平铺的来回列表，读起来是一串孤立的问答；带上 tid，同一段对话的
    来回就能重新拼回去，几个月后点开还能从头读到尾、还能接着往下问。
    """
    lesson = (lesson or "").strip()
    if not lesson or not (question or "").strip():
        return
    data = load_study(library_root)
    rec = data.setdefault("lessons", {}).setdefault(lesson, {})
    lst = rec.setdefault("ladder", [])
    key = (int(page or 0), (tid or "").strip(), question.strip(),
           (answer or "").strip())
    for x in lst:
        if (x.get("p"), x.get("tid") or "", x.get("q"), x.get("a")) == key:
            return
    lst.append({"p": int(page or 0), "tid": (tid or "").strip(),
                "q": question.strip(), "a": (answer or "").strip(),
                "stalls": int(stalls or 0), "gave_answer": bool(gave_answer),
                "stall": bool(stall), "at": _now()})
    save_study(library_root, data)


def _study_roots(root: str) -> list[str]:
    """把 `root` 解析成"真有 study 账本的那些目录"。

    `root` 既可以是**课程目录**（含 `.ledger/`），也可以是**库根目录** ——
    后者会把每门课都扫一遍。为什么要兼容：实测踩到一次"读回来是空的"——
    服务端在没传 course 时把库根当成了课程目录，于是去找
    `D:\\学习库\\.ledger\\study.json`（不存在），而账本其实在
    `D:\\学习库\\<课>\\.ledger\\study.json`。
    """
    if os.path.isfile(study_ledger_path(root)):
        return [root]
    out: list[str] = []
    if os.path.isdir(root):
        for c in sorted(os.listdir(root)):
            d = os.path.join(root, c)
            if os.path.isdir(d) and os.path.isfile(study_ledger_path(d)):
                out.append(d)
    return out


def lessons_of(root: str) -> list[str]:
    """账本里记过内容的**节 id**（课程目录或库根都行）。"""
    out: list[str] = []
    for r in _study_roots(root):
        out.extend((load_study(r).get("lessons") or {}).keys())
    return sorted(set(out))


def stem_of(lesson: str) -> str:
    """从 lesson id 里抠出**源文件 stem**（讲次笔记的文件名就是它）。

    lesson id 的形状：`<课程>:<stem>:<部分>:<课型>`；
    「过课件」那种整份的没有"部分"那一段，是 `<课程>:<stem>:<stem>:survey`。
    这里只取第 2 段 —— 调用处会拿它去 `sources` 里核对，**对不上就跳过**，
    所以猜错的代价只是"那段对话没出现在笔记里"，不会写错地方。
    """
    parts = (lesson or "").split(":")
    return parts[1] if len(parts) >= 2 else ""


def threads_of(root: str, lesson: str = "") -> list[dict]:
    """把追问来回**按子对话拼回去**，最新的排最前。

    返回的每一段：
      `{tid, lesson, page, title, n, at, at_end, gave_answer, turns:[{q,a,at,stalls,gave_answer}]}`

    老账本里没有 `tid` 的那几条（功能上线前记的）按**页**归段 ——
    这不是凑数：老界面的会话键就是 `节:第N页`，一页本来就只有一段对话流。
    硬要按 tid 拆成一条一段的话，以前 8 轮对话会变成 8 张单轮卡片，
    比平铺列表还难读。
    """
    rows = ladder_of(root, lesson)
    groups: dict[tuple, list[dict]] = {}
    order: list[tuple] = []
    for x in rows:
        tid = (x.get("tid") or "").strip()
        key = (x.get("lesson") or "", tid) if tid else \
              (x.get("lesson") or "", f"@p{x.get('p') or 0}")
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(x)

    out: list[dict] = []
    for key in order:
        g = sorted(groups[key], key=lambda x: (x.get("at") or "", x.get("p") or 0))
        first = g[0]
        turns = [{"q": x.get("q") or "", "a": x.get("a") or "",
                  "at": x.get("at") or "", "stalls": x.get("stalls") or 0,
                  "stall": bool(x.get("stall")),
                  "gave_answer": bool(x.get("gave_answer"))} for x in g]
        out.append({
            "tid": key[1], "lesson": first.get("lesson") or "",
            "page": first.get("p") or 0,
            "title": (first.get("q") or "").strip(),
            "n": len(turns), "at": first.get("at") or "",
            "at_end": g[-1].get("at") or "",
            "gave_answer": any(t["gave_answer"] for t in turns),
            "turns": turns})
    out.sort(key=lambda t: (t["at_end"], t["at"]))
    out.reverse()
    return out


def ladder_of(root: str, lesson: str = "") -> list[dict]:
    """取出追问来回（给了 lesson 就只取那一节）。

    `root` 既可以是**课程目录**（含 `.ledger/`），也可以是**库根目录** ——
    后者会把每门课都扫一遍（见 `_study_roots`）。
    """
    out: list[dict] = []
    for r in _study_roots(root):
        data = load_study(r)
        for k, v in (data.get("lessons") or {}).items():
            if lesson and k != lesson:
                continue
            for x in v.get("ladder") or []:
                out.append(dict(x, lesson=k))
    out.sort(key=lambda x: (x.get("at") or "", x.get("p") or 0))
    return out


def marks_by_page(library_root: str) -> dict[int, dict]:
    """跨全部小节汇总「每页被标记了几处、其中几条是提问」。

    这是**第一遍的产出支配后面流程**的依据：知识点会标注"你在这一页标记过 N 处"，
    问题清单可以按标记数排序 ——「你划过线的地方」比"页序"更能说明该先学什么。
    """
    data = load_study(library_root)
    out: dict[int, dict] = {}
    for v in (data.get("lessons") or {}).values():
        for m in v.get("marks") or []:
            try:
                pg = int(m.get("p"))
            except (TypeError, ValueError):
                continue
            d = out.setdefault(pg, {"n": 0, "q": 0, "marks": []})
            d["n"] += 1
            if m.get("q"):
                d["q"] += 1
            d["marks"].append(m)
    return out


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
    # 划线信号单列 —— 它是数量最大、门槛最低的一类，别混在自评里
    bp = marks_by_page(library_root)
    if bp:
        n_m = sum(v["n"] for v in bp.values())
        n_q = sum(v["q"] for v in bp.values())
        top = sorted(bp.items(), key=lambda kv: -kv[1]["n"])[:5]
        out.append(f"  课件标记：{n_m} 处 · 涉及 {len(bp)} 页 · 其中提问 {n_q} 条"
                   f"（标记最多的页："
                   + "、".join(f"第 {p} 页×{v['n']}" for p, v in top) + "）")
    for k, v in sorted(lessons.items()):
        g = v.get("grades") or {}
        n_ok = sum(1 for x in g.values() if x == "ok")
        out.append(f"  {v.get('title') or k}：{len(g)} 条自评"
                   f"（会 {n_ok}）· 追问 {len(v.get('asks') or [])} · "
                   f"手写 {len(v.get('shots') or [])} 张 · "
                   f"课件标记 {len(v.get('marks') or [])} 处")
    return out
