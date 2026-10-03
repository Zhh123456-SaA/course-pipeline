# -*- coding: utf-8 -*-
"""学习工作台的测试（纯函数，离线跑）。

用户原话：「让我可以直接进入这个工作台，选择学习什么东西而不是全都要回归 DSH」。
工作台要回答三个每天都会问的问题：**上次学到哪 → 有什么可学 → 学完的在哪**。
这里盯的就是这三件事的数据对不对，以及「课程」和「专题」有没有分对。

跑法：python tests/test_workbench.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROJ, "src"))
sys.path.insert(0, HERE)
import workbench as W  # noqa: E402

FAILS: list[str] = []
PASSES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(f"{name}{(' — ' + detail) if detail else ''}")


LIB = os.path.join(tempfile.gettempdir(), "wb-test", str(os.getpid()))
shutil.rmtree(LIB, ignore_errors=True)


def _w(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _wj(path: str, obj) -> None:
    _w(path, json.dumps(obj, ensure_ascii=False))


# ---- 一门普通课程：有原件、两节课页（过课件 + 先问后看）、4 轮对话、3 张卡 ----
_wj(f"{LIB}/生物/.ledger/source.json",
    {"version": 1, "sources": {"ch": {"page_count": 132, "file": "c.pptx"}}})
_wj(f"{LIB}/生物/.ledger/kcs.json", {"version": 1, "chapters": [
    {"id": "ch", "kcs": [{"id": "K1", "label": "细胞膜"}, {"id": "K2", "label": "脂筏"}]}]})
_wj(f"{LIB}/生物/.ledger/study.json", {"version": 1, "lessons": {
    "生物:ch:ch:survey": {"ladder": [
        {"p": 27, "tid": "m27-1_2_3_4", "q": "脂筏？", "a": "你先说成分。", "at": "2026-09-29 22:23"},
        {"p": 27, "tid": "m27-1_2_3_4", "q": "不知道", "a": "缩小一点。", "at": "2026-09-29 22:24"},
        {"p": 56, "tid": "m56-1", "q": "这表什么意思？", "a": "讲完了。", "at": "2026-10-02 23:00"},
    ]}}})
_wj(f"{LIB}/生物/.ledger/cards.json", {"version": 1, "by_id": {
    "a": {}, "b": {}, "c": {}}})
_w(f"{LIB}/生物/lessons/过课件.html", '<body data-mode="survey" data-lesson="x">')
_w(f"{LIB}/生物/lessons/第一节.html", '<body data-mode="ask-first" data-lesson="y">')

# ---- 一个专题：有知识点骨架、**没有原件**（专题的定义就是这个）----
_wj(f"{LIB}/架构设计/.ledger/kcs.json", {"version": 1, "chapters": [
    {"id": "arch", "kcs": [{"id": f"A{i}", "label": f"单元{i}"} for i in range(1, 9)]}]})

# ---- 一个空目录（应当被跳过）----
os.makedirs(f"{LIB}/没账本也没课页", exist_ok=True)

# ---------------------------------------------------------------- 1 扫库与分类

d = W.scan(LIB)
names = [c["name"] for c in d["courses"]]
check("★ 课程与专题分开了（有原件=课程；只有骨架、没原件=专题）",
      names == ["生物"] and [t["name"] for t in d["topics"]] == ["架构设计"],
      f"courses={names} topics={[t['name'] for t in d['topics']]}")
check("  空目录被跳过（不显示成空课程）", "没账本也没课页" not in names)
check("★ 课程统计取自账本：页数 / 知识点 / 对话段数 / 轮数 / 卡片",
      d["courses"][0]["pages"] == 132 and d["courses"][0]["kcs"] == 2
      and d["courses"][0]["threads"] == 2 and d["courses"][0]["turns"] == 3
      and d["courses"][0]["cards"] == 3,
      json.dumps({k: d["courses"][0][k] for k in
                  ("pages", "kcs", "threads", "turns", "cards")}, ensure_ascii=False))
check("  专题的知识点也数得出来", d["topics"][0]["kcs"] == 8)
check("  卡片总数是各课相加", d["cards"] == 3)

# ---------------------------------------------------------------- 2 继续上次

h = W.home_data(LIB)
r = h["resume"]
check("★ 「继续上次」取**账本里最近动手**的那门课/那一页",
      r and r["course"] == "生物" and r["page"] == 56 and r["at"] == "2026-10-02 23:00",
      json.dumps(r, ensure_ascii=False))
import urllib.parse as _up  # noqa: E402
check("★ 链接优先给**过课件**那一节（它覆盖全部页，锚点才存在）",
      _up.unquote(r["url"]).endswith("过课件.html#p56"), r["url"][-40:])
check("  没有学习记录时不硬编一个「继续」（专题还没开始学 → 没有 resume）",
      W.home_data(os.path.join(LIB, "架构设计"))["resume"] is None,
      str(W.home_data(os.path.join(LIB, "架构设计"))["resume"]))

# ---------------------------------------------------------------- 3 渲染

html = W.render_home(h)
for needle, what in (("学习工作台", "标题"), ("继续上次", "继续那一块"),
                     ("我的课程", "课程栏"), ("专题学习", "专题栏"),
                     ("复习", "复习栏"), ("#p56", "继续的链接"),
                     ("/_c/%E7%94%9F%E7%89%A9", "课程卡链接")):
    check(f"★ 首页有{what}", needle in html)
check("  没有专题时给一句人话（不是空白栏）",
      "还没有专题" in W.render_home({"courses": [], "topics": [], "cards": 0,
                                     "resume": None}))
check("  课程名/路径里的尖括号会被转义（不把用户的东西当 HTML 执行）",
      "&lt;x&gt;" in W.render_home(
          {"courses": [dict(d["courses"][0], name="<x>", lessons=[])], "topics": [],
           "cards": 0, "resume": None}))

cp = W.render_course({}, d["courses"][0])
check("★ 课程页：家底统计 + 各节课页入口 + 能回工作台",
      "这门课的家底" in cp and "过课件" in cp and "先问后看" in cp
      and "回工作台" in cp)
check("  课页列表里过课件排最前（那是最常用的入口）",
      cp.index("过课件") < cp.index("先问后看"))

# ---------------------------------------------------------------- 4 极端情况

check("  空库不炸", "学习工作台" in W.render_home(W.home_data(LIB + "-不存在")))
_w(f"{LIB}/坏账本/.ledger/kcs.json", "{ 这不是 json")          # 语法坏的
_wj(f"{LIB}/坏账本2/.ledger/kcs.json", ["不是对象"])            # 合法 JSON 但不是对象
check("★ 坏账本不炸（工作台不能因为一门课的数据烂掉就打不开）",
      isinstance(W.scan(LIB), dict))
check("  合法但**不是对象**的账本也当「没有」处理",
      W._load(f"{LIB}/坏账本2/.ledger/kcs.json") == {})

shutil.rmtree(LIB, ignore_errors=True)

# ---------------------------------------------------------------- 汇总

for p in PASSES:
    print("PASS  " + p)
for f_ in FAILS:
    print("FAIL  " + f_)
print("=" * 60)
print(f"通过 {len(PASSES)} / 失败 {len(FAILS)}")
raise SystemExit(1 if FAILS else 0)
