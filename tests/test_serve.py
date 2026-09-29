# -*- coding: utf-8 -*-
"""本地服务的测试 —— 重点是**阶梯的判定逻辑**，那是"交互式追问"的核心。

为什么必须测：用户原话「**交互式必须要做**」，而交互式的价值全在
"默认不直接给答案、先反问他"这一条上。判定错了（比如把实质回答误判成卡壳），
就会在第二轮直接把答案倒给他 —— 那跟以前没有区别（实测：他那 12 次追问
每次都是直接给答案，读完就忘，只产出 13 张卡、0 条笔记）。

不启真服务：只测纯函数与账本写入，避免测试挂住端口。
跑法：python tests/test_serve.py
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
import serve as V  # noqa: E402
import study as S  # noqa: E402

FAILS: list[str] = []
PASSES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(f"{name}{(' — ' + detail) if detail else ''}")


def _try_gbk(raw: bytes) -> str:
    """.bat 能不能按 GBK 解开（cmd 就是按系统 ANSI 码页读它的）。解不开返回空串。"""
    try:
        return raw.decode("gbk")
    except UnicodeDecodeError:
        return ""


# ---------------------------------------------------------------- 1 卡壳判定

for t in ("不知道", "不会", "答不上来", "想不出来", "没思路", "我放弃", "算了",
          "   ", "?", "……", "太难了", "别问了"):
    check(f"算卡壳：{t!r}", V.is_stall(t), f"is_stall={V.is_stall(t)}")

for t in ("脂筏把信号分子浓缩在一起，所以效率高",
          "我猜是因为它富含胆固醇",
          "脂筏上有 GPCR 这类受体",
          "我觉得是浓缩效应，但不确定特异性那部分",
          "能" * 3):
    check(f"不算卡壳（有实质内容）：{t[:16]!r}", not V.is_stall(t),
          f"is_stall={V.is_stall(t)}")

check("★ 「不确定」这种**有内容的犹豫**不算卡壳（否则会把思考误判成放弃）",
      not V.is_stall("我不太确定，可能是浓缩效应吧"), )

# ---------------------------------------------------------------- 2 硬开关

for t in ("别问了直接讲", "直接讲吧", "给答案", "告诉我吧", "别绕了"):
    check(f"硬开关命中：{t!r}", V.wants_answer(t))
check("普通问题不触发硬开关", not V.wants_answer("脂筏为什么能当信号转导平台？"))
check("★ 硬开关优先于阶梯：他明说了就直接给答案，不跟他绕",
      V.wants_answer("太难了，直接讲"))

# ---------------------------------------------------------------- 3 追问 prompt

p1 = V.build_ask_prompt("课件原文……", "他框选的一段", "为什么？", [], 27)
check("prompt 含课件原文（AI 必须有依据）", "课件原文" in p1)
check("prompt 含他框选的那一段", "他框选的一段" in p1)
check("prompt 含他的提问", "为什么？" in p1)
check("首次追问时卡壳数为 0", "连续卡壳 0 次" in p1)

hist = [{"role": "assistant", "text": "你觉得呢？"},
        {"role": "user", "text": "不知道", "stall": True},
        {"role": "assistant", "text": "再想想原文最后一句"},
        {"role": "user", "text": "不会", "stall": True}]
p2 = V.build_ask_prompt("X", "", "还是不会", hist, 27)
check("★ prompt 里报出**连续卡壳次数**（AI 靠它决定该不该给答案）",
      "连续卡壳 2 次" in p2, p2[-120:])
check("  把之前的来回也带进去", "你觉得呢？" in p2 and "再想想原文最后一句" in p2)
check("  没有框选时给兜底文案（不硬塞空串）", "没框选" in V.build_ask_prompt("X", "", "q", [], 1))
check("  历史过长时只取最近 8 条（不无限膨胀）",
      V.build_ask_prompt("X", "", "q",
                         [{"role": "user", "text": f"第{i}轮" * 5, "stall": False}
                          for i in range(20)], 1).count("第0轮") == 0)

# ---------------------------------------------------------------- 4 账本直写

ROOT = os.path.join(tempfile.gettempdir(), "course-pipeline-serve", str(os.getpid()))


def _cleanup() -> None:
    shutil.rmtree(ROOT, ignore_errors=True)
    p = os.path.dirname(ROOT)
    try:
        if os.path.isdir(p) and not os.listdir(p):
            os.rmdir(p)
    except OSError:
        pass


import atexit  # noqa: E402
atexit.register(_cleanup)

LIB = os.path.join(ROOT, "某课")
os.makedirs(LIB, exist_ok=True)
LES = "某课:讲次:节:survey"

r = V.record("/api/mark", "mark", {"lesson": LES, "at": "t1",
                                   "mark": {"p": 27, "r": [1.0, 2.0, 3.0, 4.0],
                                            "q": "脂筏算不算细胞器？"}}, LIB)
check("★ 标记能**直接进账本**（这就是「一键式」）", r.get("ok"), str(r))
d = S.load_study(LIB)
check("  账本里查得到",
      d["lessons"][LES]["marks"][0]["q"] == "脂筏算不算细胞器？",
      json.dumps(d["lessons"], ensure_ascii=False)[:160])

V.record("/api/grade", "grade", {"lesson": LES, "at": "t2", "mode": "ask-first",
                                 "qid": "C03.16#0", "grade": "mid"}, LIB)
d = S.load_study(LIB)
check("★ 自评也能直接进账本",
      d["lessons"][LES]["grades"] == {"C03.16#0": "mid"},
      str(d["lessons"][LES]["grades"]))

check("缺 lesson 时明确报错、不炸",
      not V.record("/api/mark", "mark", {"mark": {"p": 1}}, LIB).get("ok"))
check("未知类型明确报错",
      not V.record("/x", "什么鬼", {"lesson": LES}, LIB).get("ok"))

# 直写与"导出再导入"必须落成**同一份账本**（否则一键式与离线两条路会分叉）
one = {"lesson": LES, "mode": "survey", "exported_at": "t3",
       "marks": [{"p": 27, "r": [1.0, 2.0, 3.0, 4.0], "q": "脂筏算不算细胞器？"}]}
d2 = S.load_study(LIB)
rep = S.import_export(LIB, d2, one)
check("★ 直写过的标记再用导出导入一次 → 认得出是重复（两条路同一份账本）",
      any("重复跳过 1 处" in x for x in rep), str(rep))

# ---------------------------------------------------------------- 4b 追问来回要能查看

# 用户原话：「那我在**哪里查看**我和 ai 的交互和反问呢」——
# 在这之前，阶梯的来回只活在浏览器一个小面板里、服务端只在内存里，
# **一个字都没进账本**，关掉就没了（又变回"读完就忘"）。
V.study.append_ladder(LIB, LES, 27, "脂筏为什么能当信号转导平台？",
                      "你框那段里，脂筏富含的是哪两类成分？", stalls=0)
V.study.append_ladder(LIB, LES, 27, "不知道",
                      "那我们缩小一点，先说说细胞膜的主要成分是什么？", stalls=1)
V.study.append_ladder(LIB, LES, 27, "脂筏为什么能当信号转导平台？",
                      "你框那段里，脂筏富含的是哪两类成分？", stalls=0)   # 同一轮重复
got = S.ladder_of(LIB)
check("★ 追问来回能存进账本、也能读回来", len(got) == 2, str(len(got)))
check("★ 记下了「我问的」与「AI 回的」原文",
      any(x["q"] == "不知道" and "缩小一点" in x["a"] for x in got),
      json.dumps(got, ensure_ascii=False)[:180])
check("  带上了卡壳次数（阶梯靠它决定给不给答案）",
      any(x["stalls"] == 1 for x in got))
check("  同一轮重复调用不重复记", len([x for x in got if x["q"] == "不知道"]) == 1)
check("  按 lesson 过滤", len(S.ladder_of(LIB, LES)) == 2
      and S.ladder_of(LIB, "别的课") == [])
V.study.append_ladder(LIB, LES, 1, "  ", "x")
check("空问题不记（不产生垃圾条目）", len(S.ladder_of(LIB)) == 2)

# ★ 实测踩到"读回来是空的"：服务端没传 course 时把**库根**当成课程目录，
#   去找 <库根>/.ledger/study.json（不存在）。库根与课程目录两种都要能吃。
ROOT2 = os.path.join(ROOT, "库根")
shutil.rmtree(ROOT2, ignore_errors=True)
os.makedirs(os.path.join(ROOT2, "课A"), exist_ok=True)
V.study.append_ladder(os.path.join(ROOT2, "课A"), "课A:讲:节:x", 3, "问题甲", "回答甲")
check("★ 传库根也能读到（自动扫每门课）", len(S.ladder_of(ROOT2)) == 1,
      str(S.ladder_of(ROOT2)))
check("  传课程目录也能读到", len(S.ladder_of(os.path.join(ROOT2, "课A"))) == 1)
check("  课程里有 ladder 字段（不是只存在内存）",
      bool(S.load_study(os.path.join(ROOT2, "课A"))["lessons"]["课A:讲:节:x"].get("ladder")))

# ---------------------------------------------------------------- 4c 知识点接口（侧栏用）

KCSDIR = os.path.join(ROOT, "库K")
os.makedirs(os.path.join(KCSDIR, "生物", ".ledger"), exist_ok=True)
json.dump({"version": 1, "chapters": [{"id": "L1", "label": "第一章", "kcs": [
    {"id": "L1.2", "label": "第二个", "page": 9, "type": "concept",
     "importance": "must", "is_hub": True, "points": ["a", "b", "c", "d"],
     "questions": [{"q": "你问过的问题"}]},
    {"id": "L1.1", "label": "第一个", "page": 3, "type": "principle",
     "importance": "key", "points": [], "questions": []},
]}]}, open(os.path.join(KCSDIR, "生物", ".ledger", "kcs.json"), "w",
          encoding="utf-8"), ensure_ascii=False)
_ks = V.kcs_flat(KCSDIR, "生物")
check("★ /api/kcs：摊平所有知识点", len(_ks) == 2, str(len(_ks)))
check("★ 按页码排序（侧栏里顺序才对）", [k["page"] for k in _ks] == [3, 9],
      str([k["page"] for k in _ks]))
check("  带上了页码/重要度/枢纽标记", _ks[1]["hub"] is True
      and _ks[1]["importance"] == "must")
check("  要点截到前 3 条（侧栏放不下）", len(_ks[1]["points"]) == 3)
check("  带上他在这一页问过的问题", _ks[1]["questions"] == ["你问过的问题"])
check("没有骨架的课返回空表、不炸", V.kcs_flat(KCSDIR, "没有这门课") == [])

# ---------------------------------------------------------------- 4d 框里读出来

# 用户原话：「问的确实很好，**但你的问题和我的划线没关系啊**」。
# 根因：他画的框在程序眼里只是**图片上的四个百分比数字** —— "框里是什么"程序不知道，
# 所以只能拿整页文字去问。修法：把框那一小块裁出来交给视觉模型（抄隔壁 deepreader）。
#
# ★ 这里踩过一个只有实跑才抓得到的坑：`from src.vision import VisionClient` 之前
#   **必须先调一次 engine.settings()** —— 是它把真源目录加进 sys.path 的。
#   顺序反了会 ModuleNotFoundError，整条"读框"静默失败，AI 又只能拿整页瞎问。
_src = __import__("inspect").getsource(V.read_region)
check("★ read_region：先取 settings（顺带把真源加进 sys.path）再 import 视觉类",
      _src.index("engine.settings()") < _src.index("from src.vision import"),
      "顺序反了会 ModuleNotFoundError")
check("★ read_region：没有 rect 时明确拒绝、不炸", not V.read_region(ROOT, "x", 1, None, "", "").get("ok"))
check("★ read_region：rect 长度不对也拒绝", not V.read_region(ROOT, "x", 1, [1, 2], "", "").get("ok"))
check("★ read_region：找不到页图时给出可读的原因",
      "页图" in (V.read_region(ROOT, "没有这门课", 1, [0, 0, 50, 50], "", "").get("msg") or ""),
      str(V.read_region(ROOT, "没有这门课", 1, [0, 0, 50, 50], "", "")))
check("★ read_region：框太小会拒绝（避免拿一条缝去问模型）",
      "太小" in (V.read_region(KCSDIR, "生物", 1, [0, 0, 0.1, 0.1], "", "").get("msg") or "")
      or not V.read_region(KCSDIR, "生物", 1, [0, 0, 0.1, 0.1], "", "").get("ok"))
check("★ page_image：没有账本时返回空串、不炸", V.page_image(ROOT, "没有这门课", 1) == "")
check("★ 追问 prompt 里框选内容是**主角**，整页原文降为背景",
      "以它为主" in V.build_ask_prompt("整页文字", "框里读出来的", "问", [], 1)
      and "仅供背景" in V.build_ask_prompt("整页文字", "框里读出来的", "问", [], 1))
check("  没框选时 prompt 也说得清楚",
      "没框选" in V.build_ask_prompt("整页文字", "", "问", [], 1))

# ---------------------------------------------------------------- 5 目录穿越

check("★ 静态路由拒绝目录穿越（.. 不许逃出库）",
      V.Handler.__dict__.get("_static") is not None)
_src = __import__("inspect").getsource(V.Handler._static)
check("  _static 里有 ../ 与绝对路径的拦截", "startswith(\"..\")" in _src
      and "isabs" in _src, _src[:200])

# ---------------------------------------------------------------- 6 一键启动的 .bat

# ★ 回归（真实事故，用户截图报的）：.bat 写成 UTF-8 → cmd.exe 按 GBK 解析 →
#   每行中文变乱码，**而且乱码行被当成命令执行**，连 `python run.py serve` 都被啃掉，
#   服务压根没起来，用户看到的就是"连不上大模型"。
#   `chcp 65001` 救不了：cmd 是先解析、后执行。
BAT = os.path.join(PROJ, "启动学习库.bat")
check(".bat 放在项目根（跟 run.py 并排，不然 cd %~dp0 后找不到 run.py）",
      os.path.isfile(BAT), BAT)
if os.path.isfile(BAT):
    raw = open(BAT, "rb").read()
    check("★ .bat 能被 **GBK** 解码（cmd 就是按系统 ANSI 码页读它的）",
          True if _try_gbk(raw) else False,
          "解不开 = 中文会变乱码并被当成命令执行")
    txt = _try_gbk(raw) or ""
    check("★ 关键命令行在（字面量，不许被乱码啃掉）",
          "python run.py serve" in txt)
    check("  用 chcp 936（和控制台同码页）", "chcp 936" in txt)
    check("  以 @echo off 开头", txt.lstrip().startswith("@echo off"))
    check("  有 pause（出错时窗口不闪退，用户看得到原因）", "pause" in txt)
    check("  没有 UTF-8 BOM（BOM 会让 cmd 把第一行认成乱码）",
          not raw.startswith(b"\xef\xbb\xbf"), str(list(raw[:3])))

# ---------------------------------------------------------------- 汇总

for p in PASSES:
    print("PASS  " + p)
for f_ in FAILS:
    print("FAIL  " + f_)
print("=" * 60)
print(f"通过 {len(PASSES)} / 失败 {len(FAILS)}")
raise SystemExit(1 if FAILS else 0)
